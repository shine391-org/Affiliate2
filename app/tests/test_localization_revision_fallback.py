"""Current-revision audit and non-blocking voice degradation through C7/QC."""
import json
import pytest
from backend.core.localization import fast_localization_profile
from backend.services import localization_service as svc, localization_qc_service as qc
from backend.services.dubbing_job_state import DubbingJobState
from backend.services.localization_stage_service import LocalizationRun
from tests.test_localization_measured_speakers import transcript, mock_dubbing


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["one_male", "no_male", "catalogue_error"])
async def test_voice_degradation_renders_persists_and_reuses_chunks(monkeypatch, tmp_path, case):
    events = mock_dubbing(monkeypatch)
    async def catalogue(*_args, **_kwargs):
        if case == "catalogue_error": raise TimeoutError("catalogue unavailable")
        return [{"voice_id": "vi-VN-NamMinhNeural", "gender": "male"}] if case == "one_male" else [
            {"voice_id": "vi-VN-HoaiMyNeural", "gender": "female"}]
    monkeypatch.setattr("backend.services.tts_service.list_voices", catalogue)
    tx = transcript()
    tx.metadata["alignment"] = {"diarization_applied": True, "speaker_count": 2}
    profile = fast_localization_profile().model_copy(update={"speaker_genders": {"SPEAKER_00": "male", "SPEAKER_01": "male"}})
    args = dict(source_media=tmp_path / "s.mp4", target_language="Vietnamese", profile=profile,
                user_settings=None, work_dir=tmp_path / "dub", reuse_existing_chunks=True)
    result = await svc.synthesize_timed_dub(tx, **args)
    expected = "vi-VN-HoaiMyNeural" if case == "no_male" else "vi-VN-NamMinhNeural"
    assert [event[2] for event in events] == [expected, expected] and result.dub_track.exists()
    warnings = result.transcript.metadata["speakers"]["voice_warnings"]
    codes = {warning["code"] for warning in warnings}
    assert ({"speaker_voice_default_fallback", "speaker_gender_mismatch"} if case == "no_male"
            else {"speaker_voice_reused"}) <= codes
    if case == "catalogue_error": assert "speaker_voice_catalog_unavailable" in codes
    fields = {"code", "speaker_id", "source_gender", "selected_voice", "selected_voice_gender", "provider", "reason", "requires_human_review"}
    assert all(set(warning) == fields and warning["requires_human_review"] for warning in warnings)
    assert json.loads((tmp_path / "dub/dubbing_state.json").read_text())["voice_warnings"] == warnings
    rerun = await svc.synthesize_timed_dub(tx, **args)
    assert len(events) == 2 and rerun.transcript.metadata["speakers"]["voice_warnings"] == warnings
    monkeypatch.setattr(qc, "probe_media", lambda *_: dict(width=1080, height=1920, duration=12., has_audio=True, has_video=True))
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda *_: -1.)
    report = qc.evaluate_localization_qc(source_video=tmp_path / "s.mp4", final_video=result.dub_track,
        transcript=result.transcript, profile=profile, timing_issues=result.issues)
    assert report.status == "NEEDS_REVIEW" and all(warning in report.warnings for warning in warnings)
    # A per-segment correction replaces the fallback; do not report a voice
    # which was never synthesized for that speaker.
    if case == "no_male":
        tx.segments[0].overrides["voice_id"] = "custom-male"
        corrected_profile = profile.model_copy(update={"voice_genders": {"custom-male": "male"}})
        corrected = await svc.synthesize_timed_dub(tx, **{**args, "profile": corrected_profile})
        assert not [w for w in corrected.transcript.metadata["speakers"]["voice_warnings"] if w["speaker_id"] == "SPEAKER_00"]
        assert corrected.transcript.metadata["speakers"]["resolved_voices"]["SPEAKER_00"] == {"custom-male": "male"}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["translation", "tts_model", "speaker_voices", "timing"])
async def test_audit_counts_only_current_input_and_never_revives_old_revision(monkeypatch, tmp_path, change):
    tx = transcript(("Xin chào tất cả mọi người",))
    profile = fast_localization_profile()
    args = dict(source_media=tmp_path / "s.mp4", target_language="Vietnamese", user_settings=None,
                work_dir=tmp_path / "dub", reuse_existing_chunks=True)
    mock_dubbing(monkeypatch, durations=[1800])
    async def shorten(*_args, **_kwargs): return '[{"id":"s0","text":"Xin chào"}]'
    monkeypatch.setattr(svc, "_call_ai_chat_resilient", shorten)
    first = await svc.synthesize_timed_dub(tx, profile=profile, **args)
    old_key = first.transcript.metadata["dubbing"]["acceptance_key"]
    assert first.rewrite_count == 1
    changed = tx.model_copy(deep=True)
    next_profile = profile
    if change == "translation": changed.segments[0].translated_text = "Chào mọi người"
    elif change == "timing": changed.segments[0].overrides["target_end_ms"] = 1100
    else: next_profile = profile.model_copy(update={change: "new-model" if change == "tts_model" else {"SPEAKER_00": "vi-VN-NamMinhNeural"}})
    mock_dubbing(monkeypatch)
    second = await svc.synthesize_timed_dub(changed, profile=next_profile, **args)
    audit = json.loads((tmp_path / "dub/rewrite_audit.json").read_text())
    assert second.rewrite_count == second.transcript.metadata["dubbing"]["rewrite_count"] == audit["rewrite_count"] == 0
    assert audit["acceptance_key"] != old_key and audit["entries"][0]["acceptance_key"] == old_key
    same = await svc.synthesize_timed_dub(changed, profile=next_profile, **args)
    assert same.transcript.metadata["dubbing"]["acceptance_key"] == audit["acceptance_key"]
    restored = await svc.synthesize_timed_dub(tx, profile=profile, **args)
    assert restored.rewrite_count == 0 and restored.transcript.metadata["dubbing"]["acceptance_key"] != old_key


