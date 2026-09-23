# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from pathlib import Path

import httpx

from backend.core.exceptions import VoiceGenerationError


async def generate_voice(
    text: str,
    *,
    voice_id: str,
    api_key: str,
    output_path: Path,
    model: str = "eleven_multilingual_v2",
    base_url: str | None = None,
    speed: float = 1.0,
) -> Path:
    if not api_key:
        raise VoiceGenerationError("ElevenLabs API key is required")
    base = (base_url or "https://api.elevenlabs.io/v1").rstrip("/")
    url = f"{base}/text-to-speech/{voice_id}"
    payload = {
        "text": text,
        "model_id": model or "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": max(0.7, min(1.2, float(speed))),
        },
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        res = await client.post(url, headers=headers, json=payload)
    if res.status_code >= 400:
        raise VoiceGenerationError(
            f"ElevenLabs TTS HTTP {res.status_code}: {res.text[:200]}"
        )
    if not res.content:
        raise VoiceGenerationError("ElevenLabs TTS returned empty audio")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(res.content)
    return output_path
