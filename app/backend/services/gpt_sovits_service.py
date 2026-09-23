# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from pathlib import Path

import httpx

from backend.core.exceptions import VoiceGenerationError


def _normalize_gpt_sovits_language(value: str | None) -> str:
    """Match the donor-compatible GPT-SoVITS language contract.

    VideoLingo's pinned GPT-SoVITS adapter supports Chinese and English only.
    Do not silently send Vietnamese or another unsupported language to a local
    clone server and report the resulting garbage/error as an HQ path.
    """
    key = (value or "").strip().lower()
    if key in {"zh", "cn", "chinese", "中文", "all_zh"}:
        return "zh"
    if key in {"en", "english", "英文", "英语"}:
        return "en"
    raise VoiceGenerationError(
        "GPT-SoVITS donor-compatible mode supports Chinese/English only; "
        f"unsupported language: {value or '(empty)'}"
    )


async def generate_voice(
    text: str,
    *,
    output_path: Path,
    base_url: str = "http://127.0.0.1:9880",
    text_language: str = "vi",
    reference_audio: str | None = None,
    prompt_text: str | None = None,
    prompt_language: str | None = None,
    speed: float = 1.0,
    **_,
) -> Path:
    if not reference_audio:
        raise VoiceGenerationError("GPT-SoVITS requires reference audio")
    text_lang = _normalize_gpt_sovits_language(text_language)
    prompt_lang = _normalize_gpt_sovits_language(prompt_language or text_language)
    payload = {
        "text": text,
        "text_lang": text_lang,
        "ref_audio_path": str(reference_audio),
        "prompt_lang": prompt_lang,
        "prompt_text": prompt_text or "",
        "speed_factor": float(speed),
    }
    url = (base_url or "http://127.0.0.1:9880").rstrip("/") + "/tts"
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        res = await client.post(url, json=payload)
    if res.status_code >= 400:
        raise VoiceGenerationError(
            f"GPT-SoVITS HTTP {res.status_code}: {res.text[:200]}"
        )
    if not res.content:
        raise VoiceGenerationError("GPT-SoVITS returned empty audio")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(res.content)
    return output_path
