# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Missing local renders are visible state; GET /api/videos is never destructive."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

MARKER = "__test_missing_artifact__"


@pytest.fixture(scope="module", autouse=True)
def _init_schema():
    from backend.database import init_db
    asyncio.run(init_db())


def _wipe_marked() -> None:
    """Delete only rows owned by this module, including canonical history."""
    from backend.database import AsyncSessionLocal
    from backend.models.generated_video import GeneratedVideo
    from backend.models.phase_g import PublicationRecord
    from backend.models.video_metrics import VideoMetrics
    from sqlalchemy import delete, select

    async def go():
        async with AsyncSessionLocal() as db:
            ids = (await db.execute(
                select(GeneratedVideo.id).where(GeneratedVideo.source_type == MARKER)
            )).scalars().all()
            if ids:
                await db.execute(delete(VideoMetrics).where(VideoMetrics.generated_video_id.in_(ids)))
                await db.execute(delete(PublicationRecord).where(PublicationRecord.generated_video_id.in_(ids)))
                await db.execute(delete(GeneratedVideo).where(GeneratedVideo.id.in_(ids)))
            await db.commit()
    asyncio.run(go())


@pytest.fixture(autouse=True)
def _only_our_rows():
    _wipe_marked()
    yield
    _wipe_marked()


async def _insert(vid: str, video_path: str | None, *, ago_seconds: int) -> str:
    from backend.database import AsyncSessionLocal
    from backend.models.generated_video import GeneratedVideo

    row_id = f"{MARKER}-{vid}"
    async with AsyncSessionLocal() as db:
        db.add(GeneratedVideo(
            id=row_id,
            user_id="local",
            title=vid,
            status="ready",
            aspect_ratio="9:16",
            video_path=video_path,
            source_type=MARKER,
            created_at=(datetime.now(timezone.utc) - timedelta(seconds=ago_seconds)).replace(tzinfo=None),
        ))
        await db.commit()
    return row_id


def _seed(tmp_path: Path, spec: list[tuple[str, bool]]) -> None:
    async def go():
        for i, (vid, exists) in enumerate(spec):
            path = tmp_path / f"{vid}.mp4"
            if exists:
                path.write_bytes(b"video")
            await _insert(vid, str(path), ago_seconds=i)
    asyncio.run(go())


def _list(**kw) -> dict:
    from backend.api.videos import list_videos
    from backend.database import AsyncSessionLocal

    params = {"status": None, "source_type": MARKER, "limit": 20, "offset": 0}
    params.update(kw)

    async def go():
        async with AsyncSessionLocal() as db:
            return await list_videos(db=db, **params)
    return asyncio.run(go())


def _ids(out: dict) -> list[str]:
    return [v["id"].removeprefix(f"{MARKER}-") for v in out["videos"]]


class TestMissingArtifactContract:
    def test_missing_files_are_returned_and_flagged_without_backfill(self, tmp_path):
        _seed(tmp_path, [("missing", False), ("live", True), ("later", True)])
        out = _list(limit=2)
        assert _ids(out) == ["missing", "live"]
        assert out["total"] == 3
        by_id = {v["id"].removeprefix(f"{MARKER}-"): v for v in out["videos"]}
        assert by_id["missing"]["artifact_missing"] is True
        assert by_id["live"]["artifact_missing"] is False

    def test_get_does_not_delete_generated_or_publication_history(self, tmp_path):
        from backend.database import AsyncSessionLocal
        from backend.models.generated_video import GeneratedVideo
        from backend.models.phase_g import PublicationRecord
        from backend.models.video_metrics import VideoMetrics
        from sqlalchemy import func, select

        path = tmp_path / "published-missing.mp4"
        video_id = asyncio.run(_insert("published", str(path), ago_seconds=0))

        async def seed_history():
            async with AsyncSessionLocal() as db:
                publication = PublicationRecord(
                    generated_video_id=video_id,
                    platform="youtube",
                    status="PUBLISHED",
                    mapping_status="mapped",
                )
                db.add(publication)
                await db.flush()
                db.add(VideoMetrics(
                    generated_video_id=video_id,
                    publication_record_id=publication.id,
                    platform="youtube",
                    views=12,
                ))
                await db.commit()
        asyncio.run(seed_history())

        out = _list(limit=10)
        row = next(v for v in out["videos"] if v["id"] == video_id)
        assert row["artifact_missing"] is True

        async def counts():
            async with AsyncSessionLocal() as db:
                generated = (await db.execute(
                    select(func.count()).select_from(GeneratedVideo).where(GeneratedVideo.id == video_id)
                )).scalar_one()
                publications = (await db.execute(
                    select(func.count()).select_from(PublicationRecord).where(
                        PublicationRecord.generated_video_id == video_id
                    )
                )).scalar_one()
                metrics = (await db.execute(
                    select(func.count()).select_from(VideoMetrics).where(
                        VideoMetrics.generated_video_id == video_id
                    )
                )).scalar_one()
                return generated, publications, metrics

        assert asyncio.run(counts()) == (1, 1, 1)

    def test_offset_and_filters_remain_plain_database_pagination(self, tmp_path):
        from backend.database import AsyncSessionLocal
        from backend.models.generated_video import GeneratedVideo
        from sqlalchemy import select

        _seed(tmp_path, [("a", False), ("b", True), ("c", True), ("d", True)])

        async def mark_draft():
            async with AsyncSessionLocal() as db:
                row = (await db.execute(select(GeneratedVideo).where(
                    GeneratedVideo.id == f"{MARKER}-c"
                ))).scalar_one()
                row.status = "draft"
                await db.commit()
        asyncio.run(mark_draft())

        out = _list(limit=2, offset=1, status="ready")
        assert _ids(out) == ["b", "d"]
        assert out["total"] == 3

    def test_rows_without_a_path_are_not_reported_missing(self):
        asyncio.run(_insert("draft", None, ago_seconds=0))
        out = _list(limit=10)
        assert _ids(out) == ["draft"]
        assert out["videos"][0]["artifact_missing"] is False
