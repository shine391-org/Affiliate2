# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Tests for backend.services.performance_tracker.

Covers post-upload metrics tracking: the poll-cadence gate
(_should_poll_now), metric persistence + history shaping
(_save_metrics / get_video_performance), aggregate summaries
(get_performance_summary), the optimal-posting-time recommender
(recommend_posting_time), the on-demand poll loop, and the
YouTube/TikTok fetch helpers.

All DB-backed rows live under an isolated user_id and are cleaned up.
Network calls (httpx) and the two fetch helpers are mocked — nothing
leaves the process.
"""
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from backend.services import performance_tracker as pt


# ── schema ────────────────────────────────────────────────────────────────
#
# The recommender reads generated videos and their metric snapshots, so these
# tests hit the database. Without this they only passed when some OTHER module
# happened to create the tables first: run alone, `sample_size` came back 0
# against three rows the test had just inserted, which reads as a product bug
# rather than as a missing table.
#
# Models are imported INSIDE the fixture on purpose - create_all only emits a
# table whose model has been imported by the time it runs.
@pytest.fixture(scope="module", autouse=True)
def _schema():
    import asyncio as _asyncio

    async def _create():
        from backend.database import Base, engine
        from backend.models.generated_video import GeneratedVideo  # noqa: F401
        from backend.models.video_metrics import VideoMetrics  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    _asyncio.run(_create())
    yield

_UID = "test-perf-tracker"


async def _cleanup():
    from backend.database import AsyncSessionLocal
    from backend.models.generated_video import GeneratedVideo
    from backend.models.phase_g import PublicationRecord
    from backend.models.video_metrics import VideoMetrics
    from sqlalchemy import delete, select

    async with AsyncSessionLocal() as db:
        # Delete metrics tied to this user's videos first (no user_id on metrics).
        vids = (await db.execute(
            select(GeneratedVideo.id).where(GeneratedVideo.user_id == _UID)
        )).scalars().all()
        if vids:
            await db.execute(
                delete(VideoMetrics).where(VideoMetrics.generated_video_id.in_(vids))
            )
            # Publication rows are keyed by video id and are NOT cascade-deleted
            # with the video. Leaving them behind means the next run sees the
            # recommender's publication query succeed against a video that no
            # longer has a metric, so the averages come out diluted.
            await db.execute(
                delete(PublicationRecord).where(
                    PublicationRecord.generated_video_id.in_(vids)
                )
            )
        await db.execute(delete(GeneratedVideo).where(GeneratedVideo.user_id == _UID))
        await db.commit()


def _run(coro_factory):
    async def go():
        from backend.database import init_db
        await init_db()
        await _cleanup()
        try:
            await coro_factory()
        finally:
            await _cleanup()
    asyncio.run(go())


async def _add_uploaded_video(db, *, published_at=None, **overrides):
    """Create an uploaded video, plus the publication record the recommender
    requires.

    Since ea46089 the recommender refuses to substitute ``video.created_at``
    for a publication timestamp - created_at is the RENDER time, so using it
    as the posting hour would report a posting-time insight built on the wrong
    clock. A test that seeds only the video is therefore measuring nothing:
    every row is skipped and sample_size comes back 0.
    """
    from backend.models.generated_video import GeneratedVideo
    from backend.models.phase_g import PublicationRecord

    kwargs = dict(
        user_id=_UID,
        status="uploaded",
        title="A video",
        uploaded_platforms_json=json.dumps(["youtube"]),
        created_at=datetime.utcnow(),
    )
    kwargs.update(overrides)
    gv = GeneratedVideo(**kwargs)
    db.add(gv)
    await db.flush()
    if published_at is not None:
        db.add(PublicationRecord(
            generated_video_id=gv.id, platform="youtube",
            status="PUBLISHED", published_at=published_at,
        ))
    return gv


# ── _should_poll_now (pure cadence logic) ─────────────────────────────────────

def test_should_poll_now_cadence_branches():
    from backend.models.generated_video import GeneratedVideo
    now = datetime.utcnow()
    v = GeneratedVideo(created_at=now, updated_at=now)
    assert pt._should_poll_now(v) is True  # no metric snapshot yet
    assert pt._should_poll_now(v, now - timedelta(hours=1)) is False
    assert pt._should_poll_now(v, now - timedelta(hours=3)) is True
    v.created_at = now - timedelta(days=3)
    assert pt._should_poll_now(v, now - timedelta(hours=3)) is False
    assert pt._should_poll_now(v, now - timedelta(hours=7)) is True
    v.created_at = now - timedelta(days=10)
    assert pt._should_poll_now(v, now - timedelta(hours=12)) is False
    assert pt._should_poll_now(v, now - timedelta(days=2)) is True
    v.created_at = now - timedelta(days=40)
    assert pt._should_poll_now(v) is False


# ── _save_metrics + get_video_performance ─────────────────────────────────────

def test_save_metrics_and_video_performance_history():
    async def body():
        from backend.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            gv = await _add_uploaded_video(db, uploaded_platforms_json='["youtube", "tiktok"]')
            vid = gv.id
            await db.commit()

        await pt._save_metrics(vid, "youtube", {"views": 100, "likes": 10, "comments": 2, "shares": 0})
        await pt._save_metrics(vid, "youtube", {"views": 250, "likes": 25, "comments": 4, "shares": 0})
        await pt._save_metrics(vid, "tiktok", {"views": 500, "likes": 60})

        perf = await pt.get_video_performance(vid)
        assert perf["generated_video_id"] == vid
        assert set(perf["history"].keys()) == {"youtube", "tiktok"}
        assert len(perf["history"]["youtube"]) == 2
        # "latest" reflects the newest snapshot per platform.
        assert perf["latest"]["youtube"]["views"] == 250
        assert perf["latest"]["tiktok"]["views"] == 500

    _run(body)


# ── get_performance_summary ───────────────────────────────────────────────────

def test_performance_summary_empty():
    async def body():
        summary = await pt.get_performance_summary(_UID)
        assert summary == {"total_views": 0, "total_likes": 0,
                           "total_videos": 0, "best_video": None}

    _run(body)


def test_performance_summary_totals_and_best_video():
    async def body():
        from backend.database import AsyncSessionLocal
        from backend.models.video_metrics import VideoMetrics

        async with AsyncSessionLocal() as db:
            low = await _add_uploaded_video(db, title="low")
            high = await _add_uploaded_video(db, title="high")
            db.add(VideoMetrics(generated_video_id=low.id, platform="youtube",
                                views=100, likes=10, fetched_at=datetime.utcnow()))
            db.add(VideoMetrics(generated_video_id=high.id, platform="youtube",
                                views=900, likes=90, fetched_at=datetime.utcnow()))
            high_id = high.id
            await db.commit()

        summary = await pt.get_performance_summary(_UID)
        assert summary["total_videos"] == 2
        assert summary["total_views"] == 1000
        assert summary["total_likes"] == 100
        assert summary["best_video"]["id"] == high_id
        assert summary["best_video"]["views"] == 900

    _run(body)


# ── recommend_posting_time ────────────────────────────────────────────────────

def test_recommend_posting_time_no_videos():
    async def body():
        rec = await pt.recommend_posting_time(_UID, platform="youtube")
        assert rec["recommendation"] is None
        assert rec["sample_size"] == 0
        assert "No uploaded videos" in rec["message"]

    _run(body)


def test_recommend_posting_time_too_few_platform_videos():
    async def body():
        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            # Two videos, only one on youtube → below the 3-video threshold.
            await _add_uploaded_video(db, uploaded_platforms_json=json.dumps(["youtube"]))
            await _add_uploaded_video(db, uploaded_platforms_json=json.dumps(["tiktok"]))
            await db.commit()

        rec = await pt.recommend_posting_time(_UID, platform="youtube")
        assert rec["recommendation"] is None
        assert rec["sample_size"] == 1
        assert "at least 3" in rec["message"]

    _run(body)


def test_recommend_posting_time_metrics_but_no_early_velocity():
    async def body():
        from backend.database import AsyncSessionLocal
        from backend.models.video_metrics import VideoMetrics

        # 3 youtube videos, but every first-metric snapshot has 0 views, so
        # they're all skipped as having no early velocity. Exercises the
        # metric-gathering loop, then the "not enough performance data" return
        # (without reaching the buggy day_breakdown builder — see note below).
        async with AsyncSessionLocal() as db:
            for i in range(3):
                published = datetime.utcnow()
                gv = await _add_uploaded_video(
                    db, uploaded_platforms_json=json.dumps(["youtube"]),
                    published_at=published)
                # A snapshot taken AT posting time establishes no velocity: the
                # recommender only counts 1h..24h windows. The rows are still
                # published and measured, they just cannot be compared.
                db.add(VideoMetrics(generated_video_id=gv.id, platform="youtube",
                                    views=0, fetched_at=published))
            await db.commit()

        rec = await pt.recommend_posting_time(_UID, platform="youtube")
        assert rec["recommendation"] is None
        # `sample_size` counts QUALIFIED videos - those with a snapshot in the
        # 1h..24h window. A snapshot taken at posting time establishes no
        # velocity, so none of these three qualify, which is exactly the
        # condition this test exists to pin.
        assert rec["sample_size"] == 0
        assert "Need at least 3 verified publication timestamps" in rec["message"]

    _run(body)


def test_recommend_posting_time_happy_path_builds_breakdowns():
    """The path that used to crash outright.

    `day_breakdown` was built by rounding over `day_performance`, whose values
    are LISTS of view counts — `round([...], 1)` raises TypeError. Any
    successful recommendation populates day_performance, so this took out the
    entire happy path; it now uses the pre-computed `day_avgs`.
    """
    async def body():
        from backend.database import AsyncSessionLocal
        from backend.models.video_metrics import VideoMetrics

        # 3 youtube uploads with real early velocity, on two distinct weekdays
        # so day_breakdown has more than one entry to sort.
        async with AsyncSessionLocal() as db:
            for created, views in [
                (datetime(2026, 7, 20, 14, 0), 900),   # Monday
                (datetime(2026, 7, 20, 14, 0), 1100),  # Monday
                (datetime(2026, 7, 22, 9, 0), 300),    # Wednesday
            ]:
                gv = await _add_uploaded_video(
                    db, created_at=created, published_at=created,
                    uploaded_platforms_json=json.dumps(["youtube"]))
                db.add(VideoMetrics(generated_video_id=gv.id, platform="youtube",
                                    views=views,
                                    fetched_at=created + timedelta(hours=2)))
            await db.commit()

        rec = await pt.recommend_posting_time(_UID, platform="youtube")

        assert rec["recommendation"] is not None
        assert rec["sample_size"] == 3
        # Monday averages 1000 vs Wednesday's 300 → Monday wins.
        assert rec["recommendation"]["day"] == "monday"
        assert rec["recommendation"]["hour_utc"] == 14

        # Every breakdown value must be a rounded NUMBER, not a list.
        day_bd = rec["day_breakdown"]
        # day_breakdown is observed VIEWS PER HOUR, not a total or a mean of the
    # raw view counts: each video contributes views/elapsed_hours, so Monday is
        # (900/2 + 1100/2) / 2 = 500.0 and Wednesday is 300/2 = 150.0.
        assert day_bd == {"monday": 500.0, "wednesday": 150.0}
        assert all(isinstance(v, (int, float)) for v in day_bd.values())
        # ...and ordered Monday-first, not alphabetically or by insertion.
        assert list(day_bd) == ["monday", "wednesday"]
        assert all(isinstance(v, (int, float)) for v in rec["hour_breakdown"].values())

    _run(body)


def test_weekday_index_orders_monday_first_and_tolerates_unknown():
    # Day keys come from strftime("%A").lower(), so a non-English locale can
    # produce a name outside the tuple — it must sort last, not raise.
    assert pt._weekday_index("monday") == 0
    assert pt._weekday_index("sunday") == 6
    assert pt._weekday_index("lundi") == 7


# ── poll_all_uploaded_videos ──────────────────────────────────────────────────

def test_poll_all_uploaded_videos_fetches_and_saves():
    async def body():
        from backend.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            gv = await _add_uploaded_video(
                db, youtube_video_id="yt-abc",
                uploaded_platforms_json=json.dumps(["youtube"]),
                created_at=datetime.utcnow(), updated_at=None)
            vid = gv.id
            await db.commit()

        fake_metrics = {"views": 4242, "likes": 42, "comments": 4, "shares": 0}
        # _save_metrics is mocked so the poll loop (which scans ALL uploaded
        # videos, not just this user's) never writes rows for real videos.
        save_mock = AsyncMock()
        with patch.object(pt, "fetch_youtube_metrics",
                          new=AsyncMock(return_value=fake_metrics)), \
             patch.object(pt, "fetch_tiktok_metrics",
                          new=AsyncMock(return_value=None)), \
             patch.object(pt, "_save_metrics", new=save_mock):
            await pt.poll_all_uploaded_videos()

        # Our seeded video was fetched + persisted with the fetched metrics.
        calls = [c.args for c in save_mock.call_args_list]
        assert (vid, "youtube", fake_metrics) in calls

    _run(body)


# ── fetch helpers ─────────────────────────────────────────────────────────────

def test_fetch_youtube_metrics_no_key_returns_none():
    async def body():
        from backend.config import settings
        with patch.object(settings, "YOUTUBE_API_KEY", ""):
            assert await pt.fetch_youtube_metrics("abc") is None

    _run(body)


def test_fetch_youtube_metrics_parses_api_response():
    async def body():
        from backend.config import settings

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "items": [{"id": "abc", "statistics": {
                "viewCount": "1500", "likeCount": "120", "commentCount": "8"}}]
        }
        with patch.object(settings, "YOUTUBE_API_KEY", "fake-key"), \
             patch("httpx.get", return_value=fake_resp):
            metrics = await pt.fetch_youtube_metrics("abc")

        assert metrics == {"views": 1500, "likes": 120, "comments": 8, "shares": None}

    _run(body)


def test_fetch_tiktok_metrics_no_token_returns_none():
    async def body():
        # Mock the settings lookup to a row with no token → None, no network.
        # (Uses a mock session so we never mutate the real "local" settings row.)
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)

        class FakeCM:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *a):
                return False

        with patch.object(pt, "AsyncSessionLocal", return_value=FakeCM()):
            assert await pt.fetch_tiktok_metrics("pub-123") is None

    _run(body)
