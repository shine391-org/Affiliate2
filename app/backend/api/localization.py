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
from backend.services.localization_stage_service import LocalizationRun, STAGES

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


_ASR_KEYS = {
    "asr_provider", "asr_model", "asr_quality",
    "alignment_mode", "diarization_mode", "diarization_speakers",
}
_SEMANTIC_KEYS = {"semantic_segmentation"}
_TRANSLATION_KEYS = {
    "translation_provider", "translation_model", "translation_preset",
    "translation_instructions", "glossary",
}
_DUBBING_KEYS = {
    "tts_provider", "tts_model", "tts_voice", "speaker_voices",
    "speaker_reference_audio", "tts_reference_audio", "tts_reference_text",
    "tts_reference_language", "dub_min_cue_duration_ms",
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
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="Localization run not found") from exc


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
    return {
        "manifest": run.snapshot(),
        "transcript": dubbing_tx or translation,
        "plan": plan or [],
        "chunks": chunks or [],
        "qc": qc,
    }


async def _dispatch_existing_run(run: LocalizationRun):
    from backend.agents.job_helper import restart_job
    from backend.core.task_runner import dispatch
    from backend.core.tool_runners import run_tool_translate

    if not run.source_path.exists():
        raise HTTPException(status_code=409, detail="Persisted source media is missing")
    if not await restart_job(run.job_id):
        raise HTTPException(status_code=404, detail="Job row not found")

    req = run.request
    dispatch(run_tool_translate(
        run.job_id,
        run.source_path,
        str(req.get("target_language") or "Vietnamese"),
        str(req.get("mode") or "full_dub"),
        str(req.get("voice_id") or ""),
        str(req.get("caption_style") or "viral"),
        str(req.get("emoji_style") or "moderate"),
        target_aspect=req.get("target_aspect"),
        profile_id=req.get("profile_id"),
        profile_override=run.effective_profile,
    ))


@router.get("/jobs/{job_id}")
async def localization_job_detail(job_id: str):
    return _job_detail(_load_run(job_id))


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
        stages = data.get("stages") or {}
        qc = stages.get("qc") or {}
        qc_status = (qc.get("metadata") or {}).get("status")
        failed = [
            {"stage": name, "error": rec.get("error")}
            for name, rec in stages.items()
            if rec.get("status") == "failed"
        ]
        if qc_status in {"FAILED", "NEEDS_REVIEW"} or failed:
            items.append({
                "job_id": data.get("job_id"),
                "updated_at": data.get("updated_at"),
                "qc_status": qc_status,
                "failed_stages": failed,
                "request": data.get("request") or {},
            })
    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    items = items[: max(1, min(limit, 200))]
    return {"total": len(items), "items": items}


@router.post("/jobs/{job_id}/override")
async def localization_expert_override(job_id: str, body: ExpertOverrideRequest):
    run = _load_run(job_id)
    invalidations: list[str] = []

    if body.profile_updates:
        merged = {**run.effective_profile, **body.profile_updates}
        try:
            profile = LocalizationProfile.model_validate(merged)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid profile override: {exc}") from exc
        run.update_effective_profile(profile.model_dump())
        stage = _profile_invalidation(set(body.profile_updates))
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


@router.post("/jobs/{job_id}/chunks/{chunk_id}/regenerate")
async def regenerate_localization_chunk(job_id: str, chunk_id: int):
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
    run = _load_run(job_id)
    stage = body.from_stage or run.first_incomplete_stage() or "qc"
    run.invalidate_from(stage, delete_artifacts=True)
    await _dispatch_existing_run(run)
    return {"ok": True, "job_id": job_id, "from_stage": stage}


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
    from backend.core.tool_runners import run_tool_translate

    jobs = []
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
        dispatch(run_tool_translate(
            job.id,
            in_path,
            target_language,
            mode,
            voice_id,
            caption_style,
            emoji_style,
            target_aspect=target_aspect,
            profile_id=profile_id,
        ))
        jobs.append({"job_id": job.id, "filename": file.filename})

    return {"count": len(jobs), "jobs": jobs}
