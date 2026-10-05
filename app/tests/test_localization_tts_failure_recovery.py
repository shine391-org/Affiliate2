"""Canonical TTS retry/atomic-write QA with provider-only fault injection.

No LIVE service is claimed. Success uses a real WAV and real FFprobe duration,
failure uses the real chunk retry loop and atomic publication boundary.
"""
import hashlib
import shutil
import wave

import pytest

from backend.core.localization import LocalizationProfile
from backend.services import localization_service as svc, tts_service


RETRYABLE = ["connection", "timeout", "429_status", "429_code", "503", "unavailable"]
DETERMINISTIC = ["invalid_key", "invalid_voice", "invalid_model", "wrapped_401"]


def _error(kind):
    if kind == "timeout":
        return TimeoutError("TTS timed out")
    if kind in {"connection", "unavailable"}:
        return ConnectionError("TTS provider temporarily unavailable")
    if kind.startswith("429") or kind == "503":
        error = RuntimeError("TTS provider rejected request")
        setattr(error, "code" if kind == "429_code" else "status_code",
                429 if kind.startswith("429") else 503)
        return error
    if kind == "wrapped_401":
        original = RuntimeError("Authentication rejected")
        setattr(original, "status_code", 401)
        wrapped = RuntimeError("TTS provider failed")
        wrapped.__cause__ = original
        return wrapped
    return RuntimeError({"invalid_key": "Invalid API key",
                         "invalid_voice": "Invalid voice",
                         "invalid_model": "Invalid model"}[kind])


def _runtime():
    return svc.TTSRuntimeConfig(provider=tts_service.TTSProvider.OPENAI_COMPATIBLE,
                                default_voice="fixture", api_key="", model="fixture",
                                base_url=None, region=None)


def _wav(path):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(b"\x01\x00" * int(24000 * 0.8))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", RETRYABLE)
async def test_tts_transient_recovers_and_measures_real_output(monkeypatch, tmp_path, kind):
    if not shutil.which("ffprobe"):
        pytest.skip("FFprobe required for real duration measurement")
    calls = []

    async def generate(text, **kwargs):
        calls.append(text)
        if len(calls) == 1:
            raise _error(kind)
        _wav(kwargs["output_path"])
        return kwargs["output_path"]

    monkeypatch.setattr(tts_service, "generate_tts", generate)
    output = tmp_path / "chunk.wav"
    measured = await svc._generate_tts_chunk(
        "Xin chào", runtime=_runtime(), voice="fixture", profile=LocalizationProfile(id="qa-tts", name="QA TTS"),
        speaker_id=None, target_language="vi", output_path=output, attempts=3,
    )
    assert calls == ["Xin chào", "Xin chào"]
    assert measured == 800
    assert await svc._audio_duration_ms(output) == measured


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", RETRYABLE + DETERMINISTIC)
async def test_tts_exhaustion_is_bounded_and_preserves_previous_atomic_chunk(monkeypatch, tmp_path, kind):
    output = tmp_path / "chunk_0001.wav"
    _wav(output)
    sidecar = svc._chunk_identity_path(output)
    sidecar.write_text('{"identity":"approved-before-retry","duration_ms":800}')
    before = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
              for p in (output, sidecar)}
    calls = []
    error = _error(kind)

    async def generate(text, **kwargs):
        calls.append(text)
        # A partial provider write must never replace an accepted old chunk.
        kwargs["output_path"].write_bytes(b"partial provider output")
        raise error

    monkeypatch.setattr(tts_service, "generate_tts", generate)
    with pytest.raises(Exception) as caught:
        await svc._generate_tts_chunk_atomic(
            "Xin chào", runtime=_runtime(), voice="fixture", profile=LocalizationProfile(id="qa-tts", name="QA TTS"),
            speaker_id=None, target_language="vi", output_path=output,
            identity="new-input", attempts=3,
        )
    assert len(calls) == (3 if kind in RETRYABLE else 1)
    if kind in RETRYABLE:
        assert caught.value.__cause__ is error
        assert "3 attempts" in str(caught.value)
    else:
        assert caught.value is error
    assert {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in (output, sidecar)} == before
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(before)
