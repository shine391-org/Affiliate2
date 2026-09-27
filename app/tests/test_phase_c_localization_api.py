# SPDX-License-Identifier: AGPL-3.0-only
import pytest

from backend.api import localization as api
from backend.api import tools


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
    assert api._profile_invalidation({"translation_guardrails"}) == "translation"
    assert api._profile_invalidation({"rewrite_model"}) == "dubbing"
    assert api._profile_invalidation({"rewrite_base_url"}) == "dubbing"
    assert api._profile_invalidation({"rewrite_instructions"}) == "dubbing"
    assert api._profile_invalidation({"rewrite_guardrails"}) == "dubbing"
    assert api._profile_invalidation({"audio_mode"}) == "audio_mix"
    assert api._profile_invalidation({"render_profile"}) == "final_render"
    assert api._profile_invalidation({"translation_model", "tts_model"}) == "translation"
    assert api._profile_invalidation({"asr_model", "render_profile"}) == "asr"


@pytest.mark.asyncio
async def test_translate_source_reuses_downloaded_media_without_upload(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"real-source")

    async def fake_downloaded_path(video_id):
        assert video_id == "downloaded-1"
        return source

    async def fail_upload(*args, **kwargs):
        raise AssertionError("selected downloaded source must not be re-uploaded")

    monkeypatch.setattr(tools, "_downloaded_video_path", fake_downloaded_path)
    monkeypatch.setattr(tools, "save_upload", fail_upload)

    resolved = await tools._resolve_translate_source(
        file=None,
        downloaded_video_id="downloaded-1",
        job_id="job-1",
    )

    assert resolved == source


@pytest.mark.asyncio
async def test_translate_source_requires_exactly_one_input():
    with pytest.raises(Exception, match="exactly one"):
        await tools._resolve_translate_source(file=None, downloaded_video_id=None, job_id="job-1")


@pytest.mark.asyncio
async def test_translate_source_treats_whitespace_download_id_as_absent(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"real-source")

    async def fake_downloaded_path(video_id):
        raise AssertionError("whitespace id must not resolve a download")
    monkeypatch.setattr(tools, "_downloaded_video_path", fake_downloaded_path)

    class FakeUpload:
        filename = "drop.mp4"
        async def read(self):
            return b"upload-bytes"
        def close(self):
            pass

    async def captured_upload(file, job_id, allowed_exts, max_bytes, suffix=""):
        assert file.filename == "drop.mp4"
        return source
    monkeypatch.setattr(tools, "save_upload", captured_upload)

    resolved = await tools._resolve_translate_source(
        file=FakeUpload(), downloaded_video_id="   ", job_id="job-1",
    )
    assert resolved == source


@pytest.mark.asyncio
async def test_translate_rejects_missing_download_before_creating_job(monkeypatch):
    async def missing_source(video_id):
        raise tools.HTTPException(404, "Downloaded source not found")

    monkeypatch.setattr(tools, "_downloaded_video_path", missing_source)

    with pytest.raises(tools.HTTPException, match="Downloaded source not found"):
        await tools._resolve_translate_source(
            file=None, downloaded_video_id="missing", job_id="job-1",
        )


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

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
            events.append(("invalidate", stage, delete_artifacts, set(preserve_files)))

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
        ("invalidate", "separation", True, set()),
        ("refresh_source_identity",),
        ("dispatch", "job-source-reset"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_stage", "expected_preserve"),
    [
        (None, {"checkpoint.json"}),
        ("translation", set()),
    ],
)
async def test_translation_rerun_distinguishes_auto_resume_from_explicit_recompute(
    monkeypatch, requested_stage, expected_preserve,
):
    events = []

    class FakeRun:
        job_id = "job-translation-rerun"

        def first_incomplete_stage(self):
            return "translation"

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
            events.append(("invalidate", stage, delete_artifacts, set(preserve_files)))

        def refresh_source_identity(self):
            raise AssertionError("translation rerun must not rebind source identity")

    async def fake_inactive(job_id):
        events.append(("inactive", job_id))

    async def fake_dispatch(run):
        events.append(("dispatch", run.job_id))

    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_load_run", lambda job_id: FakeRun())
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    result = await api.rerun_localization_job(
        "job-translation-rerun",
        api.RerunRequest(from_stage=requested_stage),
    )

    assert result["from_stage"] == "translation"
    assert events == [
        ("inactive", "job-translation-rerun"),
        ("invalidate", "translation", True, expected_preserve),
        ("dispatch", "job-translation-rerun"),
    ]


