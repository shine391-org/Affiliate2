"""Human-triggered production actions for the Review Queue.

The overlay owns orchestration; ViralMint remains the media engine:
- Translate/Dub reuses ViralMint Whisper, AI translation, Edge TTS, captions, FFmpeg.
- Creative Remake calls the Affiliate2 remake pipeline, which itself reuses ViralMint media/TTS.

Jobs are recorded in ViralMint's public Job model so the existing Activity UI and
restart/zombie handling remain useful. Affiliate2 stores only the job reference.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .review_store import (
    CANDIDATE_STAGE,
    REMAKE_STAGE,
    ReviewStore,
    ReviewValidationError,
    find_project_root,
    utc_now,
)

VALID_VOICES = {
    "vi-VN-NamMinhNeural",
    "vi-VN-HoaiMyNeural",
}
VALID_DURATIONS = {"20-35s", "35-60s"}
VALID_TRANSLATE_MODES = {"full_dub", "captions_only"}


class ActionValidationError(ValueError):
    pass


def _candidate_key(platform: str, content_id: str) -> str:
    return f"{platform}_{content_id}"


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (params or {}).items() if v is not None}


def _normalize_params(action: str, params: dict[str, Any] | None) -> dict[str, Any]:
    p = _clean_params(params)
    voice_id = p.get("voice_id") or "vi-VN-NamMinhNeural"
    if voice_id not in VALID_VOICES:
        raise ActionValidationError(f"Unsupported Vietnamese voice: {voice_id}")
    p["voice_id"] = voice_id

    if action == "translate":
        translate_mode = p.get("translate_mode") or "full_dub"
        if translate_mode not in VALID_TRANSLATE_MODES:
            raise ActionValidationError(f"Unsupported translate mode: {translate_mode}")
        p["translate_mode"] = translate_mode
        p["caption_style"] = "viral"
        p["target_language"] = "Vietnamese"
    elif action == "remake":
        target_duration = p.get("target_duration") or "20-35s"
        if target_duration not in VALID_DURATIONS:
            raise ActionValidationError(f"Unsupported target duration: {target_duration}")
        p["target_duration"] = target_duration
    else:
        raise ActionValidationError(f"Unsupported resolved action: {action}")
    return p


def resolve_requested_action(
    requested_action: str,
    detail: dict,
    params: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], str | None]:
    """Pure validation/normalization for action launch.

    Returns (resolved_action, normalized_params, review_note).
    RERENDER repeats the last successful production mode. Legacy Phase 3.2
    prototypes have no action_manifest, so their natural fallback is remake.
    """
    if requested_action not in {"translate", "remake", "rerender"}:
        raise ActionValidationError(f"Unsupported action: {requested_action}")

    has_final = bool(detail.get("remake_exists"))
    candidate_review = detail.get("candidate_review") or {}
    remake_review = detail.get("remake_review") or {}

    if requested_action in {"translate", "remake"}:
        if has_final:
            raise ActionValidationError(
                "A final video already exists. Mark it REWORK, then use RERENDER."
            )
        if candidate_review.get("status") != "APPROVED_FOR_REMAKE":
            raise ActionValidationError(
                "Candidate must be APPROVED_FOR_REMAKE before production."
            )
        return requested_action, _normalize_params(requested_action, params), None

    # rerender
    if not has_final:
        raise ActionValidationError("RERENDER requires an existing final video.")
    if remake_review.get("status") != "REWORK":
        raise ActionValidationError("RERENDER requires remake status REWORK.")
    note = (remake_review.get("note") or "").strip()
    if not note:
        raise ActionValidationError("RERENDER requires the human REWORK note.")

    manifest = detail.get("action_manifest") or {}
    previous_action = manifest.get("action")
    if previous_action not in {"translate", "remake"}:
        previous_action = "remake"  # Phase 3.2 legacy prototypes

    previous_params = manifest.get("params") or {}
    merged = {**previous_params, **_clean_params(params)}
    return previous_action, _normalize_params(previous_action, merged), note


async def get_job_snapshot(job_id: str | None) -> dict | None:
    if not job_id:
        return None
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if not row:
        return {
            "job_id": job_id,
            "status": "expired",
            "progress_pct": None,
            "current_step": "Job record no longer exists",
            "error_message": None,
            "output": {},
        }

    try:
        output = json.loads(row.output_json or "{}")
    except (TypeError, json.JSONDecodeError):
        output = {}
    try:
        inputs = json.loads(row.input_json or "{}")
    except (TypeError, json.JSONDecodeError):
        inputs = {}

    return {
        "job_id": row.id,
        "job_type": row.job_type,
        "status": row.status,
        "progress_pct": row.progress_pct,
        "current_step": row.current_step,
        "error_message": row.error_message,
        "input": inputs,
        "output": output,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


async def latest_action_snapshot(store: ReviewStore, platform: str, content_id: str) -> dict | None:
    ref = store.latest_action(platform, content_id)
    if not ref:
        return None
    snap = await get_job_snapshot(ref.get("job_id"))
    if snap is None:
        return None
    snap["requested_action"] = ref.get("action")
    snap["params"] = ref.get("params") or {}
    return snap


async def _progress(job_id: str, pct: float, step: str) -> None:
    from backend.agents.job_helper import update_job_status
    from backend.core.ws_manager import ws_manager

    await update_job_status(
        job_id,
        "running",
        progress_pct=pct,
        current_step=step,
    )
    await ws_manager.send_progress(job_id, pct, step, "local")


def _safe_remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _prepare_output_dir(root: Path, candidate_key: str, job_id: str) -> tuple[Path, Path | None]:
    """Move the old output aside so failed rerenders can be rolled back."""
    remakes_root = root / "data" / "remakes"
    remakes_root.mkdir(parents=True, exist_ok=True)
    output_dir = remakes_root / candidate_key
    backup = remakes_root / f".{candidate_key}.backup-{job_id[:8]}"
    _safe_remove_tree(backup)
    if output_dir.exists():
        output_dir.rename(backup)
        return output_dir, backup
    return output_dir, None


def _rollback_output_dir(output_dir: Path, backup: Path | None) -> None:
    _safe_remove_tree(output_dir)
    if backup and backup.exists():
        backup.rename(output_dir)


def _commit_output_dir(backup: Path | None) -> None:
    if backup:
        _safe_remove_tree(backup)


def _write_manifest(
    output_dir: Path,
    *,
    action: str,
    requested_action: str,
    params: dict[str, Any],
    job_id: str,
    review_note: str | None,
) -> Path:
    manifest = {
        "action": action,
        "requested_action": requested_action,
        "params": params,
        "job_id": job_id,
        "review_note": review_note,
        "completed_at": utc_now(),
    }
    path = output_dir / "action_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def _run_translate(
    root: Path,
    platform: str,
    content_id: str,
    output_dir: Path,
    params: dict[str, Any],
    job_id: str,
) -> Path:
    source = root / "data" / "candidates" / _candidate_key(platform, content_id) / "source.mp4"
    if not source.is_file():
        raise FileNotFoundError(f"Source video not found: {source}")

    work = output_dir / ".work"
    work.mkdir(parents=True, exist_ok=True)

    await _progress(job_id, 8, "Transcribing source audio...")
    from backend.services.whisper_service import whisper_service, job_download_notice

    tx = await whisper_service.transcribe(
        str(source),
        on_download=job_download_notice(job_id, "local"),
    )
    segments = tx.get("segments", [])
    if not segments:
        raise RuntimeError("No speech detected — nothing to translate.")

    await _progress(job_id, 32, "Translating to Vietnamese...")
    # Reuse ViralMint's resilient batching contract rather than reimplementing it.
    from backend.core.tool_runners import _load_user_settings, _translate_segments

    user_settings = await _load_user_settings("local")
    translated = await _translate_segments(segments, "Vietnamese", user_settings)
    (output_dir / "translation.json").write_text(
        json.dumps({"segments": translated}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    caption_input = source
    if params["translate_mode"] == "full_dub":
        await _progress(job_id, 56, "Generating Vietnamese voice...")
        full_text = " ".join(s.get("text", "") for s in translated).strip()
        if not full_text:
            raise RuntimeError("Translation produced empty Vietnamese text.")

        from backend.services.edge_tts_service import generate_voice
        from backend.services.ffmpeg_service import add_audio_to_video

        audio = work / "dub.mp3"
        dubbed = work / "dubbed.mp4"
        await generate_voice(full_text, voice_id=params["voice_id"], output_path=audio)

        await _progress(job_id, 72, "Muxing Vietnamese voice...")
        await add_audio_to_video(source, audio, dubbed)
        caption_input = dubbed

    await _progress(job_id, 86, "Burning Vietnamese captions...")
    from backend.services.caption_service import burn_captions, generate_captions_ass
    from backend.services.video_utils import aspect_label

    aspect = await asyncio.to_thread(aspect_label, caption_input)
    ass_path = await generate_captions_ass(
        translated,
        style="viral",
        aspect_ratio=aspect,
        emoji_style="moderate",
    )
    staged = work / "final.mp4"
    try:
        await burn_captions(caption_input, ass_path, staged)
    finally:
        try:
            Path(ass_path).unlink(missing_ok=True)
        except OSError:
            pass

    if not staged.is_file() or staged.stat().st_size < 10_000:
        raise RuntimeError("Translate/Dub produced no usable final video.")

    final_path = output_dir / "final.mp4"
    os.replace(staged, final_path)
    _safe_remove_tree(work)
    return final_path


async def _run_creative_remake(
    root: Path,
    platform: str,
    content_id: str,
    params: dict[str, Any],
    review_note: str | None,
    job_id: str,
) -> Path:
    src_path = root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))

    await _progress(job_id, 12, "Building Vietnamese creative adaptation...")
    from radar.remake.pipeline import run_remake_pipeline

    result = await run_remake_pipeline(
        candidate_id=_candidate_key(platform, content_id),
        target_duration=params["target_duration"],
        voice_id=params["voice_id"],
        use_cache=False,
        review_note=review_note,
    )
    if not result.success or not result.final_path:
        reason = "; ".join(result.errors or result.warnings or ["unknown remake failure"])
        raise RuntimeError(f"Creative remake failed: {reason}")

    # Intermediate media is useful while rendering, not after human review begins.
    output_dir = Path(result.final_path).parent
    keep = {"final.mp4", "adaptation.json", "segment_plan.json", "action_manifest.json"}
    for p in output_dir.iterdir():
        if p.is_file() and p.name not in keep and p.suffix.lower() in {".mp4", ".mp3", ".wav"}:
            try:
                p.unlink()
            except OSError:
                pass
    return Path(result.final_path)


async def _execute_action(
    *,
    job_id: str,
    store: ReviewStore,
    platform: str,
    content_id: str,
    requested_action: str,
    resolved_action: str,
    params: dict[str, Any],
    review_note: str | None,
) -> None:
    from backend.agents.job_helper import update_job_status
    from backend.core.ws_manager import ws_manager

    root = store.root
    key = _candidate_key(platform, content_id)
    output_dir, backup = _prepare_output_dir(root, key, job_id)

    try:
        output_dir.mkdir(parents=True, exist_ok=True)

        if resolved_action == "translate":
            final_path = await _run_translate(
                root, platform, content_id, output_dir, params, job_id
            )
        else:
            final_path = await _run_creative_remake(
                root, platform, content_id, params, review_note, job_id
            )

        manifest_path = _write_manifest(
            output_dir,
            action=resolved_action,
            requested_action=requested_action,
            params=params,
            job_id=job_id,
            review_note=review_note,
        )
        _commit_output_dir(backup)

        # Production always returns to human review; never auto-approve.
        store.set_decision(platform, content_id, REMAKE_STAGE, "REMAKE_REVIEW", None)

        await update_job_status(
            job_id,
            "success",
            progress_pct=100,
            current_step="Ready for human review",
            output_data={
                "file": str(final_path),
                "manifest": str(manifest_path),
                "platform": platform,
                "content_id": content_id,
                "action": resolved_action,
            },
        )
        await ws_manager.send(
            {
                "type": "job_complete",
                "job_id": job_id,
                "result": {
                    "review_url": "/review",
                    "platform": platform,
                    "content_id": content_id,
                },
            },
            "local",
        )
    except Exception as exc:
        _rollback_output_dir(output_dir, backup)
        await update_job_status(
            job_id,
            "failed",
            current_step="Action failed",
            error_message=str(exc),
        )
        await ws_manager.send(
            {"type": "job_failed", "job_id": job_id, "error": str(exc)},
            "local",
        )


async def launch_action(
    store: ReviewStore,
    platform: str,
    content_id: str,
    requested_action: str,
    params: dict[str, Any] | None = None,
) -> dict:
    """Validate human state, create a ViralMint Job, then dispatch production."""
    detail = store.get_candidate_detail(platform, content_id)
    if not detail.get("source_exists"):
        raise ActionValidationError("Candidate has no local source.mp4.")

    latest = await latest_action_snapshot(store, platform, content_id)
    if latest and latest.get("status") in {"pending", "running"}:
        raise ActionValidationError(
            f"An action is already {latest['status']} for this candidate."
        )

    resolved_action, normalized_params, review_note = resolve_requested_action(
        requested_action,
        detail,
        params,
    )

    from backend.agents.job_helper import create_job
    from backend.core.task_runner import spawn_background

    job = await create_job(
        f"content:{resolved_action}",
        "local",
        {
            "content_engine_action": requested_action,
            "resolved_action": resolved_action,
            "platform": platform,
            "content_id": content_id,
            **normalized_params,
        },
    )
    store.record_action(
        job.id,
        platform,
        content_id,
        requested_action,
        {
            **normalized_params,
            "resolved_action": resolved_action,
        },
    )

    spawn_background(
        _execute_action(
            job_id=job.id,
            store=store,
            platform=platform,
            content_id=content_id,
            requested_action=requested_action,
            resolved_action=resolved_action,
            params=normalized_params,
            review_note=review_note,
        )
    )

    return {
        "job_id": job.id,
        "status": "pending",
        "requested_action": requested_action,
        "resolved_action": resolved_action,
        "params": normalized_params,
    }
