# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import wave

import pytest

from backend.services import edge_tts_service
from backend.services.tts_service import TTSProvider, generate_tts


def test_edge_tts_rejects_extreme_speed_before_network(tmp_path):
    with pytest.raises(Exception, match="speed"):
        asyncio.run(edge_tts_service.generate_voice(
            "xin chào",
            output_path=tmp_path / "x.mp3",
            speed=1.5,
        ))


def test_tts_provider_registry_covers_hq_and_clone_paths():
    assert {
        TTSProvider.EDGE_TTS,
        TTSProvider.OPENAI_TTS,
        TTSProvider.OPENAI_COMPATIBLE,
        TTSProvider.AZURE,
        TTSProvider.ELEVENLABS,
        TTSProvider.GPT_SOVITS,
    } == set(TTSProvider)


def test_tts_service_passes_speed_to_edge(monkeypatch, tmp_path):
    seen = {}

    async def fake_generate(text, voice_id=None, output_path=None, speed=1.0, **kwargs):
        seen["speed"] = speed
        output_path.write_bytes(b"x")
        return output_path

    monkeypatch.setattr("backend.services.edge_tts_service.generate_voice", fake_generate)
    out = asyncio.run(generate_tts(
        "hello",
        provider=TTSProvider.EDGE_TTS,
        output_path=tmp_path / "x.mp3",
        speed=1.08,
    ))
    assert out.exists()
    assert seen["speed"] == 1.08


def test_openai_compatible_dispatches_endpoint_model_voice_and_key(monkeypatch, tmp_path):
    seen = {}

    async def fake_generate(text, **kwargs):
        seen.update(kwargs)
        kwargs["output_path"].write_bytes(b"x")
        return kwargs["output_path"]

    monkeypatch.setattr(
        "backend.services.openai_compatible_tts_service.generate_voice",
        fake_generate,
    )
    out = asyncio.run(generate_tts(
        "xin chào",
        provider=TTSProvider.OPENAI_COMPATIBLE,
        api_key="k",
        base_url="http://localhost:9000/v1",
        voice_id="voice-a",
        model="model-a",
        output_path=tmp_path / "c.wav",
    ))
    assert out.exists()
    assert seen["api_key"] == "k"
    assert seen["base_url"] == "http://localhost:9000/v1"
    assert seen["voice_id"] == "voice-a"
    assert seen["model"] == "model-a"


def test_azure_dispatches_region(monkeypatch, tmp_path):
    seen = {}

    async def fake_generate(text, **kwargs):
        seen.update(kwargs)
        kwargs["output_path"].write_bytes(b"x")
        return kwargs["output_path"]

    monkeypatch.setattr("backend.services.azure_tts_service.generate_voice", fake_generate)
    out = asyncio.run(generate_tts(
        "xin chào",
        provider=TTSProvider.AZURE,
        api_key="azure-key",
        region="southeastasia",
        voice_id="vi-VN-HoaiMyNeural",
        output_path=tmp_path / "a.wav",
    ))
    assert out.exists()
    assert seen["region"] == "southeastasia"
    assert seen["api_key"] == "azure-key"


def test_elevenlabs_dispatches_multilingual_model(monkeypatch, tmp_path):
    seen = {}

    async def fake_generate(text, **kwargs):
        seen.update(kwargs)
        kwargs["output_path"].write_bytes(b"x")
        return kwargs["output_path"]

    monkeypatch.setattr("backend.services.elevenlabs_tts_service.generate_voice", fake_generate)
    out = asyncio.run(generate_tts(
        "xin chào",
        provider=TTSProvider.ELEVENLABS,
        api_key="eleven-key",
        voice_id="voice-id",
        model="eleven_multilingual_v2",
        output_path=tmp_path / "e.mp3",
    ))
    assert out.exists()
    assert seen["model"] == "eleven_multilingual_v2"
    assert seen["voice_id"] == "voice-id"


def test_gpt_sovits_dispatches_reference_audio_and_languages(monkeypatch, tmp_path):
    seen = {}

    async def fake_generate(text, **kwargs):
        seen.update(kwargs)
        kwargs["output_path"].write_bytes(b"x")
        return kwargs["output_path"]

    monkeypatch.setattr("backend.services.gpt_sovits_service.generate_voice", fake_generate)
    out = asyncio.run(generate_tts(
        "xin chào",
        provider=TTSProvider.GPT_SOVITS,
        base_url="http://127.0.0.1:9880",
        output_path=tmp_path / "g.wav",
        reference_audio="ref.wav",
        reference_text="hello",
        reference_language="en",
        target_language="vi",
    ))
    assert out.exists()
    assert seen["reference_audio"] == "ref.wav"
    assert seen["prompt_text"] == "hello"
    assert seen["prompt_language"] == "en"
    assert seen["text_language"] == "vi"



