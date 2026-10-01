# SPDX-License-Identifier: AGPL-3.0-only
import pytest
from types import SimpleNamespace

from backend.api import localization as api
from backend.api import tools
from backend.core.localization import LocalizationProfile


async def _fake_claim(job_id: str, **_kwargs):
    # Preserve each test's existing fake_inactive assertions/event recording.
    await api._assert_job_inactive(job_id)
    return {
        "status": "failed",
        "progress_pct": 0.0,
        "current_step": None,
        "error_message": None,
        "started_at": None,
        "completed_at": None,
        "claimed_at": None,
    }


async def _fake_restore(_job_id: str, _token: dict):
    return None


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
    assert api._profile_invalidation({"translation_base_url"}) == "semantic"
    assert api._profile_invalidation({"translation_guardrails"}) == "translation"
    assert api._profile_invalidation({"rewrite_model"}) == "dubbing"
    assert api._profile_invalidation({"rewrite_base_url"}) == "dubbing"
    assert api._profile_invalidation({"rewrite_instructions"}) == "dubbing"
    assert api._profile_invalidation({"rewrite_guardrails"}) == "dubbing"
    assert api._profile_invalidation({"audio_mode"}) == "audio_mix"
    assert api._profile_invalidation({"render_profile"}) == "final_render"
    assert api._profile_invalidation({"translation_model", "tts_model"}) == "semantic"
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

    async def fake_dispatch(run, **_kwargs):
        events.append(("dispatch", run.job_id))

    fake_run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: fake_run)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)
    monkeypatch.setattr(
        api, "_review_revision",
        lambda run, stage, artifact: "translation-revision-123456",
    )

    result = await api.continue_after_translation_review(
        "job-review",
        api.ReviewApprovalRequest(revision="translation-revision-123456"),
    )

    assert result["from_stage"] == "dubbing"
    assert fake_run.request["translation_review_approved"] is True
    assert fake_run.request["translation_review_pending"] is False
    assert events[-1] == ("dispatch", "job-review")


@pytest.mark.asyncio
async def test_rerun_from_separation_rebinds_source_identity(monkeypatch):
    events = []

    class FakeRun:
        job_id = "job-source-reset"
        request = {}

        def first_incomplete_stage(self):
            return "dubbing"

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
            events.append(("invalidate", stage, delete_artifacts, set(preserve_files)))

        def refresh_source_identity(self):
            events.append(("refresh_source_identity",))

        def update_request(self, value):
            self.request = value

    async def fake_inactive(job_id):
        events.append(("inactive", job_id))

    async def fake_dispatch(run, **_kwargs):
        events.append(("dispatch", run.job_id))

    fake_run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
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
        request = {}

        def first_incomplete_stage(self):
            return "translation"

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
            events.append(("invalidate", stage, delete_artifacts, set(preserve_files)))

        def refresh_source_identity(self):
            raise AssertionError("translation rerun must not rebind source identity")

        def update_request(self, value):
            self.request = value

    async def fake_inactive(job_id):
        events.append(("inactive", job_id))

    async def fake_dispatch(run, **_kwargs):
        events.append(("dispatch", run.job_id))

    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
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
async def test_existing_rerun_dispatches_persisted_effective_profile(monkeypatch, tmp_path):
    captured = {}
    source = tmp_path / "persisted.mp4"
    source.write_bytes(b"source")

    class FakeRun:
        job_id = "job-persisted-tts"
        source_path = source
        request = {"target_language": "Vietnamese", "mode": "full_dub"}
        effective_profile = {
            "id": "persisted",
            "name": "Persisted",
            "tts_provider": "elevenlabs",
            "tts_model": "eleven_multilingual_v2",
            "tts_voice": "old-voice",
        }

        def verify_source_identity(self):
            return None

    async def fake_restart(job_id):
        return True

    def fake_dispatch(coro):
        captured["coro"] = coro
        coro.close()

    async def completed_runner():
        return None

    def fake_runner(*args, **kwargs):
        captured["profile_override"] = kwargs["profile_override"]
        return completed_runner()

    monkeypatch.setattr("backend.agents.job_helper.restart_job", fake_restart)
    monkeypatch.setattr("backend.core.task_runner.dispatch", fake_dispatch)
    monkeypatch.setattr("backend.core.tool_runners.run_tool_translate", fake_runner)

    await api._dispatch_existing_run(FakeRun())

    assert captured["profile_override"]["tts_provider"] == "elevenlabs"
    assert captured["profile_override"]["tts_voice"] == "old-voice"


