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
    assert api._profile_invalidation({"rewrite_model"}) == "dubbing"
    assert api._profile_invalidation({"rewrite_base_url"}) == "dubbing"
    assert api._profile_invalidation({"audio_mode"}) == "audio_mix"
    assert api._profile_invalidation({"render_profile"}) == "final_render"
    assert api._profile_invalidation({"translation_model", "tts_model"}) == "translation"
    assert api._profile_invalidation({"asr_model", "render_profile"}) == "asr"



@pytest.mark.asyncio
async def test_continue_after_translation_approves_same_run(monkeypatch):
    events = []

    class FakeRun:
        job_id = "job-review"
        request = {
            "review_translation": True,
            "translation_review_pending": True,
            "translation_review_approved": False,
        }

        def is_complete(self, stage, artifacts=()):
            return stage == "translation"

        def update_request(self, value):
            self.request = value
            events.append(("update", dict(value)))

    async def fake_inactive(job_id):
        events.append(("inactive", job_id))

    async def fake_dispatch(run):
        events.append(("dispatch", run.job_id))

    fake_run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_load_run", lambda job_id: fake_run)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    result = await api.continue_after_translation_review("job-review")

    assert result["from_stage"] == "dubbing"
    assert fake_run.request["translation_review_approved"] is True
    assert fake_run.request["translation_review_pending"] is False
    assert events[-1] == ("dispatch", "job-review")


@pytest.mark.asyncio
async def test_rerun_from_separation_rebinds_source_identity(monkeypatch):
    events = []

    class FakeRun:
        job_id = "job-source-reset"

        def first_incomplete_stage(self):
            return "dubbing"

        def invalidate_from(self, stage, *, delete_artifacts):
            events.append(("invalidate", stage, delete_artifacts))

        def refresh_source_identity(self):
            events.append(("refresh_source_identity",))

    async def fake_inactive(job_id):
        events.append(("inactive", job_id))

    async def fake_dispatch(run):
        events.append(("dispatch", run.job_id))

    fake_run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_load_run", lambda job_id: fake_run)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    result = await api.rerun_localization_job(
        "job-source-reset",
        api.RerunRequest(from_stage="separation"),
    )

    assert result["from_stage"] == "separation"
    assert events == [
        ("inactive", "job-source-reset"),
        ("invalidate", "separation", True),
        ("refresh_source_identity",),
        ("dispatch", "job-source-reset"),
    ]
