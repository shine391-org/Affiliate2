# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio

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