@pytest.mark.asyncio
@pytest.mark.parametrize("request_field,expected", [
    ("voice_id", "vi-VN-HoaiMyNeural"),
    ("regenerate_chunk_ids", []),
])
async def test_explicit_tts_rebind_updates_only_tts_and_invalidates_from_dubbing(
    monkeypatch, request_field, expected,
):
    events = []

    class FakeRun:
        job_id = "job-rebind-tts"
        request = {
            "target_language": "Vietnamese", "mode": "full_dub",
            "voice_id": "old-manual-voice", "regenerate_chunk_ids": [2],
        }
        effective_profile = {
            "id": "hq",
            "name": "HQ",
            "translation_model": "qwen-translation",
            "tts_provider": "elevenlabs",
            "tts_model": "eleven_multilingual_v2",
            "tts_voice": "old-voice",
            "tts_base_url": "https://api.elevenlabs.io/v1",
            "tts_region": None,
        }

        def update_effective_profile(self, value):
            self.effective_profile = value
            events.append(("profile", dict(value)))

        def update_request(self, value):
            self.request = value

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files=set()):
            events.append(("invalidate", stage, delete_artifacts, set(preserve_files)))

    async def fake_inactive(job_id):
        events.append(("inactive", job_id))

    async def fake_binding(*args, **kwargs):
        return {
            "tts_provider": "edge_tts",
            "tts_model": None,
            "tts_voice": "vi-VN-HoaiMyNeural",
            "tts_base_url": None,
            "tts_region": None,
        }

    async def fake_dispatch(run, **_kwargs):
        events.append(("dispatch", run.job_id))

    run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: run)
    monkeypatch.setattr(api, "_resolve_current_tts_binding", fake_binding)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    result = await api.rebind_localization_tts(
        run.job_id,
        api.TTSRebindRequest(source="settings"),
    )

    assert result["from_stage"] == "dubbing"
    assert run.effective_profile["tts_provider"] == "edge_tts"
    assert run.effective_profile["tts_voice"] == "vi-VN-HoaiMyNeural"
    assert run.effective_profile["translation_model"] == "qwen-translation"
    assert run.request["tts_binding_mode"] == "inherit"
    assert run.request.get(request_field) == expected
    assert ("invalidate", "dubbing", True, set()) in events
    assert not any(event[0] == "invalidate" and event[1] != "dubbing" for event in events)
    assert events[-1] == ("dispatch", run.job_id)


@pytest.mark.asyncio
async def test_tts_rebind_voice_override_still_invalidates_only_dubbing(monkeypatch):
    events = []

    class FakeRun:
        job_id = "job-rebind-voice"
        request = {"target_language": "Vietnamese", "mode": "full_dub"}
        effective_profile = {"id": "hq", "name": "HQ", "translation_model": "keep-me", "tts_provider": "edge_tts"}

        def update_effective_profile(self, value):
            self.effective_profile = value

        def update_request(self, value):
            self.request = value

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files=set()):
            events.append(stage)

    async def fake_binding(_profile, _target, *, voice_id=None, model=None):
        assert voice_id == "vi-VN-NamMinhNeural"
        return {
            "tts_provider": "edge_tts", "tts_model": model,
            "tts_voice": voice_id, "tts_base_url": None, "tts_region": None,
        }

    async def fake_dispatch(run, **_kwargs):
        return None

    async def fake_inactive(job_id):
        return None

    run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: run)
    monkeypatch.setattr(api, "_resolve_current_tts_binding", fake_binding)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    await api.rebind_localization_tts(
        run.job_id,
        api.TTSRebindRequest(source="settings", voice_id="vi-VN-NamMinhNeural"),
    )

    assert events == ["dubbing"]
    assert run.effective_profile["translation_model"] == "keep-me"
    assert run.request["tts_binding_mode"] == "inherit"