@pytest.mark.parametrize("change", ["translation", "speaker_voices", "tts_model"])
def test_invalidation_immediately_zeros_durable_count_without_erasing_history(monkeypatch, tmp_path, change):
    from backend.config import settings
    monkeypatch.setattr(type(settings), "STORAGE_ROOT", property(lambda _: tmp_path))
    profile = fast_localization_profile().model_dump()
    run = LocalizationRun("j", source_path=str(tmp_path / "s.mp4"), effective_profile=profile)
    state = DubbingJobState(run.root / "dubbing_state.json", scope="j", binding={})
    old_key = state.activate_revision("same-input")
    state.data["rewrite_audit"].append(dict(segment_id="s0", before="Long text", after="Text", acceptance_key=old_key))
    state.save()
    audit_path = run.save_json("dubbing", "rewrite_audit.json", state.audit_payload())
    run.complete("dubbing", artifacts={"rewrite_audit": audit_path}, metadata={"rewrite_count": 1})
    if change == "translation": run.invalidate_from("translation")
    else: run.update_effective_profile({**profile, change: "new" if change == "tts_model" else {"SPEAKER_00": "new"}})
    audit = json.loads(audit_path.read_text())
    assert audit["rewrite_count"] == 0 and audit["acceptance_key"] is None
    assert audit["entries"][0]["acceptance_key"] == old_key
    state = DubbingJobState(state.path, scope="j", binding={})
    assert state.activate_revision("same-input") != old_key and state.rewrite_count == 0


@pytest.mark.asyncio
async def test_end_only_override_keeps_valid_word_onset(monkeypatch, tmp_path):
    mock_dubbing(monkeypatch, durations=[400])
    tx = transcript(("Xin chào",), speakers=False)
    tx.segments[0].overrides = {"target_end_ms": 1400}
    tx.segments[0].source_words = [{"word": "bad", "start": "invalid", "end": .9},
                                  {"word": "chào", "start": .5, "end": .8}]
    result = await svc.synthesize_timed_dub(tx, source_media=tmp_path / "s.mp4", target_language="Vietnamese",
        profile=fast_localization_profile(), user_settings=None, work_dir=tmp_path / "dub")
    assert result.plan[0].original_start_ms == result.plan[0].target_start_ms == 500
    assert result.chunks[0].end_ms == 1400
    assert result.plan[0].end_locked and not result.plan[0].start_locked


def test_individual_edge_locks_allow_only_the_other_edge_to_borrow():
    from backend.services import dubbing_planner as dp
    cfg = dp.DubbingConfig()
    for edge in ["start", "end"]:
        plan, chunks = dp.build_plan([dp.DubbingCue(index=1, segment_id="s", start_ms=1000,
            end_ms=2000, text="Hello", **{edge + "_locked": True})], cfg)
        chunks[0].actual_duration_ms = 1400
        dp.allocate_silence(plan, chunks, cfg, timeline_end_ms=4000)
        if edge == "start": assert chunks[0].start_ms == 1000 and chunks[0].end_ms > 2000
        else: assert chunks[0].end_ms == 2000 and chunks[0].start_ms < 1000


@pytest.mark.parametrize("bad", [{}, {"word": "bad", "start": None, "end": 1},
    {"word": "bad", "start": .1, "end": float("nan")}, {"word": "bad", "start": 9, "end": 10},
    {"word": "bad", "start": .1, "end": .1}, {"word": "", "start": .1, "end": .2}])
def test_bad_word_does_not_discard_later_or_earlier_valid_words(bad):
    seg = transcript(("Xin chào",), speakers=False).segments[0]
    good = {"word": "chào", "start": .5, "end": .8}
    for words in [[bad, good], [good, bad]]:
        seg.source_words = words
        assert svc._speech_onset_ms(seg) == 500


@pytest.mark.asyncio
async def test_c04_current_replay_migrates_without_rewriting_or_regenerating_audio(monkeypatch, tmp_path):
    tx = transcript(("Xin chào tất cả mọi người",), speakers=False)
    args = dict(source_media=tmp_path / "s.mp4", target_language="Vietnamese", user_settings=None,
                profile=fast_localization_profile(), work_dir=tmp_path / "dub", reuse_existing_chunks=True)
    events = mock_dubbing(monkeypatch, durations=[1800])
    async def shorten(*_args, **_kwargs): return '[{"id":"s0","text":"Xin chào"}]'
    monkeypatch.setattr(svc, "_call_ai_chat_resilient", shorten)
    await svc.synthesize_timed_dub(tx, **args)
    path = tmp_path / "dub/dubbing_state.json"
    state = json.loads(path.read_text())
    state["acceptance_key"] = state.pop("acceptance_input_key")
    for entry in state["rewrite_audit"]: entry.pop("acceptance_key")
    state["rewrite_audit"].insert(0, dict(segment_id="old", before="Old text", after="Text"))
    path.write_text(json.dumps(state))
    async def forbidden(*_args, **_kwargs): pytest.fail("Do not pay for a rewrite already accepted at these inputs")
    monkeypatch.setattr(svc, "_call_ai_chat_resilient", forbidden)
    result = await svc.synthesize_timed_dub(tx, **args)
    assert len(events) == 2 and result.rewrite_count == 1
    audit = json.loads((tmp_path / "dub/rewrite_audit.json").read_text())
    assert audit["entries"][0]["acceptance_key"] == "legacy-unscoped"
    assert audit["entries"][1]["acceptance_key"] == audit["acceptance_key"]
