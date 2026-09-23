# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from pathlib import Path

import httpx

from backend.core.exceptions import VoiceGenerationError


def normalize_base_url(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise VoiceGenerationError("OpenAI-compatible TTS base URL must be http(s)")
    if base.endswith("/audio/speech"):
        base = base[: -len("/audio/speech")]
    return base.rstrip("/")


async def generate_voice(
    text: str,
    *,
    voice_id: str,
    api_key: str,
    base_url: str,
    output_path: Path,
    model: str = "tts-1",
    speed: float = 1.0,
) -> Path:
    base = normalize_base_url(base_url)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model or "tts-1",
        "input": text,
        "voice": voice_id,
        "speed": float(speed),
        "response_format": "wav",
    }
    async with httpx.AsyncClient(timeout=90.0, trust_env=False) as client:
        res = await client.post(f"{base}/audio/speech", headers=headers, json=payload)
    if res.status_code >= 400:
        raise VoiceGenerationError(
            f"OpenAI-compatible TTS HTTP {res.status_code}: {res.text[:200]}"
        )
    if not res.content:
        raise VoiceGenerationError("OpenAI-compatible TTS returned empty audio")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(res.content)
    return output_path