@pytest.mark.asyncio
async def test_tts_rebind_to_other_provider_drops_provider_scoped_voice_state(monkeypatch):
    events = []

    class FakeRun:
        job_id = "job-rebind-provider"
        request = {"target_language": "Vietnamese", "mode": "full_dub"}
        effective_profile = {
            "id": "hq",
            "name": "HQ",
            "translation_model": "qwen-translation",
            "asr_model": "large-v3",
            "tts_provider": "elevenlabs",
            "tts_model": "eleven_multilingual_v2",
            "tts_voice": "eleven-voice-id",
            "tts_base_url": "https://api.elevenlabs.io/v1",
            "tts_region": None,
            "speaker_voices": {"SPEAKER_00": "eleven-id"},
            "speaker_reference_audio": {"SPEAKER_00": "ref/eleven.wav"},
            "tts_reference_audio": "ref/global.wav",
            "tts_reference_text": "reference transcript",
            "tts_reference_language": "vi",
        }

        def update_effective_profile(self, value):
            self.effective_profile = value

        def update_request(self, value):
            self.request = value

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files=set()):
            events.append(stage)

    async def fake_binding(*args, **kwargs):
        return {
            "tts_provider": "edge_tts", "tts_model": None,
            "tts_voice": "vi-VN-HoaiMyNeural", "tts_base_url": None, "tts_region": None,
        }

    async def fake_dispatch(run, **_kwargs):
        return None

    async def fake_inactive(job_id):
        return None

    run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: run)
    monkeypatch.setattr(api, "_resolve_current_tts_binding", fake_binding)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    result = await api.rebind_localization_tts(
        run.job_id, api.TTSRebindRequest(source="settings"),
    )

    profile = run.effective_profile
    assert profile["tts_provider"] == "edge_tts"
    assert profile["speaker_voices"] == {}
    assert profile["speaker_reference_audio"] == {}
    assert profile["tts_reference_audio"] is None
    assert profile["tts_reference_text"] is None
    assert profile["tts_reference_language"] is None
    assert "eleven-id" not in str(profile)
    # Translation/ASR artifacts and configuration are untouched.
    assert profile["translation_model"] == "qwen-translation"
    assert profile["asr_model"] == "large-v3"
    assert run.request["tts_binding_mode"] == "inherit"
    assert events == ["dubbing"]
    assert "speaker_voices" in result["cleared_profile_fields"]


def test_provider_scoped_tts_resets_keeps_state_when_provider_is_unchanged():
    profile = {
        "tts_provider": "elevenlabs",
        "speaker_voices": {"SPEAKER_00": "eleven-id"},
    }
    assert api._provider_scoped_tts_resets(profile, {"tts_provider": "elevenlabs"}) == {}


def test_provider_scoped_tts_resets_keeps_clone_reference_for_gpt_sovits():
    resets = api._provider_scoped_tts_resets(
        {"tts_provider": "elevenlabs"}, {"tts_provider": "gpt_sovits"},
    )
    assert resets == {"speaker_voices": {}, "speaker_reference_audio": {}}


def test_rebuilt_profile_validates_after_provider_scoped_resets():
    profile = LocalizationProfile.model_validate({
        "id": "hq",
        "name": "HQ",
        "tts_provider": "edge_tts",
        "speaker_voices": {},
        "speaker_reference_audio": {},
        "tts_reference_audio": None,
        "tts_reference_text": None,
        "tts_reference_language": None,
    })
    assert profile.speaker_voices == {}
    assert profile.tts_reference_audio is None


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

        def is_complete(self, stage, artifacts=()):
            return stage in {"translation", "dubbing", "audio_mix"}

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

    async def fake_dispatch(run, **_kwargs):
        events.append(("dispatch", run.job_id))

    fake_run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
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
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)

    with pytest.raises(api.HTTPException, match="normalized"):
        await api.rerender_localization_job(
            "job-render-invalid",
            api.RenderRerunRequest(
                mask_box={"x": 0, "y": 72, "width": 100, "height": 22, "mode": "blur"},
                caption_placement={"mode": "mask_region", "vertical_offset": 0.0},
            ),
        )


