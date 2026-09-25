"""Native Phase-F performance contract against the application SQLite owner."""
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
from sqlalchemy import select

from backend.database import AsyncSessionLocal, init_db
from backend.models.generated_video import GeneratedVideo
from backend.models.phase_g import PublicationRecord
from backend.models.video_metrics import VideoMetrics
from backend.services import performance_tracker as pt

_UID = "test-phase-f-performance"


def run(body):
    async def wrapped():
        from sqlalchemy import delete, select
        await init_db()
        async with AsyncSessionLocal() as db:
            ids = (await db.execute(select(GeneratedVideo.id).where(GeneratedVideo.user_id == _UID))).scalars().all()
            if ids:
                await db.execute(delete(VideoMetrics).where(VideoMetrics.generated_video_id.in_(ids)))
                await db.execute(delete(PublicationRecord).where(PublicationRecord.generated_video_id.in_(ids)))
            await db.execute(delete(GeneratedVideo).where(GeneratedVideo.user_id == _UID))
            await db.commit()
        try:
            await body()
        finally:
            async with AsyncSessionLocal() as db:
                ids = (await db.execute(select(GeneratedVideo.id).where(GeneratedVideo.user_id == _UID))).scalars().all()
                if ids:
                    await db.execute(delete(VideoMetrics).where(VideoMetrics.generated_video_id.in_(ids)))
                await db.execute(delete(PublicationRecord).where(PublicationRecord.generated_video_id.in_(ids)))
                await db.execute(delete(GeneratedVideo).where(GeneratedVideo.user_id == _UID))
                await db.commit()
    asyncio.run(wrapped())


async def video(**kwargs):
    values = dict(user_id=_UID, title="A real asset", status="uploaded",
                  youtube_video_id="yt-123", uploaded_platforms_json='["youtube"]',
                  created_at=datetime.utcnow())
    values.update(kwargs)
    async with AsyncSessionLocal() as db:
        row = GeneratedVideo(**values)
        db.add(row)
        await db.commit()
        return row.id


def test_normalize_missing_and_malformed_youtube_fields():
    async def body():
        from backend.config import settings
        response = MagicMock(status_code=200)
        response.json.return_value = {"items": [{"id": "yt-123", "statistics": {"viewCount": "0", "likeCount": "4"}}]}
        with patch.object(settings, "YOUTUBE_API_KEY", "test-key"), patch("httpx.get", return_value=response):
            assert await pt.fetch_youtube_metrics("yt-123") == {
                "views": 0, "likes": 4, "comments": None, "shares": None,
            }
        response.json.return_value = {"items": [{"id": "wrong", "statistics": {"viewCount": "100"}}]}
        with patch.object(settings, "YOUTUBE_API_KEY", "test-key"), patch("httpx.get", return_value=response):
            assert await pt.fetch_youtube_metrics("yt-123") is None
        response.json.return_value = {"items": [{"statistics": {"viewCount": "100"}}]}
        with patch.object(settings, "YOUTUBE_API_KEY", "test-key"), patch("httpx.get", return_value=response):
            assert await pt.fetch_youtube_metrics("yt-123") is None
        response.json.return_value = {"items": [{"id": "yt-123", "statistics": {"viewCount": "oops"}}]}
        with patch.object(settings, "YOUTUBE_API_KEY", "test-key"), patch("httpx.get", return_value=response):
            assert await pt.fetch_youtube_metrics("yt-123") is None
    run(body)


def test_native_snapshot_identity_nulls_duplicate_and_stale():
    async def body():
        from sqlalchemy import select
        vid = await video()
        other = await video(title="Other", youtube_video_id="yt-other")
        assert await pt._save_metrics(vid, "youtube", {"views": 15, "likes": None, "comments": None, "shares": None})
        assert not await pt._save_metrics(vid, "youtube", {"views": 15, "likes": None, "comments": None, "shares": None})
        detail = await pt.get_video_performance(vid)
        assert detail["latest"]["youtube"]["likes"] is None
        assert detail["latest"]["youtube"]["platform_content_id"] == "yt-123"
        assert detail["latest"]["youtube"]["state"] == "INSUFFICIENT_DATA"
        assert (await pt.get_video_performance(other))["latest"] == {}
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(VideoMetrics).where(VideoMetrics.generated_video_id == vid))).scalars().all()
            assert len(rows) == 1
            rows[0].fetched_at = datetime.utcnow() - timedelta(days=3)
            await db.commit()
        assert (await pt.get_video_performance(vid))["latest"]["youtube"]["stale"] is True
    run(body)