@pytest.mark.asyncio
async def test_rerender_updates_render_config_and_invalidates_only_final_render(monkeypatch):
    events = []

    class FakeRun:
        job_id = "job-render-config"
        request = {
            "target_language": "vi",
            "mode": "full_dub",
            "mask_box": None,
            "caption_placement": {"mode": "default", "vertical_offset": 0.0},
        }
        effective_profile = {"target_aspect": "9:16", "render_profile": "hq"}

        def update_request(self, value):
            self.request = value
            events.append(("request", value))

        def update_effective_profile(self, value):
            self.effective_profile = value
            events.append(("profile", value))

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
            events.append(("invalidate", stage, delete_artifacts, set(preserve_files)))

    async def fake_inactive(job_id):
        events.append(("inactive", job_id))

    async def fake_dispatch(run):
        events.append(("dispatch", run.job_id))

    fake_run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_load_run", lambda job_id: fake_run)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    result = await api.rerender_localization_job(
        "job-render-config",
        api.RenderRerunRequest(
            target_aspect="16:9",
            mask_box={
                "x": 0.0,
                "y": 0.72,
                "width": 1.0,
                "height": 0.22,
                "mode": "solid",
                "normalized": True,
            },
            caption_placement={"mode": "mask_region", "vertical_offset": -0.05},
        ),
    )

    assert result == {"ok": True, "job_id": "job-render-config", "from_stage": "final_render"}
    assert fake_run.request["mask_box"]["mode"] == "solid"
    assert fake_run.request["caption_placement"] == {"mode": "mask_region", "vertical_offset": -0.05}
    assert fake_run.effective_profile["target_aspect"] == "16:9"
    assert ("invalidate", "final_render", True, set()) in events
    assert not any(event[0] == "invalidate" and event[1] != "final_render" for event in events)


@pytest.mark.asyncio
async def test_rerender_rejects_non_normalized_or_invalid_mask_before_dispatch(monkeypatch):
    async def fake_inactive(job_id):
        return None

    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)

    with pytest.raises(api.HTTPException, match="normalized"):
        await api.rerender_localization_job(
            "job-render-invalid",
            api.RenderRerunRequest(
                mask_box={"x": 0, "y": 72, "width": 100, "height": 22, "mode": "blur"},
                caption_placement={"mode": "mask_region", "vertical_offset": 0.0},
            ),
        )


@pytest.mark.asyncio
async def test_rerender_partial_update_preserves_existing_render_config(monkeypatch):
    class FakeRun:
        job_id = "job-render-partial"
        request = {
            "mode": "full_dub",
            "mask_box": {
                "x": 0.05,
                "y": 0.72,
                "width": 0.9,
                "height": 0.15,
                "mode": "blur",
                "normalized": True,
            },
            "caption_placement": {"mode": "mask_region", "vertical_offset": -0.04},
        }
        effective_profile = {"target_aspect": "9:16", "render_profile": "hq"}

        def update_request(self, value):
            self.request = value

        def update_effective_profile(self, value):
            self.effective_profile = value

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
            assert stage == "final_render"

    async def fake_inactive(job_id):
        return None

    async def fake_dispatch(run):
        return None

    fake_run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_load_run", lambda job_id: fake_run)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    await api.rerender_localization_job(
        "job-render-partial",
        api.RenderRerunRequest(target_aspect="16:9"),
    )

    assert fake_run.request["mask_box"]["mode"] == "blur"
    assert fake_run.request["caption_placement"] == {
        "mode": "mask_region",
        "vertical_offset": -0.04,
    }
    assert fake_run.effective_profile["target_aspect"] == "16:9"


@pytest.mark.asyncio
async def test_rerender_explicit_null_mask_disables_mask_and_resets_placement(monkeypatch):
    class FakeRun:
        job_id = "job-render-disable-mask"
        request = {
            "mask_box": {
                "x": 0.05, "y": 0.72, "width": 0.9, "height": 0.15,
                "mode": "blur", "normalized": True,
            },
            "caption_placement": {"mode": "mask_region", "vertical_offset": -0.04},
        }
        effective_profile = {"target_aspect": "9:16"}

        def update_request(self, value):
            self.request = value

        def update_effective_profile(self, value):
            self.effective_profile = value

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
            assert stage == "final_render"

    async def fake_noop(*args, **kwargs):
        return None

    fake_run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_noop)
    monkeypatch.setattr(api, "_load_run", lambda job_id: fake_run)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_noop)

    await api.rerender_localization_job(
        fake_run.job_id,
        api.RenderRerunRequest(
            mask_box=None,
            caption_placement={"mode": "default", "vertical_offset": 0},
        ),
    )

    assert fake_run.request["mask_box"] is None
    assert fake_run.request["caption_placement"] == {
        "mode": "default",
        "vertical_offset": 0.0,
    }


@pytest.mark.asyncio
async def test_dispatch_existing_run_prefers_effective_caption_style_override(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    captured = {}

    class FakeRun:
        job_id = "job-caption-style"
        source_path = source
        request = {
            "target_language": "Vietnamese",
            "mode": "captions_only",
            "voice_id": "",
            "caption_style": "viral",
            "emoji_style": "none",
        }
        effective_profile = {"caption_style": "bold", "target_aspect": "9:16"}

        def verify_source_identity(self):
            return {}

    async def fake_restart(job_id):
        return True

    def fake_runner(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "runner-call"

    def fake_dispatch(call):
        captured["call"] = call

    monkeypatch.setattr("backend.agents.job_helper.restart_job", fake_restart)
    monkeypatch.setattr("backend.core.tool_runners.run_tool_translate", fake_runner)
    monkeypatch.setattr("backend.core.task_runner.dispatch", fake_dispatch)

    await api._dispatch_existing_run(FakeRun())

    assert captured["args"][5] == "bold"
    assert captured["kwargs"]["target_aspect"] == "9:16"
    assert captured["kwargs"]["profile_override"]["caption_style"] == "bold"