@pytest.mark.asyncio
async def test_rerender_rejects_invalid_mask_mode_before_dispatch(monkeypatch):
    dispatched = False

    async def fake_inactive(job_id):
        return None

    async def fake_dispatch(run, **_kwargs):
        nonlocal dispatched
        dispatched = True

    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    with pytest.raises(api.HTTPException, match="mode") as exc:
        await api.rerender_localization_job(
            "job-render-invalid-mode",
            api.RenderRerunRequest(mask_box={
                "x": 0.05,
                "y": 0.72,
                "width": 0.9,
                "height": 0.15,
                "mode": "pixelate",
                "normalized": True,
            }),
        )

    assert exc.value.status_code == 400
    assert dispatched is False


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

        def is_complete(self, stage, artifacts=()):
            return stage in {"translation", "dubbing", "audio_mix"}

        def update_request(self, value):
            self.request = value

        def update_effective_profile(self, value):
            self.effective_profile = value

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
            assert stage == "final_render"

    async def fake_inactive(job_id):
        return None

    async def fake_dispatch(run, **_kwargs):
        return None

    fake_run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
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

        def is_complete(self, stage, artifacts=()):
            return stage in {"translation", "dubbing", "audio_mix"}

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
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
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
async def test_rerender_explicit_null_caption_placement_resets_default(monkeypatch):
    class FakeRun:
        job_id = "job-render-reset-placement"
        request = {
            "mode": "captions_only",
            "mask_box": None,
            "caption_placement": {"mode": "mask_region", "vertical_offset": -0.04},
        }
        effective_profile = {"target_aspect": "16:9"}

        def is_complete(self, stage, artifacts=()):
            return stage in {"translation", "dubbing", "audio_mix"}

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
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: fake_run)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_noop)

    await api.rerender_localization_job(
        fake_run.job_id,
        api.RenderRerunRequest(caption_placement=None),
    )

    assert fake_run.request["caption_placement"] == {
        "mode": "default",
        "vertical_offset": 0.0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "complete_stages", "review_pending", "message"),
    [
        ("full_dub", {"translation", "audio_mix"}, False, "lồng tiếng"),
        ("full_dub", {"translation", "dubbing"}, False, "âm thanh"),
        ("full_dub", {"translation", "dubbing", "audio_mix"}, True, "duyệt bản dịch"),
        ("captions_only", {"translation", "dubbing"}, False, "âm thanh"),
    ],
)
async def test_rerender_blocks_when_upstream_is_not_ready(
    monkeypatch, mode, complete_stages, review_pending, message,
):
    events = []

    class FakeRun:
        job_id = "job-render-not-ready"
        request = {
            "mode": mode,
            "translation_review_pending": review_pending,
            "translation_review_approved": not review_pending,
        }
        effective_profile = {"target_aspect": "9:16"}

        def is_complete(self, stage, artifacts=()):
            return stage in complete_stages

        def update_request(self, value):
            events.append(("request", value))

        def update_effective_profile(self, value):
            events.append(("profile", value))

        def invalidate_from(self, *args, **kwargs):
            events.append(("invalidate", args, kwargs))

    async def fake_inactive(job_id):
        return None

    async def fake_dispatch(run, **_kwargs):
        events.append(("dispatch", run.job_id))

    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: FakeRun())
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    with pytest.raises(api.HTTPException, match=message) as exc:
        await api.rerender_localization_job(
            "job-render-not-ready",
            api.RenderRerunRequest(target_aspect="16:9"),
        )

    assert exc.value.status_code == 409
    assert events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["full_dub", "captions_only"])
