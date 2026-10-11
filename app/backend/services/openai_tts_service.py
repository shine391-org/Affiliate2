# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""OpenAI TTS — budget option using OpenAI's speech API. ~$0.015/1K chars."""
import asyncio
import logging
import os
import threading
import uuid
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
    speed: float = 1.0,
) -> Path:
    """
    Generate speech using OpenAI TTS API.
    Voices: alloy, echo, fable, onyx, nova, shimmer.
    Models: tts-1 (fast, $15/1M chars) or tts-1-hd (quality, $30/1M chars).
    """
    if not api_key:
        raise VoiceGenerationError("OpenAI API key not configured for TTS")
    if not 0.75 <= float(speed) <= 1.25:
        raise VoiceGenerationError("OpenAI TTS speed must be between 0.75x and 1.25x")

    if output_path is None:
        output_path = settings.AUDIO_DIR / f"openai_{hash(text) & 0xFFFFFFFF:08x}.mp3"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Stream into a private sibling first. `stream_to_file` writes the target
    # path directly, so a cancel/timeout of the surrounding await leaves the
    # SYNC SDK thread running and writing a partial file onto the path the
    # caller is about to publish. A private part file plus an atomic replace
    # means an interrupted generation can never be mistaken for a finished one.
    # The part name is unique per call so two concurrent generations (or a late
    # writer from a previous call) can never share one scratch path.
    part_path = output_path.with_name(
        f"{output_path.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.part")
    # Producer-side cancellation fence. Cancelling the await cannot kill this
    # thread, so the thread itself must notice it was abandoned BEFORE it
    # publishes, and delete its own bytes instead of racing the caller.
    abandoned = threading.Event()

    def _generate():
        import openai
        client = openai.OpenAI(api_key=api_key, max_retries=0)

        response = client.audio.speech.create(
            model=model,
            voice=voice_id,
            input=text,
            response_format="mp3",
            speed=float(speed),
        )

        try:
            response.stream_to_file(str(part_path))

            if abandoned.is_set():
                # The caller already gave up on this generation. Its bytes are
                # not a result: drop them on the floor and publish nothing.
                raise VoiceGenerationError("OpenAI TTS generation was abandoned")

            if not part_path.exists() or part_path.stat().st_size == 0:
                raise VoiceGenerationError("OpenAI TTS returned empty audio")

            os.replace(part_path, output_path)
        finally:
            part_path.unlink(missing_ok=True)

        return output_path

    try:
        result = await asyncio.to_thread(_generate)
    except asyncio.CancelledError:
        # The worker thread is already running and cannot be stopped, so tell it
        # to fence its own publish instead of letting it land on our output.
        abandoned.set()
        raise
    except VoiceGenerationError:
        raise
    except Exception as e:
        raise VoiceGenerationError(f"OpenAI TTS generation failed: {e}")
    logger.info(f"OpenAI TTS generated: {result} ({result.stat().st_size / 1024:.0f}KB)")
    return result