def test_overview_is_platform_scoped_and_preserves_unmeasured():
    async def body():
        first = await video(title="Winner")
        other = await video(title="Unmeasured", youtube_video_id="yt-other",
                            uploaded_platforms_json='["youtube", "youtube"]')
        published_at = datetime(2026, 9, 24, 8, 30, 0)
        async with AsyncSessionLocal() as db:
            publication = PublicationRecord(
                generated_video_id=first,
                platform="youtube",
                status="PUBLISHED",
                mapping_status="mapped",
                published_at=published_at,
            )
            db.add(publication)
            await db.commit()
            publication_id = publication.id
        await pt._save_metrics(first, "youtube", {"views": 120, "likes": 12})
        async with AsyncSessionLocal() as db:
            metric = (await db.execute(select(VideoMetrics).where(
                VideoMetrics.generated_video_id == first,
                VideoMetrics.platform == "youtube",
            ))).scalar_one()
            assert metric.publication_record_id == publication_id
            assert metric.source == "manual"
        summary = await pt.get_performance_overview(_UID)
        assert summary["published_posts"] == 2
        assert summary["posts_with_metrics"] == 1
        assert summary["by_platform"]["youtube"]["views"] == 120
        posts = {post["generated_video_id"]: post for post in summary["posts"]}
        assert posts[first]["latest_metrics"]["likes"] == 12
        assert posts[first]["published_at"] == published_at.isoformat()
        assert posts[first]["latest_metrics"]["engagement_rate"] is None
        assert posts[other]["latest_metrics"] is None
        assert posts[other]["state"] == "INSUFFICIENT_DATA"
    run(body)


def test_performance_api_rejects_missing_or_unpublished_and_reads_exact_asset():
    async def body():
        from backend.main import app
        vid = await video()
        await pt._save_metrics(vid, "youtube", {"views": 9})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            detail = await client.get(f"/api/videos/{vid}/performance")
            assert detail.status_code == 200
            assert detail.json()["generated_video_id"] == vid
            assert detail.json()["latest"]["youtube"]["views"] == 9
            assert (await client.get("/api/videos/missing/performance")).status_code == 404
            invalid = await client.post(f"/api/videos/{vid}/performance/metrics", json={"platform": "unknown", "views": 2})
            assert invalid.status_code == 400
    run(body)


def test_manual_api_validation_and_idempotency():
    async def body():
        from backend.main import app
        vid = await video()
        unpub = await video(status="ready", uploaded_platforms_json="[]")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            path = f"/api/videos/{vid}/performance/metrics"
            for invalid in ({"platform": "youtube", "views": -1},
                            {"platform": "youtube", "views": "42"},
                            {"platform": "youtube", "likes": 1.5},
                            {"platform": "youtube"}):
                resp = await client.post(path, json=invalid)
                assert resp.status_code in (400, 422)
            blocked = await client.post(f"/api/videos/{unpub}/performance/metrics", json={"platform": "youtube", "views": 4})
            assert blocked.status_code == 400
            first = await client.post(path, json={"platform": "youtube", "views": 0})
            again = await client.post(path, json={"platform": "youtube", "views": 0})
            assert first.status_code == 200 and first.json()["changed"] is True
            assert again.status_code == 200 and again.json()["changed"] is False
            assert first.json()["performance"]["latest"]["youtube"]["views"] == 0
            assert first.json()["performance"]["latest"]["youtube"]["likes"] is None
    run(body)


def test_sync_disconnected_and_provider_error_do_not_persist():
    async def body():
        from backend.main import app
        from backend.config import settings
        vid = await video()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch.object(settings, "YOUTUBE_API_KEY", ""):
                response = await client.post(f"/api/videos/{vid}/performance/sync")
                assert response.status_code == 409
                assert "Settings" in response.json()["detail"]
            with patch.object(settings, "YOUTUBE_API_KEY", "test-key"), patch.object(pt, "fetch_youtube_metrics", return_value=None):
                response = await client.post(f"/api/videos/{vid}/performance/sync")
                assert response.status_code == 502
            assert (await client.get(f"/api/videos/{vid}/performance")).json()["history"] == {}
    run(body)
