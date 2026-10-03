# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import html
from pathlib import Path

import httpx

from backend.core.exceptions import VoiceGenerationError


def _endpoint(base_url: str | None, region: str, resource: str) -> str:
    root = (base_url or "").rstrip("/")
    if not base_url:
        if not region.strip():
            raise VoiceGenerationError("Azure TTS region is required")
        root = f"https://{region.strip()}.tts.speech.microsoft.com"
    for suffix in ("/cognitiveservices/v1", "/cognitiveservices/voices/list"):
        root = root.removesuffix(suffix)
    return root + "/cognitiveservices/" + resource


async def list_voices(*, api_key: str, region: str = "", base_url: str | None = None) -> list[dict]:
    if not api_key:
        raise VoiceGenerationError("Azure TTS key is required")
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(_endpoint(base_url, region, "voices/list"),
                                    headers={"Ocp-Apim-Subscription-Key": api_key})
    if response.status_code >= 400:
        raise VoiceGenerationError(f"Azure voice catalogue HTTP {response.status_code}")
    return [{"voice_id": row["ShortName"], "gender": row.get("Gender", "unknown"),
             "locale": row.get("Locale", ""), "name": row.get("DisplayName", row["ShortName"])}
            for row in response.json() if isinstance(row, dict) and row.get("ShortName")]


async def generate_voice(
    text: str,
    *,
    voice_id: str,
    api_key: str,
    region: str,
    output_path: Path,
    speed: float = 1.0,
    base_url: str | None = None,
    target_language: str | None = None,
) -> Path:
    if not api_key:
        raise VoiceGenerationError("Azure TTS key is required")
    region = (region or "").strip()
    if not base_url and not region:
        raise VoiceGenerationError("Azure TTS region is required")
    url = _endpoint(base_url, region, "v1")
    rate_pct = round((float(speed) - 1.0) * 100)
    voice_parts = (voice_id or "").split("-")
    locale = "-".join(voice_parts[:2]) if len(voice_parts) >= 2 else "en-US"
    if (target_language or "").lower() in {"vietnamese", "vi", "vi-vn"}:
        locale = "vi-VN"
    spoken = f"<lang xml:lang='{html.escape(locale)}'>{html.escape(text)}</lang>"
    ssml = (
        f"<speak version='1.0' xml:lang='{html.escape(locale)}'>"
        f"<voice name='{html.escape(voice_id)}'>"
        f"<prosody rate='{rate_pct:+d}%'>{spoken}</prosody>"
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
