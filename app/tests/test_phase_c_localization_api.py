# SPDX-License-Identifier: AGPL-3.0-only
import pytest

from backend.api import localization as api


@pytest.mark.asyncio
async def test_localization_batch_coordinator_runs_jobs_sequentially(monkeypatch):
    active = 0
    peak = 0
    order = []

    async def fake_runner(**spec):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        order.append(("start", spec["job_id"]))
        # Yield control so a concurrent implementation would overlap here.
        import asyncio
        await asyncio.sleep(0)
        order.append(("end", spec["job_id"]))
        active -= 1

    monkeypatch.setattr(
        "backend.core.tool_runners.run_tool_translate",
        fake_runner,
    )
    await api._run_localization_batch_sequential([
        {"job_id": "a"},
        {"job_id": "b"},
        {"job_id": "c"},
    ])

    assert peak == 1
    assert order == [
        ("start", "a"), ("end", "a"),
        ("start", "b"), ("end", "b"),
        ("start", "c"), ("end", "c"),
    ]


def test_profile_invalidation_uses_earliest_quality_stage():
    assert api._profile_invalidation({"tts_model"}) == "dubbing"
    assert api._profile_invalidation({"tts_base_url"}) == "dubbing"
    assert api._profile_invalidation({"voice_clone_reference_mode"}) == "dubbing"
    assert api._profile_invalidation({"translation_base_url"}) == "translation"
    assert api._profile_invalidation({"audio_mode"}) == "audio_mix"
    assert api._profile_invalidation({"render_profile"}) == "final_render"
    assert api._profile_invalidation({"translation_model", "tts_model"}) == "translation"
    assert api._profile_invalidation({"asr_model", "render_profile"}) == "asr"
