# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import sys
import types

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


def test_openai_tts_speed_is_forwarded(monkeypatch, tmp_path):
    seen = {}

    async def fake_generate(text, voice_id=None, api_key="", output_path=None,
                            model="tts-1", speed=1.0, **kwargs):
        seen.update(model=model, speed=speed)
        output_path.write_bytes(b"x")
        return output_path

    monkeypatch.setattr("backend.services.openai_tts_service.generate_voice", fake_generate)
    out = asyncio.run(generate_tts(
        "hello",
        provider=TTSProvider.OPENAI_TTS,
        api_key="test",
        output_path=tmp_path / "o.mp3",
        model="tts-1-hd",
        speed=1.06,
    ))
    assert out.exists()
    assert seen == {"model": "tts-1-hd", "speed": 1.06}
