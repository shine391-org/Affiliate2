# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Tests for UserIntelligence.get_pipeline_state — the proactive funnel state
that the planner uses to nudge the user's next-best step. Seeds rows under an
isolated user_id and cleans up, so it doesn't touch real data.
"""
import asyncio

from backend.core.user_intelligence import UserIntelligence

_UID = "test-pipeline-state"


async def _cleanup():
    from backend.database import AsyncSessionLocal
    from backend.models.downloaded_video import DownloadedVideo
    from backend.models.generated_video import GeneratedVideo
    from sqlalchemy import delete
    async with AsyncSessionLocal() as db:
        await db.execute(delete(DownloadedVideo).where(DownloadedVideo.user_id == _UID))
        await db.execute(delete(GeneratedVideo).where(GeneratedVideo.user_id == _UID))
        await db.commit()


def test_pipeline_state_funnel_and_next_action():
    async def go():
        from backend.database import init_db, AsyncSessionLocal
        from backend.models.downloaded_video import DownloadedVideo
        from backend.models.generated_video import GeneratedVideo
        await init_db()
        await _cleanup()
        try:
            async with AsyncSessionLocal() as db:
                # Two downloads: one analyzed, one not.
                dv_a = DownloadedVideo(user_id=_UID, insights_json='{"hook": "x"}')
                dv_b = DownloadedVideo(user_id=_UID, insights_json=None)
                db.add_all([dv_a, dv_b])
                await db.commit()
                await db.refresh(dv_a)
                # One generated video FROM dv_a, rendered but not uploaded.
                gv = GeneratedVideo(
                    user_id=_UID,
                    source_downloaded_video_id=dv_a.id,
                    video_path="/tmp/does-not-need-to-exist.mp4",
                    uploaded_platforms_json="[]",
                )
                db.add(gv)
                await db.commit()

            state = await UserIntelligence().get_pipeline_state(_UID)

            assert state["downloaded_total"] == 2
            assert state["downloaded_not_analyzed"] == 1          # dv_b
            assert state["downloaded_not_generated"] == 1         # dv_b (dv_a has a gen)
            assert state["generated_total"] == 1
            assert state["generated_not_uploaded"] == 1           # gv has a path, no uploads
            # Highest-priority nudge = turn the un-clipped download into a short.
            assert "downloaded" in (state["next_best_action"] or "").lower()
        finally:
            await _cleanup()

    asyncio.run(go())


def test_pipeline_state_empty_user_is_brand_new():
    async def go():
        from backend.database import init_db
        await init_db()
        await _cleanup()  # ensure clean
        state = await UserIntelligence().get_pipeline_state(_UID)
        assert state["downloaded_total"] == 0
        assert state["generated_total"] == 0
        # Brand-new users get the "make a first video" nudge.
        assert state["next_best_action"] is not None
        assert "brand-new" in state["next_best_action"].lower() or "first" in state["next_best_action"].lower()

    asyncio.run(go())
