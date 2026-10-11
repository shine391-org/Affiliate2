"""Transport-only recovery: mocked provider, real PCM/FFmpeg and cache receipts."""
import array
import asyncio
import math
import wave

import pytest
from aiohttp import ClientConnectionResetError
from edge_tts.exceptions import NoAudioReceived

from backend.core.exceptions import JobCancelledError, VoiceGenerationError
from backend.core.localization import LocalizationProfile
from backend.services import localization_service as svc
from backend.services.tts_service import TTSProvider


TEXT = "thì tôi sẽ quay cho bạn trong khung"


def write_audio(path, silent=False):
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        data = array.array("h", (
            0 if silent else round(2000 * math.sin(2 * math.pi * 440 * n / 16000))
            for n in range(8000)
        ))
        out.writeframes(data.tobytes())
    return path


@pytest.fixture
def setup_recovery(monkeypatch, tmp_path):
    async def no_wait(*args):
        pass

    monkeypatch.setattr(svc.asyncio, "sleep", no_wait)
    runtime = svc.TTSRuntimeConfig(TTSProvider.EDGE_TTS, "vi-VN-HoaiMyNeural", "", None, None, None)
    return dict(runtime=runtime, voice=runtime.default_voice, speaker_id="speaker-1",
                target_language="Vietnamese", output_path=tmp_path / "raw.wav")


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["strict", "auto"])
@pytest.mark.parametrize("failure", ["no_audio", "reset"])
async def test_zero_audio_transport_recovery_preserves_identity_and_verbatim_text(
    monkeypatch, setup_recovery, policy, failure,
):
    calls = []

    async def generate(text, *, output_path, **kwargs):
        calls.append((text, kwargs["voice_id"], kwargs["speed"]))
        if text == TEXT:
            if failure == "reset":
                output_path.write_bytes(b"")
                raise VoiceGenerationError("transport reset") from ClientConnectionResetError("reset")
            raise VoiceGenerationError("no audio") from NoAudioReceived("no audio")
        return write_audio(output_path)

    monkeypatch.setattr("backend.services.tts_service.generate_tts", generate)
    profile = LocalizationProfile(id="qa", name="qa", translation_automation=policy)
    events = []
    kw = dict(setup_recovery)
    path = kw.pop("output_path")
    identity = svc._chunk_synthesis_identity(
        text=TEXT, runtime=kw["runtime"], voice=kw["voice"], target_language="Vietnamese",
        speaker_id="speaker-1", reference_audio=None, reference_text=None, reference_language=None,
    )
    measured = await svc._generate_tts_chunk_atomic(
        TEXT, output_path=path, identity=identity, profile=profile, recovery_events=events, **kw,
    )
    assert len(calls) == 5
    assert " ".join(c[0] for c in calls[-2:]) == TEXT
    assert all(c[1:] == (kw["voice"], 1.0) for c in calls)
    assert measured == await svc._audio_duration_ms(path) > 0
    assert svc._chunk_identity_matches(path, identity)
    assert events[0]["reason"] == "tts_transport_split"
    assert events[0]["requires_human_review"] is True
    assert events[0]["text"] == TEXT
    assert not path.with_suffix(".wav.words.json").exists()
    assert profile.translation_automation == policy


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["strict", "auto"])
@pytest.mark.parametrize("failure", ["part_error", "silent", "cancel", "expired", "concat_corrupt"])
async def test_failed_recovery_keeps_previous_chunk_and_receipt(
    monkeypatch, setup_recovery, policy, failure,
):
    calls = []
    path = setup_recovery["output_path"]
    write_audio(path)
    svc._write_chunk_identity(path, "prior-identity", 500)
    original = path.read_bytes()
    receipt = svc._chunk_identity_path(path).read_bytes()
    events = []
    deadline = asyncio.get_running_loop().time() + 90
    clock = asyncio.get_running_loop().time

    async def generate(text, *, output_path, **kwargs):
        calls.append(text)
        if text == TEXT or (failure == "part_error" and len(calls) == 5):
            raise VoiceGenerationError("no audio") from NoAudioReceived("no audio")
        return write_audio(output_path, silent=failure == "silent" and len(calls) == 5)

    async def cancelled():
        return failure == "cancel" and len(calls) >= 4

    real_concat = svc._concat_tts_parts_sync

    def concat(parts, destination):
        if failure == "concat_corrupt":
            destination.write_bytes(b"not audio")
        else:
            real_concat(parts, destination)

    real_duration = svc._audio_duration_ms

    async def duration(audio_path):
        measured = await real_duration(audio_path)
        if failure == "expired" and audio_path.name == "joined.wav":
            # Exhaust the SAME absolute deadline after the real concat.
            monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: clock() + 100)
        return measured

    monkeypatch.setattr("backend.services.tts_service.generate_tts", generate)
    monkeypatch.setattr(svc, "_concat_tts_parts_sync", concat)
    monkeypatch.setattr(svc, "_audio_duration_ms", duration)
    kw = dict(setup_recovery)
    kw.pop("output_path")
    with pytest.raises((RuntimeError, JobCancelledError)):
        await svc._generate_tts_chunk_atomic(
            TEXT, output_path=path, identity="new-identity", profile=LocalizationProfile(
                id="qa", name="qa", translation_automation=policy), recovery_events=events,
            cancel_check=cancelled, _deadline_at=deadline, **kw,
        )
    assert path.read_bytes() == original
    assert svc._chunk_identity_path(path).read_bytes() == receipt
    assert events == []
    assert len(calls) <= 5


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["strict", "auto"])
@pytest.mark.parametrize("failure", ["auth", "config", "partial_reset", "timeout"])
async def test_unqualified_errors_never_split(monkeypatch, setup_recovery, policy, failure):
    calls = []

    async def generate(text, *, output_path, **kwargs):
        calls.append(text)
        if failure == "auth":
            raise VoiceGenerationError("HTTP 401 authentication failed")
        if failure == "config":
            raise ValueError("invalid voice configuration")
        if failure == "partial_reset":
            write_audio(output_path)
            raise ClientConnectionResetError("reset after audio")
        raise TimeoutError("request timeout")

    monkeypatch.setattr("backend.services.tts_service.generate_tts", generate)
    with pytest.raises((RuntimeError, ValueError, VoiceGenerationError)):
        await svc._generate_tts_chunk(TEXT, profile=LocalizationProfile(
            id="qa", name="qa", translation_automation=policy), **setup_recovery)
    assert all(text == TEXT for text in calls)
    assert len(calls) == (1 if failure in {"auth", "config"} else 3)
    assert not setup_recovery["output_path"].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["strict", "auto"])