def test_azure_ssml_uses_selected_voice_locale(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from backend.services import azure_tts_service

    captured = {}

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def post(self, url, headers=None, content=None, **kwargs):
            captured["content"] = content.decode("utf-8")
            return SimpleNamespace(status_code=200, content=b"wav", text="")

    monkeypatch.setattr(azure_tts_service.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    out = asyncio.run(azure_tts_service.generate_voice(
        "Hello",
        voice_id="en-US-AvaMultilingualNeural",
        api_key="k",
        region="eastus",
        output_path=tmp_path / "azure.wav",
    ))
    assert out.exists()
    assert "xml:lang='en-US'" in captured["content"]



def test_edge_speed_one_maps_to_native_zero_percent(monkeypatch, tmp_path):
    captured = {}

    class FakeCommunicate:
        def __init__(self, text, voice, rate, pitch=None):
            captured["text"] = text
            captured["rate"] = rate

        async def save(self, path):
            from pathlib import Path
            Path(path).write_bytes(b"mp3")

    import edge_tts
    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)
    out = asyncio.run(edge_tts_service.generate_voice(
        "xin chào",
        output_path=tmp_path / "edge.mp3",
        speed=1.0,
    ))
    assert out.exists()
    assert captured["rate"] == "+0%"
    assert captured["text"] == "xin chào"


def test_edge_tts_does_not_invent_pause_punctuation():
    text = "Now we continue naturally. But keep the original rhythm."
    assert edge_tts_service._add_natural_pauses(text) == text



def test_gpt_sovits_rejects_vietnamese_in_donor_compatible_mode(tmp_path):
    from backend.core.exceptions import VoiceGenerationError
    from backend.services import gpt_sovits_service

    with pytest.raises(VoiceGenerationError, match="Chinese/English only"):
        asyncio.run(gpt_sovits_service.generate_voice(
            "xin chào",
            output_path=tmp_path / "g.wav",
            reference_audio="ref.wav",
            text_language="Vietnamese",
            prompt_language="English",
        ))


def test_gpt_sovits_language_normalization_matches_donor_contract():
    from backend.services.gpt_sovits_service import _normalize_gpt_sovits_language

    assert _normalize_gpt_sovits_language("Chinese") == "zh"
    assert _normalize_gpt_sovits_language("en") == "en"

def _write_tone_with_silence(
    path,
    *,
    leading_ms: int,
    tone_ms: int,
    trailing_ms: int,
    rate: int = 16000,
) -> None:
    frames = bytearray()
    for _ in range(int(rate * leading_ms / 1000)):
        frames.extend((0).to_bytes(2, "little", signed=True))
    for i in range(int(rate * tone_ms / 1000)):
        sample = int(12000 * math.sin(2 * math.pi * 440 * i / rate))
        frames.extend(sample.to_bytes(2, "little", signed=True))
    for _ in range(int(rate * trailing_ms / 1000)):
        frames.extend((0).to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


def test_edge_tts_boundary_trim_removes_only_long_silent_padding(tmp_path):
    from backend.services import localization_service as svc

    audio = tmp_path / "edge.wav"
    _write_tone_with_silence(
        audio,
        leading_ms=350,
        tone_ms=500,
        trailing_ms=450,
    )

    duration = svc._trim_edge_tts_boundary_silence_sync(audio, 1300)

    # Keep the speech plus conservative safety padding, but do not count the
    # hundreds of milliseconds of Edge-TTS container silence as speech.
    assert 500 <= duration <= 850
    assert audio.is_file()
    assert audio.stat().st_size > 0


def test_edge_tts_boundary_trim_leaves_continuous_speech_artifact_untouched(tmp_path):
    from backend.services import localization_service as svc

    audio = tmp_path / "continuous.wav"
    _write_tone_with_silence(
        audio,
        leading_ms=0,
        tone_ms=600,
        trailing_ms=0,
    )
    before = hashlib.sha256(audio.read_bytes()).hexdigest()

    duration = svc._trim_edge_tts_boundary_silence_sync(audio, 600)

    assert duration == 600
    assert hashlib.sha256(audio.read_bytes()).hexdigest() == before


@pytest.mark.asyncio
async def test_localization_edge_tts_measures_trimmed_audio_before_timing(monkeypatch, tmp_path):
    from backend.core.localization import LocalizationProfile
    from backend.services import localization_service as svc
    from backend.services.tts_service import TTSProvider
    import backend.services.tts_service as tts_service

    output = tmp_path / "chunk.wav"

    async def fake_generate_tts(text, *, output_path, **kwargs):
        output_path.write_bytes(b"raw")
        return output_path

    async def fake_duration(path):
        return 2088

    seen = {}

    def fake_trim(path, raw_duration):
        seen["path"] = path
        seen["raw_duration"] = raw_duration
        path.write_bytes(b"trimmed")
        return 1500

    monkeypatch.setattr(tts_service, "generate_tts", fake_generate_tts)
    monkeypatch.setattr(svc, "_audio_duration_ms", fake_duration)
    monkeypatch.setattr(svc, "_trim_edge_tts_boundary_silence_sync", fake_trim)

    runtime = svc.TTSRuntimeConfig(
        provider=TTSProvider.EDGE_TTS,
        default_voice="vi-VN-HoaiMyNeural",
        api_key="",
        model=None,
        base_url=None,
        region=None,
    )
    profile = LocalizationProfile(id="trim", name="trim", tts_provider="edge_tts")

    duration = await svc._generate_tts_chunk(
        "Cảm ơn.",
        runtime=runtime,
        voice="vi-VN-HoaiMyNeural",
        profile=profile,
        speaker_id=None,
        target_language="Vietnamese",
        output_path=output,
        attempts=1,
    )

    assert seen["raw_duration"] == 2088
    assert duration == 1500
    assert output.read_bytes() == b"trimmed"


def test_old_edge_chunk_sidecar_is_invalidated_without_touching_other_providers(tmp_path):
    from backend.services import localization_service as svc
    from backend.services.tts_service import TTSProvider

    def legacy_identity(provider: str) -> str:
        payload = {
            "rules": 1,
            "provider": provider,
            "model": None,
            "base_url": None,
            "region": None,
            "voice": "vi-VN-HoaiMyNeural",
            "language": "Vietnamese",
            "speaker_id": None,
            "text": "Cảm ơn.",
            "reference_audio": None,
            "reference_text": None,
            "reference_language": None,
        }
        return hashlib.sha256(json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

    edge_runtime = svc.TTSRuntimeConfig(
        provider=TTSProvider.EDGE_TTS,
        default_voice="vi-VN-HoaiMyNeural",
        api_key="",
        model=None,
        base_url=None,
        region=None,
    )
    new_edge_identity = svc._chunk_synthesis_identity(
        text="Cảm ơn.",
        runtime=edge_runtime,
        voice="vi-VN-HoaiMyNeural",
        target_language="Vietnamese",
        speaker_id=None,
        reference_audio=None,
        reference_text=None,
        reference_language=None,
    )
    old_edge_identity = legacy_identity("edge_tts")
    assert new_edge_identity != old_edge_identity

    v1_payload = {
        "rules": 1,
        "provider": "edge_tts",
        "model": None,
        "base_url": None,
        "region": None,
        "voice": "vi-VN-HoaiMyNeural",
        "language": "Vietnamese",
        "speaker_id": None,
        "text": "Cảm ơn.",
        "reference_audio": None,
        "reference_text": None,
        "reference_language": None,
        "boundary_postprocess": "edge-boundary-trim-v1",
    }
    v1_identity = hashlib.sha256(json.dumps(
        v1_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    assert new_edge_identity != v1_identity

    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"old-edge-artifact")
    sidecar = audio.with_name(audio.name + ".input.json")
    sidecar.write_text(json.dumps({
        "rules": 1,
        "identity": old_edge_identity,
        "duration_ms": 2088,
    }), encoding="utf-8")
    assert svc._chunk_identity_matches(audio, new_edge_identity) is False
    assert svc._chunk_persisted_duration_ms(audio, new_edge_identity) is None

    # Non-Edge providers keep the old synthesis identity; adding this cleanup
    # must not force a paid/clone provider to regenerate a valid cached chunk.
    openai_runtime = svc.TTSRuntimeConfig(
        provider=TTSProvider.OPENAI_TTS,
        default_voice="alloy",
        api_key="x",
        model=None,
        base_url=None,
        region=None,
    )
    new_openai_identity = svc._chunk_synthesis_identity(
        text="Cảm ơn.",
        runtime=openai_runtime,
        voice="vi-VN-HoaiMyNeural",
        target_language="Vietnamese",
        speaker_id=None,
        reference_audio=None,
        reference_text=None,
        reference_language=None,
    )
    assert new_openai_identity == legacy_identity("openai_tts")


def test_edge_tts_boundary_trim_preserves_internal_pause(tmp_path):
    from backend.services import localization_service as svc

    audio = tmp_path / "internal-pause.wav"
    rate = 16000
    frames = bytearray()

    def tone(ms, phase_offset=0):
        count = int(rate * ms / 1000)
        for i in range(count):
            sample = int(
                12000 * math.sin(2 * math.pi * 440 * (i + phase_offset) / rate)
            )
            frames.extend(sample.to_bytes(2, "little", signed=True))

    tone(220)
    frames.extend(b"\0\0" * int(rate * 320 / 1000))
    tone(220, 5000)

    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))

    before = hashlib.sha256(audio.read_bytes()).hexdigest()
    duration = svc._trim_edge_tts_boundary_silence_sync(audio, 760)

    # The 320ms pause is internal speech timing, not provider padding. Boundary
    # cleanup must not collapse it or replace the original artifact.
    assert duration == 760
    assert hashlib.sha256(audio.read_bytes()).hexdigest() == before

