# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""OpenAI TTS — budget option using OpenAI's speech API. ~$0.015/1K chars."""
import asyncio
import logging
from pathlib import Path

from backend.config import settings
from backend.core.exceptions import VoiceGenerationError

logger = logging.getLogger(__name__)


async def generate_voice(
    text: str,
    voice_id: str = "alloy",
    api_key: str = "",
    output_path: Path = None,
    model: str = "tts-1",
) -> Path:
    """
    Generate speech using OpenAI TTS API.
    Voices: alloy, echo, fable, onyx, nova, shimmer.
    Models: tts-1 (fast, $15/1M chars) or tts-1-hd (quality, $30/1M chars).
    """
    if not api_key:
        raise VoiceGenerationError("OpenAI API key not configured for TTS")

    if output_path is None:
        output_path = settings.AUDIO_DIR / f"openai_{hash(text) & 0xFFFFFFFF:08x}.mp3"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _generate():
        import openai
        client = openai.OpenAI(api_key=api_key)

        response = client.audio.speech.create(
            model=model,
            voice=voice_id,
            input=text,
            response_format="mp3",
        )

        response.stream_to_file(str(output_path))

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise VoiceGenerationError("OpenAI TTS returned empty audio")

        return output_path

    try:
        result = await asyncio.to_thread(_generate)
        logger.info(f"OpenAI TTS generated: {result} ({result.stat().st_size / 1024:.0f}KB)")
        return result
    except VoiceGenerationError:
        raise
    except Exception as e:
        raise VoiceGenerationError(f"OpenAI TTS generation failed: {e}")