async def test_rerender_accepts_canonical_ready_upstream(monkeypatch, mode):
    events = []

    class FakeRun:
        job_id = "job-render-ready"
        request = {"mode": mode, "translation_review_pending": False}
        effective_profile = {"target_aspect": "9:16"}

        def is_complete(self, stage, artifacts=()):
            return stage in {"translation", "dubbing", "audio_mix"}

        def update_request(self, value):
            self.request = value

        def update_effective_profile(self, value):
            self.effective_profile = value

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
            events.append(("invalidate", stage, delete_artifacts, set(preserve_files)))

    async def fake_inactive(job_id):
        return None

    async def fake_dispatch(run, **_kwargs):
        events.append(("dispatch", run.job_id))

    fake_run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: fake_run)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    result = await api.rerender_localization_job(
        fake_run.job_id,
        api.RenderRerunRequest(target_aspect="16:9"),
    )

    assert result["from_stage"] == "final_render"
    assert events == [
        ("invalidate", "final_render", True, set()),
        ("dispatch", fake_run.job_id),
    ]


@pytest.mark.asyncio
async def test_canonical_rerender_resume_never_calls_upstream_processors(monkeypatch, tmp_path):
    from backend.core import tool_runners
    from backend.core.localization import LocalizationProfile, LocalizationSegment, LocalizationTranscript

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video")
    dub_track = tmp_path / "dub.wav"
    dub_track.write_bytes(b"dub")
    mixed_audio = tmp_path / "mixed.wav"
    mixed_audio.write_bytes(b"mixed")
    transcript = LocalizationTranscript(
        text="hello",
        language="en",
        segments=[LocalizationSegment(
            id="seg-1",
            source_text="hello",
            translated_text="xin chào",
            source_start_ms=0,
            source_end_ms=1000,
            target_start_ms=0,
            target_end_ms=1000,
            translation_status="success",
            timing_status="fit",
        )],
    )
    profile = LocalizationProfile(
        id="rerender-test",
        name="Rerender test",
        audio_mode="fast_duck",
        target_aspect="9:16",
    )
    statuses = {
        stage: "complete"
        for stage in ("separation", "asr", "semantic", "translation", "dubbing", "audio_mix")
    }
    statuses.update(final_render="invalidated", qc="invalidated")
    artifacts = {
        ("dubbing", "dub_track"): dub_track,
        ("audio_mix", "mixed_audio"): mixed_audio,
    }
    failures = []

    class FakeRun:
        job_id = "job-render-resume"
        source_path = source
        effective_profile = profile.model_dump()
        request = {
            "mode": "full_dub",
            "review_translation": False,
            "translation_review_pending": False,
        }
        manifest_path = tmp_path / "manifest.json"

        def update_request(self, value):
            self.request = value

        def update_effective_profile(self, value):
            self.effective_profile = value

        def verify_source_identity(self):
            return {}

        def is_complete(self, stage, required_artifacts=()):
            if statuses.get(stage) != "complete":
                return False
            return all(self.artifact(stage, key) is not None for key in required_artifacts)

        def artifact(self, stage, key):
            if key == "transcript" and stage in {"asr", "semantic", "translation", "dubbing"}:
                return tmp_path / f"{stage}-transcript.json"
            if key in {"plan", "chunks"} and stage == "dubbing":
                return tmp_path / f"{key}.json"
            return artifacts.get((stage, key))

        def load_json(self, stage, key):
            if key == "transcript":
                return transcript.model_dump()
            if key in {"plan", "chunks"}:
                return []
            raise KeyError((stage, key))

        def record(self, stage):
            return {"status": statuses.get(stage, "pending"), "metadata": {}}

        def start(self, stage):
            statuses[stage] = "running"

        def artifact_path(self, stage, name):
            return tmp_path / f"{stage}-{name}"

        def save_json(self, stage, name, value):
            path = self.artifact_path(stage, name)
            path.write_text("{}", encoding="utf-8")
            return path

        def complete(self, stage, *, artifacts=None, metadata=None):
            statuses[stage] = "complete"
            for key, path in (artifacts or {}).items():
                artifacts_map_key = (stage, key)
                globals_artifacts[artifacts_map_key] = path

        def fail(self, stage, error):
            statuses[stage] = "failed"
            failures.append((stage, str(error)))

    globals_artifacts = artifacts
    fake_run = FakeRun()

    async def fail_upstream(*args, **kwargs):
        raise AssertionError("rerender must not execute upstream processing")

    async def fake_generate(segments, *, output_path, **kwargs):
        output_path.write_text("[Events]\n", encoding="utf-8")
        return output_path

    async def fake_burn(video_path, ass_path, output_path, **kwargs):
        output_path.write_bytes(b"rendered-video")
        return output_path

    async def fake_noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "backend.services.localization_stage_service.LocalizationRun.load",
        lambda job_id: fake_run,
    )
    monkeypatch.setattr(tool_runners, "_load_user_settings", fake_noop)
    monkeypatch.setattr(tool_runners, "_tool_progress", fake_noop)
    monkeypatch.setattr(tool_runners, "_tool_success", fake_noop)
    monkeypatch.setattr(tool_runners, "_tool_fail", fake_noop)
    monkeypatch.setattr("backend.api.tools.tool_out_path", lambda job_id, suffix: tmp_path / f"out{suffix}")
    monkeypatch.setattr("backend.services.localization_service.transcribe_for_localization", fail_upstream)
    monkeypatch.setattr("backend.services.localization_service.prepare_semantic_transcript", fail_upstream)
    monkeypatch.setattr("backend.services.localization_service.translate_transcript", fail_upstream)
    monkeypatch.setattr("backend.services.localization_service.synthesize_timed_dub", fail_upstream)
    monkeypatch.setattr("backend.services.localization_service.mix_dub_audio", fail_upstream)
    monkeypatch.setattr("backend.services.caption_service.generate_captions_ass", fake_generate)
    monkeypatch.setattr("backend.services.caption_service.burn_captions", fake_burn)
    monkeypatch.setattr(
        "backend.services.localization_qc_service.evaluate_localization_qc",
        lambda **kwargs: SimpleNamespace(as_dict=lambda: {
            "status": "READY", "failures": [], "warnings": [],
        }),
    )
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda path: 1.0)

    await tool_runners.run_tool_translate(
        job_id=fake_run.job_id,
        in_path=source,
        target_language="Vietnamese",
        mode="full_dub",
        voice_id="",
        caption_style="viral",
        emoji_style="none",
        profile_override=profile.model_dump(),
        cleanup_input=False,
    )

    assert failures == []
    assert statuses["final_render"] == "complete"
    assert statuses["qc"] == "complete"


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


