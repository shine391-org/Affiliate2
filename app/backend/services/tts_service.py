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


PROVIDER_INFO = {
    TTSProvider.OPENAI_TTS: {"label": "OpenAI TTS",  "cost_1k_chars": 0.015, "quality": "standard", "needs_key": True},
    TTSProvider.EDGE_TTS:   {"label": "Edge TTS",    "cost_1k_chars": 0.0,   "quality": "basic",    "needs_key": False},
}

DEFAULT_VOICES = {
    TTSProvider.OPENAI_TTS: "alloy",                    # alloy|echo|fable|onyx|nova|shimmer
    TTSProvider.EDGE_TTS:   "en-US-AndrewMultilingualNeural",  # Natural multilingual voice
}


async def generate_tts(
    text: str,
    provider: TTSProvider = TTSProvider.EDGE_TTS,
    voice_id: str = None,
    api_key: str = "",
    output_path: Path = None,
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
        return await generate_voice(text, voice_id=voice, api_key=api_key, output_path=output_path)

    elif provider == TTSProvider.EDGE_TTS:
        from backend.services.edge_tts_service import generate_voice
        return await generate_voice(text, voice_id=voice, output_path=output_path)

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
