# SPDX-License-Identifier: AGPL-3.0-only
"""Voice gender, source facts and retries on the canonical C7/job contracts."""
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from backend.core.localization import LocalizationProfile, LocalizationProfileStore, fast_localization_profile
from backend.services import alignment_service as alignment
from backend.services import localization_qc_service as qc
from backend.services import localization_service as svc
from backend.services.localization_stage_service import LocalizationRun
from backend.services.tts_service import TTSProvider
from tests.test_localization_measured_speakers import transcript, mock_dubbing


def source_voices(tx, genders):
    tx.metadata["alignment"] = {"diarization_applied": True, "speaker_count": len(genders),
        "speaker_genders": {f"SPEAKER_{i:02d}": {"gender": gender, "confirmed": False, "source": "acoustic_pitch"}
                            for i, gender in enumerate(genders)}}
    return tx


@pytest.mark.parametrize("frequency,expected", [(120, "male"), (240, "female"), (180, "unknown")])
def test_pitch_is_a_hint_and_ambiguous_short_and_overlap_audio_remain_unverified(frequency, expected):
    audio = .3 * np.sin(2 * np.pi * frequency * np.arange(48000) / 16000)
    rows = [{"start": 0, "end": 3, "speaker": "A"}]
    fact = alignment._source_voice_hints(audio, rows)["A"]
    assert fact["gender"] == expected and fact["confirmed"] is False
    assert alignment._source_voice_hints(audio[:8000], [{"start": 0, "end": .5, "speaker": "A"}])["A"]["gender"] == "unknown"
    overlapping = [*rows, {"start": 0, "end": 3, "speaker": "B"}]
    assert all(fact["gender"] == "unknown" for fact in alignment._source_voice_hints(audio, overlapping).values())
    assert alignment._source_voice_hints(np.zeros(48000), rows)["A"]["gender"] == "unknown"


@pytest.mark.asyncio
async def test_source_gender_reverses_lexical_voice_assignment_and_survives_resume_and_targeted_regeneration(monkeypatch, tmp_path):
    events = mock_dubbing(monkeypatch)
    tx = source_voices(transcript(), ["male", "female"])
    profile = fast_localization_profile()
    args = dict(source_media=tmp_path / "source.mp4", target_language="Vietnamese", profile=profile,
                user_settings=None, work_dir=tmp_path / "job/dubbing", job_state_path=tmp_path / "job/dubbing_state.json",
                reuse_existing_chunks=True)
    first = await svc.synthesize_timed_dub(tx, **args)
    assert [event[2] for event in events] == ["vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"]
    assert first.transcript.metadata["speakers"]["resolved_voice_genders"] == {"SPEAKER_00": ["male"], "SPEAKER_01": ["female"]}
    await svc.synthesize_timed_dub(tx, **args)
    assert len(events) == 2
    await svc.synthesize_timed_dub(tx, regenerate_chunk_ids={2}, **args)
    assert len(events) == 3 and events[-1][2] == "vi-VN-HoaiMyNeural"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [TTSProvider.EDGE_TTS, TTSProvider.AZURE])
@pytest.mark.parametrize("gender,extra", [("male", "en-US-AndrewMultilingualNeural"), ("female", "en-US-AvaMultilingualNeural")])
async def test_same_gender_speakers_get_distinct_matching_advertised_voices(monkeypatch, tmp_path, gender, extra, provider):
    events = mock_dubbing(monkeypatch)
    monkeypatch.setattr(svc, "_tts_config", lambda *_: svc.TTSRuntimeConfig(provider, "vi-VN-HoaiMyNeural", "test-key", None, None, "eastasia"))
    async def catalogue(*_args, **_kwargs):
        return [{"voice_id": extra, "category": gender, "locale": "en-US"},
                {"voice_id": "en-US-UnrelatedNeural", "category": gender, "locale": "en-US"}]
    monkeypatch.setattr("backend.services.tts_service.list_voices", catalogue)
    profile = fast_localization_profile().model_copy(update={"speaker_genders": {"SPEAKER_00": gender, "SPEAKER_01": gender}})
    result = await svc.synthesize_timed_dub(source_voices(transcript(), [gender, gender]), source_media=tmp_path / "s.mp4",
        target_language="Vietnamese", profile=profile, user_settings=None, work_dir=tmp_path / "dub")
    assert len({event[2] for event in events}) == 2 and events[1][2] == extra
    assert all(values == [gender] for values in result.transcript.metadata["speakers"]["resolved_voice_genders"].values())
    assert "en-US-UnrelatedNeural" not in result.transcript.metadata["speakers"]["available_voices"]


