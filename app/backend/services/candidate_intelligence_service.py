# SPDX-License-Identifier: AGPL-3.0-only
"""Canonical Candidate Intelligence media / ASR / AI services.

Phase C12 convergence target: legacy src/radar/intel keeps only thin CLI
compatibility wrappers. Downloading, transcription and AI provider selection
live in the canonical application layer so Radar intelligence, Library analysis
and later Smart Cut share the same quality/provider contracts.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUPPORTED_RADAR_PLATFORMS = {"douyin", "bilibili"}
_SOURCE_VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv")
MAX_TOKENS = 4096
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


def extract_candidate_json(text: str) -> dict | None:
    """Extract one JSON object from a provider reply without provider coupling."""
    raw = (text or "").strip()
    fence = chr(96) * 3
    if raw.startswith(fence):
        lines = raw.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith(fence):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _existing_source(output_dir: Path) -> Path | None:
    for ext in _SOURCE_VIDEO_EXTS:
        path = output_dir / f"source{ext}"
        if path.is_file() and path.stat().st_size > 10_000:
            return path
    candidates = [
        path for path in output_dir.glob("source.*")
        if path.suffix.lower() in _SOURCE_VIDEO_EXTS
        and path.is_file()
        and path.stat().st_size > 10_000
    ]
    return max(candidates, key=lambda p: p.stat().st_size) if candidates else None


async def fetch_candidate_media(
    platform: str,
    content_id: str,
    canonical_url: str,
    output_dir: Path,
) -> dict:
    """Fetch Radar candidate media through the canonical yt-dlp service."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    existing = _existing_source(output_dir)
    if existing is not None:
        from backend.services.video_utils import probe_media_details
        return {
            "status": "ALREADY_EXISTS",
            "path": existing,
            "meta": {
                "size": existing.stat().st_size,
                "media": probe_media_details(existing),
                "source": "canonical_ytdlp",
            },
        }

    platform = (platform or "").strip().lower()
    if platform not in SUPPORTED_RADAR_PLATFORMS:
        return {
            "status": "MEDIA_UNAVAILABLE",
            "path": None,
            "meta": {"reason": f"unsupported Radar platform: {platform or '(empty)}"},
        }
    if not (canonical_url or "").strip():
        return {
            "status": "MEDIA_UNAVAILABLE",
            "path": None,
            "meta": {"reason": "candidate has no canonical_url"},
        }

    try:
        from backend.services.ytdlp_service import download_video

        result = await download_video(
            url=canonical_url.strip(),
            output_dir=output_dir,
            filename="source",
            extract_audio=False,
            options={"quality": "best"},
        )
        video_path = Path(result.get("video_path") or "")
        if not video_path.is_file() or video_path.stat().st_size <= 10_000:
            raise RuntimeError("canonical downloader returned no usable video artifact")
        return {
            "status": "SUCCESS",
            "path": video_path,
            "meta": {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source": "canonical_ytdlp",
                "size": video_path.stat().st_size,
                "duration": result.get("duration"),
                "height": result.get("height"),
                "media": result.get("media_metadata") or {},
                "requested_quality": result.get("requested_quality") or "best",
            },
        }
    except Exception as exc:
        return {
            "status": "MEDIA_UNAVAILABLE",
            "path": None,
            "meta": {"reason": f"{type(exc).__name__}: {exc}", "source": "canonical_ytdlp"},
        }


async def transcribe_candidate(
    media_path: Path,
    out_path: Path,
    *,
    quality: str = "balanced",
    model_name: str | None = None,
) -> dict:
    """Transcribe candidate media through the canonical Whisper service."""
    try:
        from backend.services.video_utils import probe_duration
        from backend.services.whisper_service import (
            resolve_whisper_model,
            whisper_service,
        )

        result = await whisper_service.transcribe(
            media_path,
            quality=quality,
            model_name=model_name,
        )
        duration_seconds = await asyncio.to_thread(
            probe_duration, media_path, 0.0
        )
        data = {
            "transcribed_at": datetime.now(timezone.utc).isoformat(),
            "model": f"faster-whisper/{resolve_whisper_model(quality, model_name)}",
            "detected_language": result.get("language"),
            "language_probability": result.get("language_probability"),
            "duration": round(float(duration_seconds), 3),
            "segments": result.get("segments") or [],
            "full_transcript": (result.get("text") or "").strip(),
        }
        _atomic_json(Path(out_path), data)
        return {"status": "SUCCESS", "data": data}
    except Exception as exc:
        return {
            "status": "ERROR",
            "data": {"error": f"{type(exc).__name__}: {exc}"},
        }