@pytest.mark.asyncio
async def test_rerun_dubbing_preserves_persisted_tts_binding(monkeypatch):
    events = []

    class FakeRun:
        job_id = "job-rerun-pinned-edge"
        request = {
            "mode": "full_dub",
            "target_language": "Vietnamese",
            "tts_binding_mode": "pinned",
            "regenerate_chunk_ids": [2],
        }
        effective_profile = {
            "translation_model": "keep-translation",
            "tts_provider": "edge_tts",
            "tts_model": None,
            "tts_voice": "vi-VN-HoaiMyNeural",
            "tts_base_url": None,
            "tts_region": None,
        }

        def first_incomplete_stage(self):
            return "dubbing"

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
            events.append(("invalidate", stage, delete_artifacts, set(preserve_files)))

        def refresh_source_identity(self):
            events.append(("refresh_source_identity",))

        def update_request(self, value):
            self.request = value

    async def fake_inactive(job_id):
        return None

    async def fail_rebind(*args, **kwargs):
        raise AssertionError("ordinary rerun must never inspect or rebind current TTS Settings")

    async def fake_dispatch(run, **_kwargs):
        events.append(("dispatch", run.job_id, run.effective_profile["tts_provider"]))

    run = FakeRun()
    original_profile = dict(run.effective_profile)
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: run)
    monkeypatch.setattr(api, "_describe_tts_bindings", fail_rebind)
    monkeypatch.setattr(api, "_resolve_current_tts_binding", fail_rebind)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    result = await api.rerun_localization_job(
        run.job_id,
        api.RerunRequest(from_stage="dubbing"),
    )

    assert result["ok"] is True
    assert result["job_id"] == run.job_id
    assert result["from_stage"] == "dubbing"
    assert result["smart_retry"] is False
    assert run.effective_profile == original_profile
    assert not run.request.get("regenerate_chunk_ids")
    assert events == [
        ("invalidate", "dubbing", True, set()),
        ("dispatch", run.job_id, "edge_tts"),
    ]