async def test_normal_success_never_splits(monkeypatch, setup_recovery, policy):
    calls = []

    async def generate(text, *, output_path, **kwargs):
        calls.append(text)
        return write_audio(output_path)

    monkeypatch.setattr("backend.services.tts_service.generate_tts", generate)
    events = []
    await svc._generate_tts_chunk(TEXT, profile=LocalizationProfile(
        id="qa", name="qa", translation_automation=policy), recovery_events=events, **setup_recovery)
    assert calls == [TEXT] and events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["strict", "auto"])
async def test_multi_cue_split_reuses_cache_without_inventing_word_boundaries(
    monkeypatch, setup_recovery, tmp_path, policy,
):
    from backend.core.localization import LocalizationSegment, LocalizationTranscript
    from backend.services.video_utils import probe_duration

    calls = []

    async def generate(text, *, output_path, **kwargs):
        calls.append(text)
        if text == TEXT:
            raise VoiceGenerationError("no audio") from NoAudioReceived("no audio")
        return write_audio(output_path)

    monkeypatch.setattr("backend.services.tts_service.generate_tts", generate)
    monkeypatch.setattr(svc, "_tts_config", lambda *_: setup_recovery["runtime"])
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda p, *a:
                        2.2 if p.name == "source.mp4" else probe_duration(p, *a))
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(id="s1", source_text="原文", translated_text="thì tôi sẽ",
                            source_start_ms=0, source_end_ms=1000, translation_status="success"),
        LocalizationSegment(id="s2", source_text="另一句", translated_text="quay cho bạn trong khung",
                            source_start_ms=1000, source_end_ms=2000, translation_status="success"),
    ])
    profile = LocalizationProfile(id="qa", name="qa", translation_automation=policy,
                                  dub_rewrite_attempts=0, video_retime_mode="off")
    kwargs = dict(source_media=tmp_path / "source.mp4", target_language="Vietnamese",
                  profile=profile, user_settings=object(), work_dir=tmp_path / "work")
    result = await svc.synthesize_timed_dub(tx, **kwargs)
    assert result.chunk_count == 1 and len(calls) == 5
    count = len(calls)
    resumed = await svc.synthesize_timed_dub(tx, reuse_existing_chunks=True, **kwargs)
    assert len(calls) == count
    assert any(i["reason"] == "tts_transport_split" for i in resumed.issues)
    assert not (tmp_path / "work/chunk_0001.wav.words.json").exists()
    assert resumed.transcript.metadata["dubbing"]["measured_segment_ids"] == []