@pytest.mark.asyncio
async def test_insufficient_matching_gender_voices_fail_before_paid_synthesis(monkeypatch, tmp_path):
    events = mock_dubbing(monkeypatch)
    async def catalogue(*_args, **_kwargs): return []
    monkeypatch.setattr("backend.services.tts_service.list_voices", catalogue)
    tx = source_voices(transcript(), ["male", "male"])
    with pytest.raises(ValueError, match="matching-gender"):
        await svc.synthesize_timed_dub(tx, source_media=tmp_path / "s.mp4", target_language="Vietnamese",
            profile=fast_localization_profile(), user_settings=None, work_dir=tmp_path / "dub")
    assert events == []


@pytest.mark.parametrize("source,confirmed,outputs,code", [
    ("male", True, ["female"], "speaker_gender_mismatch"),
    ("male", False, ["male"], "speaker_gender_unverified"),
    ("unknown", False, ["male"], "speaker_gender_unverified"),
    ("male", True, ["unknown"], "speaker_gender_unverified"),
    ("male", True, ["male", "female"], "speaker_gender_mismatch"),
])
def test_qc_gender_guard_checks_all_actual_voices_including_segment_overrides(monkeypatch, tmp_path, source, confirmed, outputs, code):
    monkeypatch.setattr(qc, "probe_media", lambda *_: dict(width=1080, height=1920, duration=4., has_audio=True, has_video=True))
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda *_: -1.)
    tx = transcript()
    tx.metadata["alignment"] = {"diarization_applied": True, "speaker_count": 2}
    tx.metadata["speakers"] = {
        "speaker_voice_identities": {"SPEAKER_00": ["A"], "SPEAKER_01": ["B"]},
        "speaker_genders": {speaker: {"gender": source, "confirmed": confirmed} for speaker in ["SPEAKER_00", "SPEAKER_01"]},
        "resolved_voice_genders": {"SPEAKER_00": outputs, "SPEAKER_01": ["male"]}}
    report = qc.evaluate_localization_qc(source_video=tmp_path / "s.mp4", final_video=tmp_path / "f.mp4", transcript=tx,
        profile=LocalizationProfile(id="p", name="P", speaker_voice_mode="shared"))
    assert report.status == "NEEDS_REVIEW"
    assert any(warning["code"] == code for warning in report.warnings)


def test_tts_changes_reset_mapping_before_first_c7_but_keep_source_facts_and_asr_resets_source(monkeypatch, tmp_path):
    from backend.config import settings
    monkeypatch.setattr(type(settings), "STORAGE_ROOT", property(lambda _: tmp_path))
    profile = fast_localization_profile().model_dump()
    profile.update(speaker_voices={"SPEAKER_00": "old-voice"}, speaker_genders={"SPEAKER_00": "male"},
                   speaker_reference_audio={"SPEAKER_00": "old.wav"}, voice_genders={"old-voice": "male"})
    run = LocalizationRun("j", source_path=str(tmp_path / "s.mp4"), effective_profile=profile)
    run.update_effective_profile({**profile, "tts_model": "changed"})
    assert run.effective_profile["speaker_voices"] == run.effective_profile["speaker_reference_audio"] == run.effective_profile["voice_genders"] == {}
    assert run.effective_profile["speaker_genders"] == {"SPEAKER_00": "male"}
    run.invalidate_from("asr")
    assert run.effective_profile["speaker_genders"] == {}
    assert LocalizationRun.load("j").effective_profile["speaker_genders"] == {}
    reusable = LocalizationProfileStore(default_profile_id="fast", profiles=[fast_localization_profile().model_copy(update={"speaker_genders": {"SPEAKER_00": "male", "narrator": "female"}})])
    assert reusable.profiles[0].speaker_genders == {"narrator": "female"}


