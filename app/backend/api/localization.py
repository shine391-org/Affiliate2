# SPDX-License-Identifier: AGPL-3.0-only
"""Phase-C localization control plane: batch, resume, QC exceptions, expert overrides."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.config import settings
from backend.core.localization import LocalizationProfile, LocalizationTranscript
from backend.services.localization_stage_service import (
    MANIFEST_VERSION,
    LocalizationRun,
    STAGES,
)

router = APIRouter(prefix="/localization", tags=["localization"])


class SegmentOverride(BaseModel):
    id: str
    translated_text: str | None = None
    target_start_ms: int | None = Field(default=None, ge=0)
    target_end_ms: int | None = Field(default=None, ge=0)
    voice_id: str | None = None
    speaker_id: str | None = None
    clear_timing: bool = False
    clear_voice: bool = False


class ExpertOverrideRequest(BaseModel):
    segments: list[SegmentOverride] = Field(default_factory=list)
    profile_updates: dict[str, Any] = Field(default_factory=dict)


class RerunRequest(BaseModel):
    from_stage: Literal[
        "separation", "asr", "semantic", "translation", "dubbing",
        "audio_mix", "final_render", "qc",
    ] | None = None


class RenderRerunRequest(BaseModel):
    target_aspect: Literal["source", "9:16", "16:9", "1:1"] | None = None
    mask_box: dict[str, Any] | None = None
    caption_placement: dict[str, Any] | None = None


_ASR_KEYS = {
    "asr_provider", "asr_model", "asr_quality",
    "alignment_mode", "diarization_mode", "diarization_speakers",
}
_SEMANTIC_KEYS = {"semantic_segmentation"}
_TRANSLATION_KEYS = {
    "translation_provider", "translation_model", "translation_base_url",
    "translation_preset", "translation_instructions", "translation_guardrails", "glossary",
}
_DUBBING_KEYS = {
    "rewrite_provider", "rewrite_model", "rewrite_base_url", "rewrite_guardrails", "rewrite_instructions",
    "tts_provider", "tts_model", "tts_voice", "tts_base_url", "tts_region",
    "speaker_voices", "speaker_reference_audio", "tts_reference_audio",
    "tts_reference_text", "tts_reference_language", "voice_clone_reference_mode",
    "dub_min_cue_duration_ms",
    "dub_max_chunk_size", "dub_gap_tolerance_ms", "dub_speed_accept",
    "dub_speed_max", "dub_rewrite_attempts", "video_retime_mode",
    "video_retime_max_slowdown",
}
_AUDIO_KEYS = {"audio_mode", "preserve_background", "background_gain_db", "dub_gain_db"}
_RENDER_KEYS = {"caption_style", "target_aspect", "render_profile"}
_QC_KEYS = {"qc_profile"}


def _load_run(job_id: str) -> LocalizationRun:
    try:
        return LocalizationRun.load(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Localization run not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _assert_job_inactive(job_id: str) -> None:
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job

    async with AsyncSessionLocal() as db:
        status = (await db.execute(
            select(Job.status).where(Job.id == job_id)
        )).scalar_one_or_none()
    if status is None:
        raise HTTPException(status_code=404, detail="Job row not found")
    if status in {"running", "pending"}:
        raise HTTPException(
            status_code=409,
            detail="Localization job is already active; wait for it to finish before editing or rerunning.",
        )


def _earliest_stage(stages: list[str]) -> str:
    return min(stages, key=STAGES.index)


def _profile_invalidation(keys: set[str]) -> str | None:
    stages: list[str] = []
    if keys & _ASR_KEYS:
        stages.append("asr")
    if keys & _SEMANTIC_KEYS:
        stages.append("semantic")
    if keys & _TRANSLATION_KEYS:
        stages.append("translation")
    if keys & _DUBBING_KEYS:
        stages.append("dubbing")
    if keys & _AUDIO_KEYS:
        stages.append("audio_mix")
    if keys & _RENDER_KEYS:
        stages.append("final_render")
    if keys & _QC_KEYS:
        stages.append("qc")
    return _earliest_stage(stages) if stages else None


def _optional_json(run: LocalizationRun, stage: str, key: str):
    try:
        return run.load_json(stage, key)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _job_detail(run: LocalizationRun) -> dict:
    translation = _optional_json(run, "translation", "transcript")
    dubbing_tx = _optional_json(run, "dubbing", "transcript")
    plan = _optional_json(run, "dubbing", "plan")
    chunks = _optional_json(run, "dubbing", "chunks")
    qc = _optional_json(run, "qc", "report")
    request = run.request
    return {
        "manifest": run.snapshot(),
        "transcript": dubbing_tx or translation,
        "plan": plan or [],
        "chunks": chunks or [],
        "qc": qc,
        "translation_review_pending": bool(
            request.get("translation_review_pending")
            and not request.get("translation_review_approved")
        ),
    }


async def _dispatch_existing_run(run: LocalizationRun):
    from backend.agents.job_helper import restart_job
    from backend.core.task_runner import dispatch
    from backend.core.tool_runners import run_tool_translate

    if not run.source_path.exists():
        raise HTTPException(status_code=409, detail="Persisted source media is missing")
    try:
        run.verify_source_identity()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not await restart_job(run.job_id):
        raise HTTPException(
            status_code=409,
            detail="Localization job became active before the rerun could be claimed.",
        )

    req = run.request
    effective = run.effective_profile
    dispatch(run_tool_translate(
        run.job_id,
        run.source_path,
        str(req.get("target_language") or "Vietnamese"),
        str(req.get("mode") or "full_dub"),
        str(req.get("voice_id") or ""),
        str(effective.get("caption_style") or req.get("caption_style") or "viral"),
        str(req.get("emoji_style") or "moderate"),
        target_aspect=effective.get("target_aspect") or req.get("target_aspect"),
        profile_id=req.get("profile_id"),
        profile_override=effective,
        review_translation=bool(req.get("review_translation")),
        cleanup_input=False,
        mask_box=req.get("mask_box"),
        caption_placement=req.get("caption_placement"),
        downloaded_video_id=req.get("downloaded_video_id"),
    ))


@router.get("/jobs/{job_id}")
async def localization_job_detail(job_id: str):
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job

    async with AsyncSessionLocal() as db:
        job = (await db.execute(
            select(Job).where(Job.id == job_id)
        )).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Localization job not found")

    try:
        run = LocalizationRun.load(job_id)
    except ValueError:
        # A runner can fail before it creates a manifest (profile resolution,
        # source materialization, etc.). The canonical Job row still owns
        # lifecycle state, so return it instead of making Studio believe the
        # job is perpetually loading/active.
        return {
            "manifest": None,
            "transcript": None,
            "plan": [],
            "chunks": [],
            "qc": None,
            "translation_review_pending": False,
            "job_status": job.status,
            "job_current_step": job.current_step,
            "job_error_message": job.error_message,
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    detail = _job_detail(run)
    detail["job_status"] = job.status
    detail["job_current_step"] = job.current_step
    detail["job_error_message"] = job.error_message
    return detail


@router.get("/exceptions")
async def localization_exceptions(limit: int = 50):
    root = settings.STORAGE_ROOT / "localization_jobs"
    if not root.exists():
        return {"total": 0, "items": []}
    items = []
    for manifest in root.glob("*/manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue
        if int(data.get("version") or 0) != MANIFEST_VERSION:
            continue
        stages = data.get("stages") or {}
        qc = stages.get("qc") or {}
        qc_status = (qc.get("metadata") or {}).get("status")
        failed = [
            {"stage": name, "error": rec.get("error")}
            for name, rec in stages.items()
            if rec.get("status") == "failed"
        ]
        if qc_status in {"FAILED", "NEEDS_REVIEW"} or failed:
            qc_report = None
            try:
                qc_report = _optional_json(LocalizationRun.load(str(data.get("job_id"))), "qc", "report")
            except (ValueError, RuntimeError):
                pass
            items.append({
                "job_id": data.get("job_id"),
                "updated_at": data.get("updated_at"),
                "qc_status": qc_status,
                "failed_stages": failed,
                "qc_failures": (qc_report or {}).get("failures") or [],
                "qc_warnings": (qc_report or {}).get("warnings") or [],
                "request": data.get("request") or {},
            })
    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    items = items[: max(1, min(limit, 200))]
    return {"total": len(items), "items": items}


@router.post("/jobs/{job_id}/override")
async def localization_expert_override(job_id: str, body: ExpertOverrideRequest):
    await _assert_job_inactive(job_id)
    run = _load_run(job_id)
    invalidations: list[str] = []

    if body.profile_updates:
        merged = {**run.effective_profile, **body.profile_updates}
        try:
            profile = LocalizationProfile.model_validate(merged)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid profile override: {exc}") from exc
        stage = _profile_invalidation(set(body.profile_updates))
        if body.segments and stage in {"separation", "asr", "semantic", "translation"}:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot combine upstream profile changes with segment overrides "
                    "because the segment IDs may be regenerated. Apply the profile "
                    "change and rerun first, then edit the resulting segments."
                ),
            )
        run.update_effective_profile(profile.model_dump())
        if stage:
            invalidations.append(stage)

    if body.segments:
        raw = _optional_json(run, "translation", "transcript")
        if not raw:
            raise HTTPException(
                status_code=409,
                detail="Translation artifact is unavailable; rerun through translation first",
            )
        transcript = LocalizationTranscript.model_validate(raw)
        by_id = {seg.id: seg for seg in transcript.segments}

        for change in body.segments:
            seg = by_id.get(change.id)
            if seg is None:
                raise HTTPException(status_code=404, detail=f"Segment not found: {change.id}")

            if change.translated_text is not None:
                value = change.translated_text.strip()
                if not value:
                    raise HTTPException(status_code=400, detail=f"Empty translation: {change.id}")
                seg.translated_text = value
                seg.translation_status = "success"
                seg.qc_status = "pending"

            if change.speaker_id is not None:
                seg.speaker_id = change.speaker_id.strip() or None

            overrides = dict(seg.overrides or {})
            if change.clear_timing:
                overrides.pop("target_start_ms", None)
                overrides.pop("target_end_ms", None)
            elif change.target_start_ms is not None or change.target_end_ms is not None:
                if change.target_start_ms is None or change.target_end_ms is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Both target_start_ms and target_end_ms are required: {change.id}",
                    )
                if change.target_end_ms <= change.target_start_ms:
                    raise HTTPException(
                        status_code=400,
                        detail=f"target_end_ms must be > target_start_ms: {change.id}",
                    )
                overrides["target_start_ms"] = change.target_start_ms
                overrides["target_end_ms"] = change.target_end_ms

            if change.clear_voice:
                overrides.pop("voice_id", None)
            elif change.voice_id is not None:
                voice = change.voice_id.strip()
                if voice:
                    overrides["voice_id"] = voice

            seg.overrides = overrides

        path = run.save_json("translation", "transcript.json", transcript.model_dump())
        old_meta = dict(run.record("translation").get("metadata") or {})
        old_meta["expert_override"] = True
        run.complete(
            "translation",
            artifacts={"transcript": path},
            metadata=old_meta,
        )
        invalidations.append("dubbing")

    if invalidations:
        stage = _earliest_stage(invalidations)
        # Keep manually edited translation when the earliest invalidation is
        # downstream; earlier profile changes intentionally recompute it.
        run.invalidate_from(stage, delete_artifacts=True)
    else:
        stage = None

    return {
        "ok": True,
        "invalidated_from": stage,
        "effective_profile": run.effective_profile,
    }


@router.post("/jobs/{job_id}/continue-after-translation")
async def continue_after_translation_review(job_id: str):
    """Approve the persisted C6 translation and continue the SAME run at C7."""
    await _assert_job_inactive(job_id)
    run = _load_run(job_id)
    if not run.is_complete("translation", ("transcript",)):
        raise HTTPException(
            status_code=409,
            detail="Translation is not complete; there is nothing to approve yet.",
        )
    req = run.request
    if not req.get("review_translation"):
        raise HTTPException(
            status_code=409,
            detail="This localization job was not configured for translation review.",
        )
    run.update_request({
        **req,
        "translation_review_approved": True,
        "translation_review_pending": False,
    })
    await _dispatch_existing_run(run)
    return {"ok": True, "job_id": job_id, "from_stage": "dubbing"}


@router.post("/jobs/{job_id}/chunks/{chunk_id}/regenerate")
async def regenerate_localization_chunk(job_id: str, chunk_id: int):
    await _assert_job_inactive(job_id)
    run = _load_run(job_id)
    chunks = _optional_json(run, "dubbing", "chunks") or []
    ids = {int(row.get("id")) for row in chunks if isinstance(row, dict) and row.get("id") is not None}
    if chunk_id not in ids:
        raise HTTPException(status_code=404, detail="Dubbing chunk not found")

    run.update_request({
        **run.request,
        "regenerate_chunk_ids": [chunk_id],
    })
    # Preserve raw chunk files so every non-target chunk can be reused.
    run.invalidate_from("dubbing", delete_artifacts=False)
    await _dispatch_existing_run(run)
    return {"ok": True, "job_id": job_id, "regenerating_chunk": chunk_id}


@router.post("/jobs/{job_id}/rerun")
async def rerun_localization_job(job_id: str, body: RerunRequest):
    await _assert_job_inactive(job_id)
    run = _load_run(job_id)
    stage = body.from_stage or run.first_incomplete_stage() or "qc"
    preserve_files = {"checkpoint.json"} if body.from_stage is None and stage == "translation" else set()
    run.invalidate_from(stage, delete_artifacts=True, preserve_files=preserve_files)
    if stage == "separation":
        # This is the explicit recovery path for an intentionally replaced
        # source. All source-derived artifacts were invalidated above, so the
        # current bytes can safely become the new canonical identity.
        run.refresh_source_identity()
    await _dispatch_existing_run(run)
    return {"ok": True, "job_id": job_id, "from_stage": stage}


@router.post("/jobs/{job_id}/rerender")
async def rerender_localization_job(job_id: str, body: RenderRerunRequest):
    await _assert_job_inactive(job_id)

    provided = body.model_fields_set
    explicit_mask = body.mask_box if "mask_box" in provided else None
    if explicit_mask is not None:
        if explicit_mask.get("normalized") is not True:
            raise HTTPException(status_code=400, detail="Mask box must explicitly set normalized=true")
        from backend.services.caption_service import parse_and_validate_mask_box

        if parse_and_validate_mask_box(explicit_mask, 1000, 1000) is None:
            raise HTTPException(status_code=400, detail="Mask box geometry is invalid")

    run = _load_run(job_id)
    mask_box = (
        body.mask_box
        if "mask_box" in provided
        else run.request.get("mask_box")
    )
    if mask_box is not None and "mask_box" not in provided:
        if mask_box.get("normalized") is not True:
            raise HTTPException(status_code=400, detail="Mask box must explicitly set normalized=true")
        from backend.services.caption_service import parse_and_validate_mask_box

        if parse_and_validate_mask_box(mask_box, 1000, 1000) is None:
            raise HTTPException(status_code=400, detail="Mask box geometry is invalid")

    placement = dict(
        body.caption_placement
        if "caption_placement" in provided
        else run.request.get("caption_placement") or {}
    )
    mode = str(placement.get("mode") or "default").strip().lower()
    if mode not in {"default", "mask_region"}:
        raise HTTPException(status_code=400, detail="Caption placement mode is invalid")
    try:
        vertical_offset = float(placement.get("vertical_offset", 0.0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Caption vertical offset must be numeric") from exc
    if not -0.25 <= vertical_offset <= 0.25:
        raise HTTPException(status_code=400, detail="Caption vertical offset must be between -0.25 and 0.25")
    if mode == "mask_region" and mask_box is None:
        raise HTTPException(status_code=400, detail="Mask-region caption placement requires an enabled mask")
    placement = {"mode": mode, "vertical_offset": vertical_offset}

    request = dict(run.request)
    if "mask_box" in provided:
        request["mask_box"] = mask_box
    if "caption_placement" in provided:
        request["caption_placement"] = placement
    if "target_aspect" in provided and body.target_aspect:
        request["target_aspect"] = body.target_aspect
    run.update_request(request)
    if "target_aspect" in provided and body.target_aspect:
        run.update_effective_profile({
            **run.effective_profile,
            "target_aspect": body.target_aspect,
        })
    run.invalidate_from("final_render", delete_artifacts=True, preserve_files=set())
    await _dispatch_existing_run(run)
    return {"ok": True, "job_id": job_id, "from_stage": "final_render"}


async def _run_localization_batch_sequential(specs: list[dict]) -> None:
    """Run HQ localization jobs one-by-one to avoid loading Demucs/WhisperX N times.

    The normal per-job runner is reused verbatim; this is only a lightweight
    coordinator and intentionally bypasses the generic max-3 heavy-task fanout.
    """
    from backend.core.tool_runners import run_tool_translate

    for spec in specs:
        await run_tool_translate(**spec)


@router.post("/batch")
async def localization_batch(
    files: list[UploadFile] = File(...),
    target_language: str = Form("vi"),
    mode: Literal["captions_only", "full_dub"] = Form("full_dub"),
    voice_id: str = Form(""),
    caption_style: str = Form("viral"),
    emoji_style: str = Form("moderate"),
    target_aspect: str | None = Form(None),
    profile_id: str | None = Form(None),
):
    """C9 batch = one proven localization runner applied to N source files."""
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    if len(files) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 videos per batch")

    from backend.agents.job_helper import create_job
    from backend.api.tools import VIDEO_EXTS, VIDEO_MAX_BYTES, save_upload
    from backend.core.task_runner import dispatch

    jobs = []
    specs = []
    for file in files:
        job = await create_job("tool:translate", "local", {
            "target_language": target_language,
            "mode": mode,
            "voice_id": voice_id if mode == "full_dub" else None,
            "caption_style": caption_style,
            "target_aspect": target_aspect,
            "profile_id": profile_id,
            "batch": True,
            "filename": file.filename,
        })
        in_path = await save_upload(file, job.id, VIDEO_EXTS, VIDEO_MAX_BYTES)
        specs.append({
            "job_id": job.id,
            "in_path": in_path,
            "target_language": target_language,
            "mode": mode,
            "voice_id": voice_id,
            "caption_style": caption_style,
            "emoji_style": emoji_style,
            "target_aspect": target_aspect,
            "profile_id": profile_id,
        })
        jobs.append({"job_id": job.id, "filename": file.filename})

    # One coordinator consumes one global heavy-task slot; jobs inside it are
    # strictly sequential, so a 20-video batch cannot fan out 20 Demucs models.
    dispatch(_run_localization_batch_sequential(specs))
    return {"count": len(jobs), "jobs": jobs}
