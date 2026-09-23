# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Multi-provider TTS service.
Unified interface for: OpenAI TTS, Edge TTS.
Same pattern as ai_provider.py — factory resolves the right backend.
"""
import logging
from enum import Enum
from pathlib import Path

from backend.config import settings
from backend.core.exceptions import VoiceGenerationError

logger = logging.getLogger(__name__)


class TTSProvider(str, Enum):
    OPENAI_TTS = "openai_tts"
    EDGE_TTS = "edge_tts"
    OPENAI_COMPATIBLE = "openai_compatible_tts"
    AZURE = "azure_tts"
    ELEVENLABS = "elevenlabs"
    GPT_SOVITS = "gpt_sovits"


PROVIDER_INFO = {
    TTSProvider.OPENAI_TTS: {"label": "OpenAI TTS", "cost_1k_chars": 0.015, "quality": "hq", "needs_key": True},
    TTSProvider.EDGE_TTS: {"label": "Edge TTS", "cost_1k_chars": 0.0, "quality": "preview", "needs_key": False},
    TTSProvider.OPENAI_COMPATIBLE: {"label": "OpenAI-compatible TTS", "cost_1k_chars": 0.0, "quality": "hq", "needs_key": False},
    TTSProvider.AZURE: {"label": "Azure Speech", "cost_1k_chars": 0.0, "quality": "hq", "needs_key": True},
    TTSProvider.ELEVENLABS: {"label": "ElevenLabs", "cost_1k_chars": 0.0, "quality": "hq", "needs_key": True},
    TTSProvider.GPT_SOVITS: {"label": "GPT-SoVITS", "cost_1k_chars": 0.0, "quality": "voice_clone", "needs_key": False},
}

DEFAULT_VOICES = {
    TTSProvider.OPENAI_TTS: "alloy",
    TTSProvider.EDGE_TTS: "vi-VN-HoaiMyNeural",
    TTSProvider.OPENAI_COMPATIBLE: "alloy",
    TTSProvider.AZURE: "vi-VN-HoaiMyNeural",
    TTSProvider.ELEVENLABS: "",
    TTSProvider.GPT_SOVITS: "",
}


async def generate_tts(
    text: str,
    provider: TTSProvider = TTSProvider.EDGE_TTS,
    voice_id: str = None,
    api_key: str = "",
    output_path: Path = None,
    model: str | None = None,
    speed: float = 1.0,
    base_url: str | None = None,
    region: str | None = None,
    reference_audio: str | None = None,
    reference_text: str | None = None,
    reference_language: str | None = None,
    target_language: str | None = None,
) -> Path:
    """Generate speech using any supported TTS provider. Returns audio file path."""
    if output_path is None:
        output_path = settings.AUDIO_DIR / f"voice_{hash(text) & 0xFFFFFFFF:08x}.mp3"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    voice = voice_id or DEFAULT_VOICES.get(provider, "")

    info = PROVIDER_INFO[provider]
    if info["needs_key"] and not api_key:
        raise VoiceGenerationError(f"{info['label']} API key not configured")

    logger.info(f"Generating TTS with {info['label']}, voice={voice}, text_len={len(text)}")

    if provider == TTSProvider.OPENAI_TTS:
        from backend.services.openai_tts_service import generate_voice
        return await generate_voice(
            text, voice_id=voice, api_key=api_key, output_path=output_path,
            model=model or "tts-1", speed=speed,
        )

    elif provider == TTSProvider.EDGE_TTS:
        from backend.services.edge_tts_service import generate_voice
        return await generate_voice(text, voice_id=voice, output_path=output_path, speed=speed)

    elif provider == TTSProvider.OPENAI_COMPATIBLE:
        from backend.services.openai_compatible_tts_service import generate_voice
        return await generate_voice(
            text, voice_id=voice, api_key=api_key, base_url=base_url or "",
            output_path=output_path, model=model or "tts-1", speed=speed,
        )

    elif provider == TTSProvider.AZURE:
        from backend.services.azure_tts_service import generate_voice
        return await generate_voice(
            text, voice_id=voice, api_key=api_key, region=region or "",
            base_url=base_url, output_path=output_path, speed=speed,
        )

    elif provider == TTSProvider.ELEVENLABS:
        from backend.services.elevenlabs_tts_service import generate_voice
        return await generate_voice(
            text, voice_id=voice, api_key=api_key, output_path=output_path,
            model=model or "eleven_multilingual_v2", base_url=base_url, speed=speed,
        )

    elif provider == TTSProvider.GPT_SOVITS:
        from backend.services.gpt_sovits_service import generate_voice
        return await generate_voice(
            text, output_path=output_path,
            base_url=base_url or "http://127.0.0.1:9880",
            text_language=target_language or "vi",
            reference_audio=reference_audio,
            prompt_text=reference_text,
            prompt_language=reference_language,
            speed=speed,
        )

    raise VoiceGenerationError(f"Unknown TTS provider: {provider}")


async def list_voices(provider: TTSProvider, api_key: str = "") -> list[dict]:
    """List available voices for a provider."""
    if provider == TTSProvider.OPENAI_TTS:
        return [
            {"voice_id": "alloy",   "name": "Alloy",   "category": "neutral"},
            {"voice_id": "echo",    "name": "Echo",     "category": "male"},
            {"voice_id": "fable",   "name": "Fable",    "category": "british"},
            {"voice_id": "onyx",    "name": "Onyx",     "category": "deep male"},
            {"voice_id": "nova",    "name": "Nova",     "category": "female"},
            {"voice_id": "shimmer", "name": "Shimmer",  "category": "soft female"},
        ]

    elif provider == TTSProvider.EDGE_TTS:
        from backend.services.edge_tts_service import get_voice_list
        return await get_voice_list()

    return []


def estimate_tts_cost(text: str, provider: TTSProvider) -> float:
    """Estimate TTS cost for a given text."""
    chars = len(text)
    cost_per_1k = PROVIDER_INFO[provider]["cost_1k_chars"]
    return round((chars / 1000) * cost_per_1k, 4)
