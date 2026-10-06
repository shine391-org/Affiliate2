"""Regression checks through C6, C7, persisted job state and QC."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

@pytest.fixture(autouse=True)
def mocked_postfit_measurement(monkeypatch):
    """Unit orchestration uses byte placeholders, NOT playable audio.

    Real measurement/failure gates run unmocked in postfit/rhythm suites.
    """
    from backend.services import localization_service as service

    async def measure(_path, raw_duration_ms, speed_factor):
        return round(raw_duration_ms / speed_factor), -12.0

    monkeypatch.setattr(service, "_measure_fitted_audio", measure)


from backend.core.localization import (
    LocalizationProfile, LocalizationProfileStore, LocalizationSegment, LocalizationTranscript,
    fast_localization_profile,
)
from backend.services import alignment_service as alignment
from backend.services import localization_qc_service as qc
from backend.services import localization_service as service
from backend.services.dubbing_job_state import DubbingJobState, tts_binding, write_json_atomic
from backend.services.dubbing_rewrite_guard import check_rewrite_meaning, check_translation_meaning
from backend.services.localization_stage_service import LocalizationRun, ManifestTransaction
from backend.services.semantic_segmentation import normalize_semantic_cues
from backend.services.tts_service import TTSProvider


def transcript(texts=("Xin chào", "Cảm ơn"), *, speakers=True):
    return LocalizationTranscript(text="Hello Thank you", language="en", segments=[
        LocalizationSegment(id=f"s{i}", source_text="Hello", translated_text=text,
                            source_start_ms=i * 3000, source_end_ms=i * 3000 + 1000,
                            target_start_ms=i * 3000, target_end_ms=i * 3000 + 1000,
                            speaker_id=f"SPEAKER_{i:02d}" if speakers else None,
                            translation_status="success", qc_status="pass",
                            overrides={"target_start_ms": i * 3000, "target_end_ms": i * 3000 + 1000})
        for i, text in enumerate(texts)
    ])


def mock_dubbing(monkeypatch, *, durations=None, fail_after=None):
    events = []
    async def catalogue(*_args, **_kwargs):
        return [{"voice_id": "vi-VN-NamMinhNeural", "gender": "male"},
                {"voice_id": "vi-VN-HoaiMyNeural", "gender": "female"}]
    monkeypatch.setattr("backend.services.tts_service.list_voices", catalogue)
    async def generate(text, *, output_path, voice, **kwargs):
        events.append(("tts", text, voice))
        if fail_after is not None and len(events) > fail_after:
            raise RuntimeError("provider failed")
        output_path.write_bytes(b"audio")
        return durations.pop(0) if durations else 800
    def compose(chunks, *, output_path, **kwargs):
        output_path.write_bytes(b"track")
        return output_path
    monkeypatch.setattr(service, "_generate_tts_chunk", generate)
    monkeypatch.setattr(service, "_compose_chunk_track_sync", compose)
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *args: 12.0)
    monkeypatch.setattr(service, "_fit_chunk_audio_sync", lambda src, dst, speed: dst)
    class AI:
        connection_name = "test"
        model = "test"
        provider = SimpleNamespace(value="test")
        async def chat(self, **kwargs):
            raise AssertionError("Unexpected AI rewrite")
    monkeypatch.setattr(service, "_profile_ai_route_clients_compat", lambda *args: [AI()])
    return events


@pytest.mark.asyncio
async def test_wrong_estimates_never_authorize_a_rewrite(monkeypatch, tmp_path):
    events = mock_dubbing(monkeypatch)
    monkeypatch.setattr(service, "estimate_spoken_ms", lambda *args: 10000)
    tx = transcript(("Xin chào", "Cảm ơn", "Hẹn gặp lại", "Chào buổi sáng"), speakers=False)
    result = await service.synthesize_timed_dub(tx, source_media=tmp_path / "source.mp4",
        target_language="Vietnamese", profile=fast_localization_profile(), user_settings=None, work_dir=tmp_path / "dub")
    assert len(events) == 4
    assert result.rewrite_count == 0
    assert result.transcript.metadata["dubbing"]["rewrite_count"] == 0
    state = json.loads((tmp_path / "dub/dubbing_state.json").read_text())
    assert all(m["estimated_ms"] == 10000 and m["actual_ms"] == 800 for m in state["measurements"].values())
    assert next(iter(result.transcript.metadata["dubbing"]["rate_calibration"].values()))["samples"] == 4


@pytest.mark.asyncio
async def test_multiple_accepted_rewrites_count_a_segment_once_and_measure_matching_text(monkeypatch, tmp_path):
    events = mock_dubbing(monkeypatch, durations=[1800, 1400, 800])
    candidates = iter(("Xin chào mọi người xem video này", "Xin chào"))
    async def chat(*args, **kwargs):
        assert events[-1][0] == "tts"
        return json.dumps([{"id": "s0", "text": next(candidates)}])
    monkeypatch.setattr(service, "_call_ai_chat_resilient", chat)
    tx = transcript(("Xin chào tất cả mọi người đang xem video này",), speakers=False)
    result = await service.synthesize_timed_dub(tx, source_media=tmp_path / "source.mp4",
        target_language="Vietnamese", profile=LocalizationProfile(id="p", name="P", video_retime_mode="off"),
        user_settings=None, work_dir=tmp_path / "dub")
    audit = json.loads((tmp_path / "dub/rewrite_audit.json").read_text())
    assert result.rewrite_count == result.transcript.metadata["dubbing"]["rewrite_count"] == audit["rewrite_count"] == 1
    assert len(audit["entries"]) == 2
    assert all(entry["required_speed_before"] > entry["dub_speed_max"] for entry in audit["entries"])
    state = json.loads((tmp_path / "dub/dubbing_state.json").read_text())
    assert {m["text"]: m["actual_ms"] for m in state["measurements"].values()} == {
        text: duration for (_, text, _), duration in zip(events, (1800, 1400, 800))}


@pytest.mark.asyncio
async def test_audit_survives_failure_after_an_accepted_rewrite(monkeypatch, tmp_path):
    mock_dubbing(monkeypatch, durations=[1800], fail_after=1)
    async def chat(*args, **kwargs):
        return '[{"id":"s0","text":"Xin chào"}]'
    monkeypatch.setattr(service, "_call_ai_chat_resilient", chat)
    with pytest.raises(RuntimeError, match="provider failed"):
        await service.synthesize_timed_dub(transcript(("Xin chào tất cả mọi người",), speakers=False),
            source_media=tmp_path / "source.mp4", target_language="Vietnamese",
            profile=fast_localization_profile(), user_settings=None, work_dir=tmp_path / "dub")
    audit = json.loads((tmp_path / "dub/rewrite_audit.json").read_text())
    assert audit["rewrite_count"] == 1
    assert audit["entries"][0]["duration_after_ms"] is None


@pytest.mark.asyncio
async def test_localized_name_remains_protected_without_c7_transliteration(monkeypatch, tmp_path):
    events = mock_dubbing(monkeypatch, durations=[1800])
    async def chat(*args, stage, **kwargs):
        return '[{"id":"s0","text":"Xin chào"}]'
    monkeypatch.setattr(service, "_call_ai_chat_resilient", chat)
    localized = transcript(("Xin chào tất cả mọi người, Trương Tam",), speakers=False)
    localized.metadata["localized_terms"] = {"张三": "Trương Tam"}
    with pytest.raises(service.LocalizationTimingError) as failed:
        await service.synthesize_timed_dub(localized,
            source_media=tmp_path / "source.mp4", target_language="Vietnamese",
            profile=fast_localization_profile(),
            user_settings=None, work_dir=tmp_path / "dub")
    assert any(issue["reason"] == "rewrite_meaning_guard" for issue in failed.value.all_issues)
    assert len(events) == 1 and "Trương Tam" in events[0][1]
    assert json.loads((tmp_path / "dub/rewrite_audit.json").read_text())["rewrite_count"] == 0


@pytest.mark.asyncio
async def test_distinct_voices_resume_targeted_regeneration_and_job_isolation(monkeypatch, tmp_path):
    events = mock_dubbing(monkeypatch)
    profile = fast_localization_profile()
    directory = tmp_path / "job/dubbing"
    state_path = tmp_path / "job/dubbing_state.json"
    kwargs = dict(source_media=tmp_path / "source.mp4", target_language="Vietnamese", profile=profile,
                  user_settings=None, work_dir=directory, job_state_path=state_path, reuse_existing_chunks=True)
    first = await service.synthesize_timed_dub(transcript(), **kwargs)
    assert [event[2] for event in events] == ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]
    initial_state = json.loads(state_path.read_text())
    assert len(initial_state["measurements"]) == len(first.transcript.metadata["dubbing"]["rate_calibration"]) == 2
    await service.synthesize_timed_dub(first.transcript, **kwargs)
    assert len(events) == 2  # Both measured raw chunks reused.
    await service.synthesize_timed_dub(first.transcript, regenerate_chunk_ids={2}, **kwargs)
    assert len(events) == 3 and events[-1][2] == "vi-VN-NamMinhNeural"
    assert json.loads(state_path.read_text())["speaker_voices"] == initial_state["speaker_voices"]
    changed = {**kwargs, "profile": profile.model_copy(update={"tts_model": "different"})}
    await service.synthesize_timed_dub(first.transcript, **changed)
    assert len(events) == 5
    assert not set(initial_state["measurements"]) & set(json.loads(state_path.read_text())["measurements"])
    # Copying the transcript metadata to another job cannot copy calibration.
    isolated = DubbingJobState(tmp_path / "other.json", scope="other", binding=initial_state["binding"])
    assert isolated.calibration() == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("same_reference", [True, False])
async def test_clone_reference_copies_cannot_fake_distinct_speaker_voices(monkeypatch, tmp_path, same_reference):
    mock_dubbing(monkeypatch, durations=[1000, 1000])
    runtime = service.TTSRuntimeConfig(TTSProvider.GPT_SOVITS, "", "", None, "http://localhost:9880", None)
    monkeypatch.setattr(service, "_tts_config", lambda *args: runtime)
    first, second = tmp_path / "one.wav", tmp_path / "two.wav"
    first.write_bytes(b"reference one")
    second.write_bytes(first.read_bytes() if same_reference else b"reference two")
    profile = LocalizationProfile(id="p", name="P", tts_provider="gpt_sovits", tts_base_url="http://localhost:9880",
        voice_clone_reference_mode="manual", diarization_mode="off", speaker_reference_audio={
            "SPEAKER_00": str(first), "SPEAKER_01": str(second)})
    result = await service.synthesize_timed_dub(transcript(("Hello", "Thank you")), source_media=tmp_path / "source.mp4",
        target_language="English", profile=profile, user_settings=None, work_dir=tmp_path / "dub")
    media = dict(width=1080, height=1920, duration=4.0, has_audio=True, has_video=True)
    monkeypatch.setattr(qc, "probe_media", lambda path: media)
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda path: -1.0)
    report = qc.evaluate_localization_qc(source_video=tmp_path / "s.mp4", final_video=tmp_path / "f.mp4",
        transcript=result.transcript, profile=profile)
    assert report.metrics["resolved_voice_count"] == (1 if same_reference else 2)
    assert report.status == "NEEDS_REVIEW"  # Clone gender has not been verified.
    assert any(item["code"] == "speaker_voice_mismatch" for item in report.warnings) == same_reference


def test_effective_voice_reference_and_connection_are_part_of_identity(tmp_path):
    runtime = service.TTSRuntimeConfig(TTSProvider.EDGE_TTS, "voice", "", None, None, None, connection_id="one")
    kwargs = dict(text="Hello", runtime=runtime, voice="voice", target_language="English", speaker_id="A",
                  reference_audio=None, reference_text=None, reference_language=None)
    original = service._chunk_synthesis_identity(**kwargs)
    runtime.connection_id = "two"
    assert service._chunk_synthesis_identity(**kwargs) != original
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"first reference")
    kwargs["reference_audio"] = str(ref)
    original = service._chunk_synthesis_identity(**kwargs)
    ref.write_bytes(b"replacement reference")
    assert service._chunk_synthesis_identity(**kwargs) != original


def test_same_binding_rerun_keeps_state_and_declared_audit_and_binding_change_clears_it(monkeypatch, tmp_path):
    from backend.config import settings
    monkeypatch.setattr(type(settings), "STORAGE_ROOT", property(lambda self: tmp_path))
    profile = fast_localization_profile().model_dump()
    run = LocalizationRun("j", source_path=str(tmp_path / "source.mp4"), effective_profile=profile)
    runtime = service.TTSRuntimeConfig(TTSProvider.EDGE_TTS, "voice", "", None, None, None)
    state = DubbingJobState(run.root / "dubbing_state.json", scope="j", binding=tts_binding(runtime, "vi"))
    state.data["speaker_voices"] = {"SPEAKER_00": "voice"}
    state.observe("sample", "voice", "Hello", 1000, 1000)
    audit = run.save_json("dubbing", "rewrite_audit.json", state.audit_payload())
    run.complete("dubbing", artifacts={"rewrite_audit": audit})
    run.invalidate_from("dubbing", delete_artifacts=True)
    assert run.artifact("dubbing", "rewrite_audit") is not None
    assert json.loads(state.path.read_text())["speaker_voices"] == {"SPEAKER_00": "voice"}
    with pytest.raises(RuntimeError):
        with ManifestTransaction(run, ("dubbing",)):
            run.update_effective_profile({**profile, "tts_model": "changed"})
            raise RuntimeError("rollback")
    assert json.loads(state.path.read_text())["measurements"]
    run.update_effective_profile({**profile, "tts_model": "changed"})
    invalidated = json.loads(state.path.read_text())
    assert invalidated["speaker_voices"] == invalidated["measurements"] == {}


def test_detected_labels_are_not_persisted_in_reusable_profiles():
    profile = LocalizationProfile(id="p", name="P", speaker_voices={"SPEAKER_00": "voice", "narrator": "other"})
    store = LocalizationProfileStore(default_profile_id="p", profiles=[profile])
    assert store.profiles[0].speaker_voices == {"narrator": "other"}
    assert profile.speaker_voices["SPEAKER_00"] == "voice"  # Job snapshot remains intact.


def test_legacy_audit_is_preserved_when_upgrading_job_state(tmp_path):
    audit_path = tmp_path / "rewrite_audit.json"
    entries = [{"segment_id": "s1", "before": "A long sentence", "after": "A sentence"},
               {"segment_id": "s1", "before": "A sentence", "after": "Sentence"}]
    write_json_atomic(audit_path, {"rewrite_count": 2, "entries": entries})
    state = DubbingJobState(tmp_path / "dubbing_state.json", scope="one", binding={}, audit_path=audit_path)
    assert state.data["rewrite_audit"] == [{**entry, "acceptance_key": "legacy-unscoped"} for entry in entries]
    assert state.rewrite_count == 0
    assert state.calibration() == {}


@pytest.mark.parametrize("failure", [False, True])
def test_fast_diarization_only_preserves_words_and_surfaces_failure(monkeypatch, failure):
    tx = transcript(("Hello there",), speakers=False)
    tx.segments[0].source_words = [{"word":"Hello", "start":0.0, "end":0.4, "score":0.9},
                                 {"word":" there", "start":0.6, "end":1.0, "score":0.8}]
    def pipeline(audio, **kwargs):
        if failure:
            raise RuntimeError("diarization inference failed")
        return [{"start":0.0, "end":0.5, "speaker":"SPEAKER_00"},
                {"start":0.5, "end":1.0, "speaker":"SPEAKER_01"}]
    whisperx = SimpleNamespace(load_audio=lambda path: "audio", DiarizationPipeline=object())
    monkeypatch.setitem(sys.modules, "whisperx", whisperx)
    monkeypatch.setattr(alignment, "_ensure_torch_checkpoint_compat", lambda: None)
    monkeypatch.setattr(alignment, "_device", lambda: "cpu")
    monkeypatch.setattr(alignment, "_make_diarization_pipeline", lambda *args, **kwargs: pipeline)
    result, report = alignment._align_sync(None, tx, fast_localization_profile(), huggingface_token="test")
    words = result.segments[0].source_words
    assert [{k:v for k,v in word.items() if not k.startswith("speaker")} for word in words] == tx.segments[0].source_words
    assert result.segments[0].id == tx.segments[0].id
    assert report.diarization_applied is not failure
    if failure:
        assert report.diarization_reason.startswith("inference_failed")
    else:
        normalized = normalize_semantic_cues(result)
        assert [segment.speaker_id for segment in normalized.segments] == ["SPEAKER_00", "SPEAKER_01"]
        assert [word for segment in normalized.segments for word in segment.source_words] == words


def test_speaker_turn_with_zero_length_asr_word_preserves_all_original_words():
    tx = transcript(("Hello there",), speakers=False)
    tx.segments[0].source_words = [{"word": "Hello", "start": 0.0, "end": 0.5, "speaker_id": "A"},
                                  {"word": " there", "start": 0.8, "end": 0.8, "speaker_id": "B"}]
    normalized = normalize_semantic_cues(tx)
    assert [word for seg in normalized.segments for word in seg.source_words] == tx.segments[0].source_words


@pytest.mark.parametrize("before,after", [("Giá tăng", "Giá giảm"), ("Giá giảm", "Giá tăng"),
                                          ("Tôi muốn", "Tôi không muốn"), ("Tôi không muốn", "Tôi muốn")])
def test_bidirectional_phrase_aware_polarity_guard(before, after):
    assert check_rewrite_meaning(before, after, "Vietnamese")


@pytest.mark.parametrize("source,target,source_language", [
    ("Not free", "Miễn phí", "en"), ("这不是免费的", "Miễn phí", "zh"),
    ("Use 5kg", "Dùng 5", "en"), ("Use 5kg", "Dùng 6kg", "en"),
    ("Increase the price", "Giảm giá", "en"),
    ("Use the protected lens", "Dùng ống kính", "en"),
])
def test_translation_invariant_guard(source, target, source_language):
    assert check_translation_meaning(source, target, source_language, "Vietnamese",
                                     glossary={"protected lens": "ống kính được bảo vệ"})


def test_glossary_and_numbers_units_survive_faithful_translation():
    assert not check_translation_meaning("Do not use 5kg of protected material",
        "Không dùng 5kg vật liệu được bảo vệ", "en", "vi", glossary={"protected material":"vật liệu được bảo vệ"})
    assert not check_rewrite_meaning("Đây là bố cục này", "Bố cục này", "vi")
    assert check_rewrite_meaning("Dùng 1.5kg", "Dùng 15kg", "vi")
    assert not check_translation_meaning("Which one?", "Cái nào?", "en", "vi", glossary={})


@pytest.mark.parametrize("source,target,language", [
    ("用5千克", "Dùng 5kg", "zh"), ("走10千米", "Đi 10km", "zh"),
    ("支付$5", "Trả 5 USD", "zh"), ("温度20°C", "Nhiệt độ 20 độ C", "zh"),
])
def test_longest_unit_alias_and_numeric_binding_survive_translation(source, target, language):
    assert not check_translation_meaning(source, target, language, "vi", glossary={})


@pytest.mark.parametrize("before,after", [
    ("Dùng 5kg và 10m", "Dùng 5m và 10kg"), ("Góc 5°", "Góc 5"),
    ("Nặng 5 lb", "Nặng 5"),
])
def test_unit_loss_and_swapped_numeric_unit_binding_are_rejected(before, after):
    assert check_rewrite_meaning(before, after, "vi")


@pytest.mark.asyncio
async def test_c6_rejects_semantic_corruption_through_canonical_retry(monkeypatch):
    class AI:
        async def chat(self, **kwargs):
            return '[{"id":"s0","text":"Miễn phí"}]'
    batch = [LocalizationSegment(id="s0", source_text="Not free", source_start_ms=0, source_end_ms=1000)]
    texts, failed = await service._translate_batch_resilient(AI(), batch, source_language="en",
        target_language="Vietnamese", context="Not free", profile=fast_localization_profile(), for_dub=True)
    assert texts == ["Not free"] and failed == {0}


@pytest.mark.parametrize("kind", ["distinct", "one_voice", "shared", "failure", "overlap", "colliding_overrides", "empty_identity"])
def test_qc_cannot_clean_unverified_multi_speaker_output(monkeypatch, tmp_path, kind):
    media = dict(width=1080, height=1920, duration=4.0, has_audio=True, has_video=True)
    monkeypatch.setattr(qc, "probe_media", lambda path: media)
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda path: -1.0)
    tx = transcript()
    tx.metadata["alignment"] = {"diarization_applied": kind != "failure", "speaker_count": 2,
        "diarization_reason": "inference_failed" if kind == "failure" else None,
        "overlap_intervals": [{"start_ms":0,"end_ms":300,"speaker_ids":["SPEAKER_00","SPEAKER_01"]}] if kind == "overlap" else []}
    second = "a" if kind in {"one_voice", "shared"} else "b"
    tx.metadata["speakers"] = {"speaker_voices":{"SPEAKER_00":"a","SPEAKER_01":second},
        "speaker_voice_identities":{"SPEAKER_00":["a"],"SPEAKER_01":[second]}}
    tx.metadata["speakers"].update(
        speaker_genders={speaker: {"gender": "male", "confirmed": True, "source": "reviewer"}
                         for speaker in ("SPEAKER_00", "SPEAKER_01")},
        resolved_voice_genders={"SPEAKER_00": ["male"], "SPEAKER_01": ["male"]})
    if kind == "colliding_overrides":
        tx.metadata["speakers"]["speaker_voice_identities"]["SPEAKER_01"] = ["a", "b"]
    elif kind == "empty_identity":
        tx.metadata["speakers"]["speaker_voice_identities"]["SPEAKER_01"] = []
    profile = LocalizationProfile(id="p", name="P", speaker_voice_mode="shared" if kind == "shared" else "distinct")
    report = qc.evaluate_localization_qc(source_video=tmp_path / "s.mp4", final_video=tmp_path / "f.mp4",
        transcript=tx, profile=profile)
    assert report.metrics["speaker_count"] == 2
    assert report.status == ("READY" if kind in {"distinct", "shared"} else "NEEDS_REVIEW")
