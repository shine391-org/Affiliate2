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


async def list_voices(
    provider: TTSProvider,
    api_key: str = "",
    *,
    base_url: str | None = None,
) -> list[dict]:
    """List available voices for a provider without generating paid speech."""
    if provider == TTSProvider.OPENAI_TTS:
        return [
            {"voice_id": "alloy",   "name": "Alloy",   "category": "neutral"},
            {"voice_id": "echo",    "name": "Echo",     "category": "male"},
            {"voice_id": "fable",   "name": "Fable",    "category": "british"},
            {"voice_id": "onyx",    "name": "Onyx",     "category": "deep male"},
            {"voice_id": "nova",    "name": "Nova",     "category": "female"},
            {"voice_id": "shimmer", "name": "Shimmer",  "category": "soft female"},
        ]

    if provider == TTSProvider.EDGE_TTS:
        from backend.services.edge_tts_service import get_voice_list
        return await get_voice_list()

    if provider == TTSProvider.ELEVENLABS:
        from backend.services.elevenlabs_tts_service import list_voices as list_elevenlabs_voices
        return await list_elevenlabs_voices(api_key=api_key, base_url=base_url)

    return []


async def test_tts_connection(
    *,
    provider: TTSProvider,
    api_key: str = "",
    base_url: str | None = None,
    model: str | None = None,
    voice_id: str | None = None,
    region: str | None = None,
) -> dict:
    """Cheap provider/voice/model validation that never synthesizes speech.

    The result distinguishes what was actually checked from what was merely
    configured:

    - ``configuration_valid``: the local settings are complete enough to try.
    - ``connection_verified``: a real request to the provider was made and
      succeeded. This is only True for providers probed over the network.
    - ``ok``: usable for the localization preflight, i.e. configuration is
      valid and, where a probe exists, it passed.

    Never report ``connection_verified=True`` for a provider that was not
    actually contacted: an invalid API key or a dead local GPT-SoVITS endpoint
    must never be presented as a working connection.
    """
    voice = (voice_id or DEFAULT_VOICES.get(provider, "") or "").strip()
    selected_model = (model or "").strip() or None

    if provider == TTSProvider.EDGE_TTS:
        voices = await list_voices(provider)
        ids = {row.get("voice_id") for row in voices}
        voice_valid = not voice or voice in ids
        return {
            "ok": voice_valid,
            "provider": provider.value,
            "configuration_valid": voice_valid,
            "connection_verified": voice_valid,
            "authenticated": True,
            "endpoint": "edge-tts",
            "voice_count": len(voices),
            "selected_voice_valid": voice_valid,
            "model_valid": True,
            "voices": voices,
            "models": [],
            "message": (
                "Edge TTS is reachable and the selected voice is available."
                if voice_valid else
                f"Edge TTS voice '{voice}' is not available."
            ),
        }

    if provider == TTSProvider.ELEVENLABS:
        from backend.services.elevenlabs_tts_service import (
            canonicalize_base_url,
            list_models,
            list_voices as list_elevenlabs_voices,
        )
        voices = await list_elevenlabs_voices(api_key=api_key, base_url=base_url)
        models = await list_models(api_key=api_key, base_url=base_url)
        voice_ids = {row.get("voice_id") for row in voices}
        model_ids = {row.get("model_id") for row in models}
        voice_valid = bool(voice) and voice in voice_ids
        model_valid = bool(selected_model) and selected_model in model_ids
        ok = voice_valid and model_valid
        if not voice:
            message = "Choose an ElevenLabs voice before localization."
        elif not voice_valid:
            message = f"ElevenLabs voice '{voice}' is missing or inaccessible."
        elif not selected_model:
            message = "Choose an ElevenLabs model before localization."
        elif not model_valid:
            message = f"ElevenLabs model '{selected_model}' is missing or unavailable."
        else:
            message = "ElevenLabs authenticated; the selected voice and model are available."
        return {
            "ok": ok,
            "provider": provider.value,
            "configuration_valid": ok,
            "connection_verified": ok,
            "authenticated": True,
            "endpoint": canonicalize_base_url(base_url),
            "voice_count": len(voices),
            "selected_voice_valid": voice_valid,
            "model_valid": model_valid,
            "voices": voices,
            "models": models,
            "message": message,
        }

    info = PROVIDER_INFO[provider]
    if info["needs_key"] and not api_key:
        raise VoiceGenerationError(f"{info['label']} API key not configured")
    if provider == TTSProvider.AZURE and not (region or base_url):
        raise VoiceGenerationError("Azure Speech requires a region or custom endpoint")
    if provider in {TTSProvider.OPENAI_COMPATIBLE, TTSProvider.GPT_SOVITS} and not base_url:
        raise VoiceGenerationError(f"{info['label']} requires a base URL")
    # No cheap, side-effect-free probe exists for these providers, so this
    # result is explicitly NOT a verified connection.
    return {
        "ok": True,
        "provider": provider.value,
        "configuration_valid": True,
        "connection_verified": False,
        "authenticated": None,
        "endpoint": base_url,
        "voice_count": None,
        "selected_voice_valid": bool(voice),
        "model_valid": bool(selected_model) or provider in {TTSProvider.AZURE, TTSProvider.GPT_SOVITS},
        "voices": await list_voices(provider, api_key, base_url=base_url),
        "models": [],
        "message": (
            "Cấu hình hợp lệ. Chưa xác minh kết nối tới nhà cung cấp này; "
            "dùng Nghe thử giọng để xác nhận tạo speech thực tế."
        ),
    }


def estimate_tts_cost(text: str, provider: TTSProvider) -> float:
    """Estimate TTS cost for a given text."""
    chars = len(text)
    cost_per_1k = PROVIDER_INFO[provider]["cost_1k_chars"]
    return round((chars / 1000) * cost_per_1k, 4)