@pytest.mark.asyncio
async def test_resume_after_accepted_rewrite_failure_keeps_c6_immutable_and_does_not_pay_for_another_rewrite(monkeypatch, tmp_path):
    tx = transcript(("Xin chào tất cả mọi người",), speakers=False)
    original = tx.model_dump()
    profile = fast_localization_profile()
    args = dict(source_media=tmp_path / "s.mp4", target_language="Vietnamese", profile=profile,
                user_settings=None, work_dir=tmp_path / "dub", reuse_existing_chunks=True)
    mock_dubbing(monkeypatch, durations=[1800], fail_after=1)
    async def shorten(*_args, **_kwargs): return '[{"id":"s0","text":"Xin chào"}]'
    monkeypatch.setattr(svc, "_call_ai_chat_resilient", shorten)
    with pytest.raises(RuntimeError, match="provider failed"):
        await svc.synthesize_timed_dub(tx, **args)
    events = mock_dubbing(monkeypatch)
    async def forbidden(*_args, **_kwargs): pytest.fail("Accepted measured rewrite must survive retry")
    monkeypatch.setattr(svc, "_call_ai_chat_resilient", forbidden)
    result = await svc.synthesize_timed_dub(tx, **args)
    assert events[0][1] == "Xin chào" and result.rewrite_count == 1
    assert tx.model_dump() == original
    audit = json.loads((tmp_path / "dub/rewrite_audit.json").read_text())
    assert len(audit["entries"]) == 1 and audit["entries"][0]["duration_before_ms"] == 1800


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [(TimeoutError("network timeout"), 3), (ValueError("invalid voice"), 1)])
async def test_canonical_edge_retries_have_one_owner_and_deterministic_failures_stop(monkeypatch, tmp_path, error, expected):
    calls = []
    class Communicate:
        def __init__(self, *args, **kwargs):
            calls.append(kwargs)
        async def save(self, path): raise error
    monkeypatch.setitem(sys.modules, "edge_tts", SimpleNamespace(Communicate=Communicate))
    with pytest.raises(Exception):
        await svc._generate_tts_chunk("Xin chào?", runtime=svc.TTSRuntimeConfig(TTSProvider.EDGE_TTS, "vi-VN-HoaiMyNeural", "", None, None, None),
            voice="vi-VN-HoaiMyNeural", profile=fast_localization_profile(), speaker_id=None,
            target_language="Vietnamese", output_path=tmp_path / "audio.wav")
    assert len(calls) == expected and all(call["pitch"] == "+0Hz" for call in calls)


@pytest.mark.asyncio
async def test_azure_catalogue_uses_bound_endpoint_and_multilingual_speech_sets_vietnamese(monkeypatch, tmp_path):
    from backend.services import azure_tts_service as azure
    from backend.services import tts_service
    requests = []
    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass
        async def get(self, url, **kwargs):
            requests.append(("get", url, kwargs))
            return SimpleNamespace(status_code=200, json=lambda: [{"ShortName": "en-US-AvaMultilingualNeural", "Gender": "Female", "Locale": "en-US"}])
        async def post(self, url, **kwargs):
            requests.append(("post", url, kwargs))
            return SimpleNamespace(status_code=200, content=b"speech", text="")
    monkeypatch.setattr(azure.httpx, "AsyncClient", Client)
    rows = await tts_service.list_voices(TTSProvider.AZURE, api_key="test-key", region="eastasia", base_url="https://bound.example/prefix/cognitiveservices/v1")
    assert rows[0]["gender"] == "Female"
    assert requests[0][1] == "https://bound.example/prefix/cognitiveservices/voices/list"
    await tts_service.generate_tts("Ngô Tiểu Đồng 18 tuổi?", provider=TTSProvider.AZURE, api_key="test-key",
        voice_id="en-US-AvaMultilingualNeural", region="eastasia", base_url="https://bound.example/prefix",
        output_path=tmp_path / "audio.wav", target_language="Vietnamese")
    assert requests[1][1] == "https://bound.example/prefix/cognitiveservices/v1"
    assert "<lang xml:lang='vi-VN'>Ngô Tiểu Đồng 18 tuổi?</lang>" in requests[1][2]["content"].decode()
    assert not tts_service.retryable_tts_error(RuntimeError("Azure TTS HTTP 401"))
    assert tts_service.retryable_tts_error(RuntimeError("Azure TTS HTTP 429"))


@pytest.mark.asyncio
async def test_azure_voice_discovery_preserves_saved_region_and_form_override(monkeypatch):
    from backend.api import settings as settings_api
    from backend.services import tts_service
    seen = []
    async def saved(): return SimpleNamespace()
    async def key(_body): return "test-key"
    async def voices(provider, api_key, *, base_url=None, region=None):
        seen.append((provider, api_key, base_url, region))
        return [{"voice_id": "vi-VN-NamMinhNeural", "gender": "Male"}]
    monkeypatch.setattr(settings_api, "_load_saved_settings", saved)
    monkeypatch.setattr(settings_api, "_tts_probe_connection", lambda *_args: {"provider": "azure_tts", "region": "eastasia"})
    monkeypatch.setattr(settings_api, "_tts_probe_key", key)
    monkeypatch.setattr(tts_service, "list_voices", voices)
    for region, expected in [(None, "eastasia"), ("southeastasia", "southeastasia")]:
        result = await settings_api.discover_tts_voices(settings_api.TTSProbeRequest(connection_id="azure-bound", region=region))
        assert result["voices"][0]["gender"] == "Male"
        assert seen[-1] == (TTSProvider.AZURE, "test-key", None, expected)
