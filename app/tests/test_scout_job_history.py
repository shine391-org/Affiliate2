"""Scout job filtering follows immutable observation history, not mutable identity metadata."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from sqlalchemy import delete

from backend.api.scout import get_scout_results
from backend.database import AsyncSessionLocal, init_db
from backend.models.phase_g import RadarObservation
from backend.models.job import Job
from backend.models.scout_result import ScoutResult


PREFIX = "test-scout-job-history"


def _run(coro):
    return asyncio.run(coro)


async def _cleanup():
    async with AsyncSessionLocal() as db:
        ids = [
            f"{PREFIX}-shared",
            f"{PREFIX}-other",
        ]
        await db.execute(delete(RadarObservation).where(RadarObservation.scout_result_id.in_(ids)))
        await db.execute(delete(ScoutResult).where(ScoutResult.id.in_(ids)))
        await db.execute(delete(Job).where(Job.id.in_(["job-old", "job-new", "job-other", "job-youtube-old"])))
        await db.commit()


def test_old_job_membership_survives_identity_refresh():
    async def body():
        await init_db()
        await _cleanup()
        try:
            async with AsyncSessionLocal() as db:
                shared = ScoutResult(
                    id=f"{PREFIX}-shared",
                    user_id="local",
                    job_id="job-new",
                    platform="douyin",
                    video_id=f"{PREFIX}-video",
                    video_url="https://example.invalid/shared",
                    title="Shared",
                    views=250,
                    created_at=datetime.utcnow(),
                )
                other = ScoutResult(
                    id=f"{PREFIX}-other",
                    user_id="local",
                    job_id="job-other",
                    platform="douyin",
                    video_id=f"{PREFIX}-other-video",
                    video_url="https://example.invalid/other",
                    title="Other",
                    views=1,
                    created_at=datetime.utcnow(),
                )
                db.add_all([shared, other])
                await db.flush()
                db.add_all([
                    RadarObservation(
                        scout_result_id=shared.id,
                        job_id="job-old",
                        captured_at=datetime.utcnow(),
                        views=100,
                    ),
                    RadarObservation(
                        scout_result_id=shared.id,
                        job_id="job-new",
                        captured_at=datetime.utcnow(),
                        views=250,
                    ),
                    RadarObservation(
                        scout_result_id=other.id,
                        job_id="job-other",
                        captured_at=datetime.utcnow(),
                        views=1,
                    ),
                ])
                await db.commit()

            old = await get_scout_results(job_id="job-old", limit=50, offset=0)
            assert old["total"] == 1
            assert [row["id"] for row in old["results"]] == [f"{PREFIX}-shared"]
            assert old["results"][0]["views"] == 250

            current = await get_scout_results(job_id="job-new", limit=50, offset=0)
            assert current["total"] == 1
            assert current["results"][0]["id"] == f"{PREFIX}-shared"
        finally:
            await _cleanup()

    _run(body())


def test_job_output_preserves_membership_for_non_radar_platforms():
    async def body():
        await init_db()
        await _cleanup()
        try:
            video_id = f"{PREFIX}-shared"
            async with AsyncSessionLocal() as db:
                db.add(ScoutResult(
                    id=video_id,
                    user_id="local",
                    job_id="job-new",
                    platform="youtube",
                    video_id=f"{PREFIX}-youtube-video",
                    video_url="https://example.invalid/youtube",
                    title="YouTube shared",
                    views=999,
                    created_at=datetime.utcnow(),
                ))
                db.add(Job(
                    id="job-youtube-old",
                    user_id="local",
                    job_type="scout",
                    status="success",
                    input_json=json.dumps({"niche": "old", "platforms": ["youtube"]}),
                    output_json=json.dumps({
                        "total_results": 1,
                        "new_results": 1,
                        "scout_result_ids": [video_id],
                    }),
                ))
                await db.commit()

            old = await get_scout_results(job_id="job-youtube-old", limit=50, offset=0)
            assert old["total"] == 1
            assert [row["id"] for row in old["results"]] == [video_id]
            assert old["results"][0]["platform"] == "youtube"
        finally:
            await _cleanup()

    _run(body())
