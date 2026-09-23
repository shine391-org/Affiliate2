"""
intel/pipeline.py — Candidate Intelligence pipeline (Phase 3.1)

Flow per candidate (from radar ranking top N):
  1. metadata.json  — radar data + fetch info
  2. source.mp4     — media download (skip if exists)
  3. transcript.json — whisper transcription (skip if exists)
  4. intelligence.json — Gemini analysis (skip if exists unless --force)

Failures mark status and continue; they never block the batch.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .ai_analyze import analyze_candidate_async
from .media import candidate_dir, fetch_media_async
from .transcribe import transcribe_async

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"


async def run_intelligence_async(
    candidates: list[dict],
    force: bool = False,
    verbose: bool = True,
) -> dict:
    """
    Run the full intelligence pipeline over a list of candidates
    (dicts from radar ranking / radar_top20.json format).

    Returns summary dict for reporting.
    """
    summary = {
        "requested": len(candidates),
        "media_downloaded": 0,
        "media_already": 0,
        "media_unavailable": 0,
        "transcripts": 0,
        "transcript_unavailable": 0,
        "intelligence_generated": 0,
        "intelligence_cached": 0,
        "intelligence_errors": 0,
        "model_calls": 0,
        "model_retries": 0,
        "results": [],
    }

    for i, cand in enumerate(candidates, 1):
        platform = cand["platform"]
        content_id = cand["content_id"]
        cdir = candidate_dir(platform, content_id)

        if verbose:
            print(f"  [{i}/{len(candidates)}] {platform}/{content_id}: {str(cand.get('title'))[:40]}")

        entry = {
            "platform": platform,
            "content_id": content_id,
            "title": cand.get("title"),
            "media_status": None,
            "transcript_status": None,
            "intelligence_status": None,
        }

        # --- 1. metadata.json ---
        metadata = {
            "content_id": content_id,
            "platform": platform,
            "title": cand.get("title"),
            "creator_name": cand.get("creator_name"),
            "creator_id": cand.get("creator_id"),
            "canonical_url": cand.get("canonical_url"),
            "publish_time": cand.get("publish_time"),
            "publish_time_iso": cand.get("publish_time_iso"),
            "latest_metrics": cand.get("latest_metrics"),
            "snapshot_count": cand.get("snapshot_count"),
            "radar_total_score": cand.get("total_score"),
            "radar_confidence": cand.get("confidence"),
            "radar_breakdown": cand.get("breakdown"),
            "pipeline_run_at": datetime.now(timezone.utc).isoformat(),
        }

        # --- 2. media ---
        media = await fetch_media_async(platform, content_id, cand.get("canonical_url") or "")
        entry["media_status"] = media["status"]
        if media["status"] == "SUCCESS":
            summary["media_downloaded"] += 1
        elif media["status"] == "ALREADY_EXISTS":
            summary["media_already"] += 1
        else:
            summary["media_unavailable"] += 1

        metadata["media"] = {
            "status": media["status"],
            "meta": media["meta"],
        }
        (cdir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # --- 3. transcript ---
        transcript_data: Optional[dict] = None
        transcript_path = cdir / "transcript.json"
        if media["path"] is not None:
            if transcript_path.exists() and not force:
                transcript_data = json.loads(
                    transcript_path.read_text(encoding="utf-8")
                )
                entry["transcript_status"] = "CACHED"
                summary["transcripts"] += 1
            else:
                tr = await transcribe_async(media["path"], transcript_path)
                entry["transcript_status"] = tr["status"]
                if tr["status"] == "SUCCESS":
                    transcript_data = tr["data"]
                    summary["transcripts"] += 1
                else:
                    summary["transcript_unavailable"] += 1
                    transcript_path.write_text(
                        json.dumps(tr["data"], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
        else:
            entry["transcript_status"] = "SKIPPED_NO_MEDIA"
            summary["transcript_unavailable"] += 1

        # --- 4. intelligence ---
        intel_path = cdir / "intelligence.json"
        ai = await analyze_candidate_async(metadata, transcript_data, intel_path, force=force)
        entry["intelligence_status"] = ai["status"]
        summary["model_calls"] += ai["model_stats"]["calls"]
        summary["model_retries"] += ai["model_stats"]["retries"]
        if ai["status"] == "SUCCESS":
            summary["intelligence_generated"] += 1
        elif ai["status"] == "CACHED":
            summary["intelligence_cached"] += 1
        else:
            summary["intelligence_errors"] += 1

        entry["intelligence"] = ai["data"] if ai["status"] in ("SUCCESS", "CACHED") else None
        summary["results"].append(entry)

        if verbose:
            print(
                f"      media={entry['media_status']} transcript={entry['transcript_status']} "
                f"intelligence={entry['intelligence_status']}"
            )

    return summary



def run_intelligence(
    candidates: list[dict],
    force: bool = False,
    verbose: bool = True,
) -> dict:
    """Sync compatibility entrypoint; one event loop owns the whole batch."""
    return asyncio.run(
        run_intelligence_async(candidates, force=force, verbose=verbose)
    )