@pytest.mark.asyncio
async def test_rerun_downstream_of_dubbing_does_not_rebind_tts(monkeypatch):
    events = []

    class FakeRun:
        job_id = "job-render-only"
        request = {"mode": "full_dub", "target_language": "Vietnamese"}
        effective_profile = {"tts_provider": "elevenlabs"}

        def first_incomplete_stage(self):
            return "final_render"

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
            events.append(("invalidate", stage))

        def refresh_source_identity(self):
            raise AssertionError("render-only rerun must not refresh source")

        def update_request(self, value):
            self.request = value

    async def fake_inactive(job_id):
        return None

    async def fail_describe(run):
        raise AssertionError("render-only rerun must not inspect/rebind TTS")

    async def fake_dispatch(run, **_kwargs):
        events.append(("dispatch", run.job_id))

    run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: run)
    monkeypatch.setattr(api, "_describe_tts_bindings", fail_describe)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    result = await api.rerun_localization_job(
        run.job_id,
        api.RerunRequest(from_stage="final_render"),
    )

    assert result["ok"] is True
    assert result["job_id"] == run.job_id
    assert result["from_stage"] == "final_render"
    assert result["smart_retry"] is False
    assert events == [("invalidate", "final_render"), ("dispatch", run.job_id)]



@pytest.mark.asyncio
async def test_smart_rerun_dubbing_targets_only_overflow_chunks(monkeypatch):
    events = []

    class FakeRun:
        job_id = "job-smart-dubbing"
        request = {"mode": "full_dub"}

        def first_incomplete_stage(self):
            return "dubbing"

        def record(self, stage):
            assert stage == "dubbing"
            return {
                "metadata": {
                    "issues": [
                        {"chunk_id": 7, "reason": "timing_overflow"},
                        {"chunk_id": 9, "reason": "speed_warning"},
                        {"chunk_id": 11, "reason": "timing_overflow"},
                    ],
                },
            }

        def update_request(self, value):
            self.request = value
            events.append(("request", dict(value)))

        def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
            events.append(("invalidate", stage, delete_artifacts, set(preserve_files)))

        def refresh_source_identity(self):
            raise AssertionError("dubbing smart retry must not refresh source")

    async def fake_inactive(job_id):
        return None

    async def fake_dispatch(run, **_kwargs):
        events.append(("dispatch", run.job_id))

    run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: run)
    monkeypatch.setattr(api, "_dispatch_existing_run", fake_dispatch)

    result = await api.rerun_localization_job(
        run.job_id,
        api.RerunRequest(),
    )

    assert result["from_stage"] == "dubbing"
    assert result["smart_retry"] is True
    assert result["targeted_chunk_ids"] == [7, 11]
    assert run.request["regenerate_chunk_ids"] == [7, 11]
    assert ("invalidate", "dubbing", False, set()) in events
    assert events[-1] == ("dispatch", run.job_id)


@pytest.mark.asyncio
async def test_auto_repair_toggle_persists_on_same_job(monkeypatch):
    class FakeRun:
        job_id = "job-auto-repair"
        request = {"mode": "full_dub", "auto_repair_attempts": {"dubbing": 1}}

        def update_request(self, value):
            self.request = value

    async def fake_inactive(job_id):
        assert job_id == run.job_id

    run = FakeRun()
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: run)

    enabled = await api.set_localization_auto_repair(
        run.job_id,
        api.AutoRepairRequest(enabled=True),
    )
    assert enabled["auto_repair"] is True
    assert run.request["auto_repair"] is True
    assert enabled["attempts"] == {"dubbing": 1}

    disabled = await api.set_localization_auto_repair(
        run.job_id,
        api.AutoRepairRequest(enabled=False),
    )
    assert disabled["auto_repair"] is False
    assert run.request["auto_repair"] is False



