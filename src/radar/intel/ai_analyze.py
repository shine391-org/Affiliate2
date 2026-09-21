"""
intel/ai_analyze.py — Gemini content analysis for Candidate Intelligence (Phase 3.1)

Uses the OpenAI-compatible endpoint (Gemini 3.5 Flash-Lite) via env config:
  AI_PROVIDER=compatible
  AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
  AI_MODEL=gemini-3.5-flash-lite
  AI_API_KEY (from .env, never logged/committed)

1 request per candidate. Caches intelligence.json per platform+content_id.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")

MAX_TOKENS = 4096
REQUEST_TIMEOUT = 120
MAX_RETRIES = 3

SYSTEM_PROMPT = """You are a short-video content strategist analyzing a Chinese short video (Douyin/Bilibili) that a Vietnamese creator team is considering remaking.

You receive: title, description, source metrics, radar score breakdown, and the full transcript (Chinese).

Return ONLY a valid JSON object (no markdown fences, no commentary) with exactly these fields:

{
  "topic": "...",
  "subtopic": "...",
  "hook": {"text": "...", "type": "...", "first_seconds": 0},
  "content_format": {"type": "...", "presentation_style": "..."},
  "structure": [{"section": "...", "approximate_time": "...", "purpose": "..."}],
  "value_proposition": "...",
  "emotional_trigger": "...",
  "retention_devices": ["..."],
  "cta": "...",
  "why_it_may_work": ["..."],
  "remake_difficulty": "LOW|MEDIUM|HIGH",
  "localization_potential": "LOW|MEDIUM|HIGH",
  "vietnamese_angle": {"concept": "...", "suggested_hook": "...", "notes": "..."},
  "risks": {"copyright_dependency": "LOW|MEDIUM|HIGH", "personality_dependency": "LOW|MEDIUM|HIGH", "product_dependency": "LOW|MEDIUM|HIGH", "context_dependency": "LOW|MEDIUM|HIGH"}
}

Rules:
- Base everything on the transcript and metadata provided. Do NOT invent visual details you cannot observe from the transcript.
- hook.first_seconds = the second mark where the hook lands (number).
- Write analysis values in English; keep quoted Chinese phrases where they matter.
- vietnamese_angle: propose a Vietnamese adaptation concept (English description is fine, Vietnamese phrases welcome)."""


def _ai_config() -> tuple[str, str, str]:
    provider = os.environ.get("AI_PROVIDER", "compatible")
    base_url = os.environ.get("AI_BASE_URL", "").rstrip("/")
    model = os.environ.get("AI_MODEL", "gemini-3.5-flash-lite")
    api_key = os.environ.get("AI_API_KEY", "")
    if provider != "compatible" or not base_url or not api_key:
        raise RuntimeError(
            "AI config missing or provider != compatible. "
            "Set AI_PROVIDER/AI_BASE_URL/AI_MODEL/AI_API_KEY in .env"
        )
    return base_url, api_key, model


def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON object from model reply (tolerates markdown fences)."""
    text = text.strip()
    # strip fences
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        # find first { ... last }
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            text = text[s:e + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def analyze_candidate(
    metadata: dict,
    transcript: Optional[dict],
    out_path: Path,
    force: bool = False,
) -> dict:
    """
    Run content analysis for one candidate.

    Args:
        metadata: candidate metadata (title, metrics, radar scores)
        transcript: transcript dict from transcribe.py (may be None)
        out_path: where intelligence.json is cached
        force: re-run even if cache exists

    Returns:
        {"status": SUCCESS|CACHED|ERROR, "data": ..., "model_stats": {...}}
    """
    if out_path.exists() and not force:
        data = json.loads(out_path.read_text(encoding="utf-8"))
        return {"status": "CACHED", "data": data, "model_stats": {"calls": 0, "retries": 0}}

    base_url, api_key, model = _ai_config()

    # Build user prompt
    lines = []
    lines.append("TITLE: " + (metadata.get("title") or "(none)"))
    if metadata.get("description"):
        lines.append("DESCRIPTION: " + str(metadata.get("description"))[:500])
    m = metadata.get("latest_metrics", {})
    lines.append(
        f"SOURCE METRICS: views={m.get('views')} likes={m.get('likes')} "
        f"comments={m.get('comments')} shares={m.get('shares')} favorites={m.get('favorites')}"
    )
    bd = metadata.get("breakdown", {})
    lines.append(
        f"RADAR SCORE: total={metadata.get('total_score')} confidence={metadata.get('confidence')} "
        f"breakdown={json.dumps(bd, ensure_ascii=False)}"
    )
    if transcript:
        lines.append(f"DETECTED LANGUAGE: {transcript.get('detected_language')}")
        full = transcript.get("full_transcript", "")
        if len(full) > 12000:
            full = full[:6000] + "\n[...middle omitted...]\n" + full[-6000:]
        lines.append("TRANSCRIPT:\n" + full)
    else:
        lines.append("TRANSCRIPT: (unavailable)")

    user_prompt = "\n\n".join(lines)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    calls = 0
    retries = 0
    last_err = None
    for attempt in range(MAX_RETRIES):
        calls += 1
        if attempt > 0:
            retries += 1
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                r = client.post(
                    f"{base_url}/chat/completions", headers=headers, json=payload
                )
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                continue
            content = r.json()["choices"][0]["message"]["content"]
            data = _extract_json(content)
            if data is None:
                last_err = "model reply was not valid JSON"
                continue
            data["_meta"] = {
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
                "model": model,
                "provider": "compatible",
            }
            out_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return {
                "status": "SUCCESS",
                "data": data,
                "model_stats": {"calls": calls, "retries": retries},
            }
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"

    return {
        "status": "ERROR",
        "data": {"error": last_err},
        "model_stats": {"calls": calls, "retries": retries},
    }
