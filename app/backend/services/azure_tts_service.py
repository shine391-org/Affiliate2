# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import html
from pathlib import Path

import httpx

from backend.core.exceptions import VoiceGenerationError


async def generate_voice(
    text: str,
    *,
    voice_id: str,
    api_key: str,
    region: str,
    output_path: Path,
    speed: float = 1.0,
    base_url: str | None = None,
) -> Path:
    if not api_key:
        raise VoiceGenerationError("Azure TTS key is required")
    region = (region or "").strip()
    if not base_url and not region:
        raise VoiceGenerationError("Azure TTS region is required")
    url = (base_url or f"https://{region}.tts.speech.microsoft.com").rstrip("/")
    if not url.endswith("/cognitiveservices/v1"):
        url += "/cognitiveservices/v1"
    rate_pct = round((float(speed) - 1.0) * 100)
    voice_parts = (voice_id or "").split("-")
    locale = "-".join(voice_parts[:2]) if len(voice_parts) >= 2 else "en-US"
    ssml = (
        f"<speak version='1.0' xml:lang='{html.escape(locale)}'>"
        f"<voice name='{html.escape(voice_id)}'>"
        f"<prosody rate='{rate_pct:+d}%'>{html.escape(text)}</prosody>"
        "</voice></speak>"
    )
    headers = {
        "Ocp-Apim-Subscription-Key": api_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
        "User-Agent": "Affiliate2",
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        res = await client.post(url, headers=headers, content=ssml.encode("utf-8"))
    if res.status_code >= 400:
        raise VoiceGenerationError(f"Azure TTS HTTP {res.status_code}: {res.text[:200]}")
    if not res.content:
        raise VoiceGenerationError("Azure TTS returned empty audio")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(res.content)
    return output_path