def _prompt_for_candidate(metadata: dict, transcript: dict | None) -> str:
    metrics = metadata.get("latest_metrics") or {}
    radar_score = metadata.get("radar_total_score", metadata.get("total_score"))
    radar_confidence = metadata.get("radar_confidence", metadata.get("confidence"))
    breakdown = metadata.get("radar_breakdown", metadata.get("breakdown")) or {}

    lines = ["TITLE: " + str(metadata.get("title") or "(none)")]
    if metadata.get("description"):
        lines.append("DESCRIPTION: " + str(metadata["description"])[:500])
    lines.append(
        "SOURCE METRICS: "
        f"views={metrics.get('views')} likes={metrics.get('likes')} "
        f"comments={metrics.get('comments')} shares={metrics.get('shares')} "
        f"favorites={metrics.get('favorites')}"
    )
    lines.append(
        f"RADAR SCORE: total={radar_score} confidence={radar_confidence} "
        f"breakdown={json.dumps(breakdown, ensure_ascii=False)}"
    )

    if transcript:
        language = transcript.get("detected_language", transcript.get("language"))
        full = str(transcript.get("full_transcript", transcript.get("text", "")) or "")
        if len(full) > 12_000:
            full = full[:6_000] + "\n[...middle omitted...]\n" + full[-6_000:]
        lines.append(f"DETECTED LANGUAGE: {language}")
        lines.append("TRANSCRIPT:\n" + full)
    else:
        lines.append("TRANSCRIPT: (unavailable)")
    return "\n\n".join(lines)


async def analyze_candidate_intelligence(
    metadata: dict,
    transcript: dict | None,
    out_path: Path,
    *,
    force: bool = False,
    user_settings=None,
) -> dict:
    """Analyze one candidate through the canonical AI provider abstraction."""
    out_path = Path(out_path)
    if out_path.exists() and not force:
        try:
            cached = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                return {
                    "status": "CACHED",
                    "data": cached,
                    "model_stats": {"calls": 0, "retries": 0},
                }
        except (OSError, json.JSONDecodeError):
            pass

    try:
        from backend.core.ai_provider import get_ai_client
        ai = get_ai_client(user_settings)
    except Exception as exc:
        return {
            "status": "ERROR",
            "data": {"error": f"{type(exc).__name__}: {exc}"},
            "model_stats": {"calls": 0, "retries": 0},
        }

    prompt = _prompt_for_candidate(metadata, transcript)
    calls = 0
    retries = 0
    last_error = None
    for attempt in range(MAX_RETRIES):
        calls += 1
        if attempt:
            retries += 1
        try:
            response = await ai.chat(
                messages=[{"role": "user", "content": prompt}],
                system=SYSTEM_PROMPT,
                max_tokens=MAX_TOKENS,
            )
            data = extract_candidate_json(response)
            if data is None:
                last_error = "model reply was not valid JSON"
                continue
            provider = getattr(ai, "provider", None)
            data["_meta"] = {
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
                "model": getattr(ai, "model", None),
                "provider": getattr(provider, "value", str(provider or "")),
                "pipeline": "canonical_candidate_intelligence_v1",
            }
            _atomic_json(out_path, data)
            return {
                "status": "SUCCESS",
                "data": data,
                "model_stats": {"calls": calls, "retries": retries},
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

    return {
        "status": "ERROR",
        "data": {"error": last_error},
        "model_stats": {"calls": calls, "retries": retries},
    }