def test_ai_route_change_invalidates_semantic_and_rewrite_route_invalidates_dubbing():
    assert api._profile_invalidation({"translation_routes"}) == "semantic"
    assert api._profile_invalidation({"translation_provider"}) == "semantic"
    assert api._profile_invalidation({"semantic_ai_total_timeout_s"}) == "semantic"
    assert api._profile_invalidation({"translation_guardrails"}) == "translation"
    assert api._profile_invalidation({"rewrite_routes"}) == "dubbing"
    assert api._profile_invalidation({"tts_connection_id"}) == "dubbing"


@pytest.mark.asyncio
async def test_current_tts_rebind_keeps_saved_connection_endpoint_live(monkeypatch):
    import backend.database as database
    from backend.services import localization_service as localization
    from backend.services import tts_service

    row = SimpleNamespace()

    class Result:
        def scalar_one_or_none(self):
            return row

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def execute(self, _query):
            return Result()

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: FakeSession())
    runtime = localization.TTSRuntimeConfig(
        provider=SimpleNamespace(value="openai_compatible_tts"),
        default_voice="vi-test",
        api_key="secret",
        model="tts-local",
        base_url="http://127.0.0.1:9001/v1",
        region="runtime-region",
        connection_id="tts-live",
    )
    monkeypatch.setattr(localization, "_tts_config", lambda *_args, **_kwargs: runtime)

    async def healthy(**kwargs):
        assert kwargs["base_url"] == "http://127.0.0.1:9001/v1"
        assert kwargs["region"] == "runtime-region"
        return {"ok": True}

    monkeypatch.setattr(tts_service, "test_tts_connection", healthy)

    binding = await api._resolve_current_tts_binding(
        {"id": "hq", "name": "HQ"},
        "Vietnamese",
    )

    assert binding["tts_connection_id"] == "tts-live"
    assert binding["tts_model"] == "tts-local"
    assert binding["tts_voice"] == "vi-test"
    assert binding["tts_base_url"] is None
    assert binding["tts_region"] is None

@pytest.mark.asyncio
async def test_continue_after_translation_rejects_stale_review_revision(monkeypatch):
    class FakeRun:
        job_id = "job-stale-review"
        request = {
            "review_translation": True,
            "translation_review_pending": True,
            "translation_review_approved": False,
        }

        def is_complete(self, stage, artifacts=()):
            return stage == "translation"

    fake_run = FakeRun()
    async def fake_inactive(job_id):
        return None
    monkeypatch.setattr(api, "_assert_job_inactive", fake_inactive)
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: fake_run)
    monkeypatch.setattr(
        api, "_review_revision",
        lambda run, stage, artifact: "current-revision-123456",
    )

    with pytest.raises(Exception, match="đã thay đổi"):
        await api.continue_after_translation_review(
            "job-stale-review",
            api.ReviewApprovalRequest(revision="stale-revision-123456"),
        )

def test_qc_review_revision_changes_when_final_video_bytes_change(tmp_path):
    report = tmp_path / "report.json"
    video = tmp_path / "final.mp4"
    report.write_text('{"status":"NEEDS_REVIEW"}', encoding="utf-8")
    video.write_bytes(b"AAAA")

    class FakeRun:
        def artifact(self, stage, key):
            if (stage, key) == ("qc", "report"):
                return report
            if (stage, key) == ("final_render", "final_video"):
                return video
            return None

    run = FakeRun()
    before = api._qc_review_revision(run)
    video.write_bytes(b"BBBB")  # same size, different bytes
    after = api._qc_review_revision(run)

    assert before
    assert after
    assert before != after

