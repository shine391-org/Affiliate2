# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Utility for creating job records in the database."""
import asyncio
import json
import logging

from backend.database import AsyncSessionLocal
from backend.models.job import Job

logger = logging.getLogger(__name__)

# ── Behavior instrumentation ────────────────────────────────────────────────
# Map a completed job's `job_type` -> a UserBehavior event so the
# personalization engine (profile synthesis, smart-suggestions, pipeline_state
# awareness) can learn what the user ACTUALLY does. Without this the whole
# intelligence layer reads `video_generated` / `video_downloaded` /
# `video_uploaded` events that nothing ever writes, so it runs on empty.
# Hooking here means every runner that reaches update_job_status(..., "success")
# is instrumented automatically, with no per-runner edits to drift out of sync.
_JOB_EVENT_MAP = {
    "generate": "video_generated",
    "download": "video_downloaded",
    "download_url": "video_downloaded",
    "batch_download": "video_downloaded",
    "clip_extraction": "clip_extracted",
    "analyze": "video_analyzed",
    "analyze_imported": "video_analyzed",
    "analyze_channel": "channel_analyzed",
}

# Keys worth carrying onto the behavior event (compact; drives the profile +
# suggestions). Pulled defensively from the job's input/output JSON.
_EVENT_DATA_KEYS = (
    "generated_video_id", "downloaded_video_id", "source_id", "source_video_id",
    "niche", "aspect_ratio", "gen_tier", "visual_style", "count", "clip_count",
    "platform",
)

_TERMINAL = {"success", "failed", "cancelled"}
# A human-review pause is inactive like a terminal state for late progress
# updates, but it is NOT finished: restart_job() may explicitly reopen it.
_PAUSED = {"awaiting_review"}
_QUIESCENT = _TERMINAL | _PAUSED
_ACTIVE_JOB_DEDUPE_LOCK = asyncio.Lock()

async def _notify_phase_f_job(job_type: str, status: str, user_id: str,
                              job_id: str, input_json: str | None,
                              output_json: str | None, error: str | None,
                              current_step: str | None):
    """Best-effort channel delivery; persisted Job remains the state owner."""
    try:
        from backend.messaging.base import NotificationEvent
        from backend.messaging.manager import messaging
        try:
            inputs = json.loads(input_json or "{}")
            outputs = json.loads(output_json or "{}")
        except (TypeError, ValueError):
            inputs, outputs = {}, {}
        if not isinstance(inputs, dict) or not isinstance(outputs, dict):
            inputs, outputs = {}, {}
        data = {"job_id": job_id,
                "generated_video_id": outputs.get("generated_video_id") or inputs.get("generated_video_id")}
        if status == "failed":
            await messaging.notify(NotificationEvent.JOB_FAILED, user_id,
                                   job_type=job_type, error=error or current_step or "No error reported", **data)
        elif job_type == "generate":
            await messaging.notify(NotificationEvent.VIDEO_GENERATED, user_id, **data)
        elif job_type == "upload":
            platforms = outputs.get("uploaded_platforms") or []
            await messaging.notify(NotificationEvent.VIDEO_UPLOADED, user_id,
                                   platform=", ".join(platforms) or "the selected destination",
                                   errors=outputs.get("errors") or [], **data)
    except Exception:
        logger.warning("Phase F job notification failed for %s", job_id, exc_info=True)



async def _record_job_behavior(job_type: str, user_id: str, input_json: str, output_json: str):
    """Best-effort: record a UserBehavior event for a completed job. Never
    raises into the job-status path — telemetry must not break completion."""
    try:
        if job_type and job_type.startswith("tool:"):
            event_type = "tool_used"
        elif job_type and job_type.startswith("upload"):
            # OSS ships the uploader — uploads are first-class funnel events.
            event_type = "video_uploaded"
        else:
            event_type = _JOB_EVENT_MAP.get(job_type)
        if not event_type:
            return

        data: dict = {}
        for blob in (input_json, output_json):
            try:
                parsed = json.loads(blob) if blob else {}
            except (TypeError, ValueError):
                parsed = {}
            if isinstance(parsed, dict):
                for k in _EVENT_DATA_KEYS:
                    if k in parsed and k not in data and parsed[k] is not None:
                        data[k] = parsed[k]
        if job_type.startswith("tool:"):
            data["tool"] = job_type[len("tool:"):]

        from backend.core.user_intelligence import UserIntelligence
        await UserIntelligence().record_event(event_type, data, user_id=user_id or "local")
    except Exception:
        logger.debug("behavior-event record failed for job_type=%s", job_type, exc_info=True)


async def create_job(job_type: str, user_id: str = "local", input_data: dict = None) -> Job:
    """Create a new Job row and return it."""
    async with AsyncSessionLocal() as db:
        job = Job(
            user_id=user_id,
            job_type=job_type,
            status="pending",
            input_json=json.dumps(input_data) if input_data else None,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job


async def create_or_reuse_active_job(
    job_type: str,
    user_id: str = "local",
    input_data: dict | None = None,
    *,
    dedupe_key: str | None = None,
) -> tuple[Job, bool]:
    """Create one active job per deterministic request signature.

    Rapid duplicate requests in the same backend process are serialized by the
    lock. The persisted _dedupe_key also lets a later request reuse an active
    row after a frontend refresh instead of starting another heavy pipeline.

    Returns (job, reused). Terminal jobs never block a fresh run. A human-review
    pause does block an identical fresh run because it is the SAME unfinished
    workflow waiting for user action.
    """
    if not dedupe_key:
        return await create_job(job_type, user_id, input_data), False

    payload = dict(input_data or {})
    payload["_dedupe_key"] = dedupe_key

    async with _ACTIVE_JOB_DEDUPE_LOCK:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            candidate_statuses = ["pending", "running", "awaiting_review"]
            # Only localization has the pre-fix "successful but actually paused"
            # compatibility case. Do not scan historical success rows for every
            # other deduped job type.
            if job_type == "tool:translate":
                candidate_statuses.append("success")

            active = (await db.execute(
                select(Job)
                .where(
                    Job.user_id == user_id,
                    Job.job_type == job_type,
                    Job.status.in_(candidate_statuses),
                )
                .order_by(Job.created_at.desc())
            )).scalars().all()

            for existing in active:
                try:
                    submitted = json.loads(existing.input_json or "{}")
                except (TypeError, ValueError):
                    continue
                if not (isinstance(submitted, dict) and submitted.get("_dedupe_key") == dedupe_key):
                    continue
                if existing.status == "success":
                    # Pre-fix localization review checkpoints were incorrectly
                    # finalized as success with a JSON marker. They are the same
                    # unfinished workflow, not a completed request that should
                    # allow a duplicate.
                    if job_type != "tool:translate":
                        continue
                    try:
                        legacy_out = json.loads(existing.output_json or "{}")
                    except (TypeError, ValueError):
                        legacy_out = {}
                    if not (
                        isinstance(legacy_out, dict)
                        and legacy_out.get("translation_review_pending") is True
                    ):
                        # This is the newest matching request and it genuinely
                        # finished. Do not skip past it and resurrect an even
                        # older legacy review checkpoint with the same signature.
                        break
                    existing.status = "awaiting_review"
                    existing.completed_at = None
                    existing.current_step = "Chờ duyệt bản dịch"
                    existing.progress_pct = min(float(existing.progress_pct or 50), 50.0)
                    await db.commit()
                    await db.refresh(existing)
                return existing, True

            job = Job(
                user_id=user_id,
                job_type=job_type,
                status="pending",
                input_json=json.dumps(payload),
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return job, False


async def update_job_status(
    job_id: str,
    status: str,
    progress_pct: float = None,
    current_step: str = None,
    error_message: str = None,
    output_data: dict = None,
):
    """Update a job's status and optional fields."""
    from datetime import datetime
    import asyncio
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            return False
        # Cancellation and human-review pause are explicit stop states and are
        # sticky. A stale coroutine may finish a native call afterwards, but it
        # must not emit success/failure/progress over either state. Explicit
        # reopen paths use restart_job(), which performs the guarded SQL update.
        if job.status in {"cancelled", "awaiting_review"} and status != job.status:
            return False
        # Other final/quiescent states must never be resurrected by a late
        # progress tick. Updates among success/failed remain allowed so a
        # draining predecessor can still self-heal a zombie-sweep race.
        if job.status in _QUIESCENT and status not in _QUIESCENT:
            return False
        prev_status = job.status
        job.status = status
        # Heartbeat: touch on EVERY accepted update, even when no other column
        # changes (a progress tick rejected by the regression guard just below
        # would otherwise produce no UPDATE at all, and `onupdate` would not
        # fire). The boot-time sweep uses this to tell a job that is alive in a
        # draining predecessor instance from one that died with the old
        # process — see database.sweep_stale_jobs.
        job.updated_at = datetime.utcnow()
        if progress_pct is not None:
            # Prevent progress regression (e.g. 95% → 50%) unless explicitly restarting
            is_restart = status == "running" and progress_pct == 0
            if is_restart or progress_pct >= (job.progress_pct or 0):
                job.progress_pct = progress_pct
        if current_step is not None:
            job.current_step = current_step
        if error_message is not None:
            job.error_message = error_message
        if output_data is not None:
            job.output_json = json.dumps(output_data)
        if status == "running":
            recovery_claim = str(job.task_id or "").startswith("recovery_claim:")
            if recovery_claim:
                # Preserve prior terminal metadata until a runner REALLY starts,
                # so a process crash before dispatch can restore old state.
                job.task_id = None
                job.error_message = None
                job.completed_at = None
                job.started_at = datetime.utcnow()
            elif not job.started_at:
                job.started_at = datetime.utcnow()
        elif str(job.task_id or "").startswith("recovery_claim:") and status != "pending":
            job.task_id = None
        if status in ("success", "failed", "cancelled"):
            job.completed_at = datetime.utcnow()
        elif status == "awaiting_review":
            # C6 translation is complete, but the localization workflow is not.
            # Keep the original started_at and do not stamp a fake completion.
            job.completed_at = None
        await db.commit()

        accepted = True

        # On the FIRST transition to success, emit a behavior event so the
        # personalization engine learns this action happened. Fire-and-forget;
        # never blocks or fails job completion. (Args evaluated now, while the
        # session is open, so no detached-instance access later.)
        if status == "success" and prev_status not in _TERMINAL:
            asyncio.create_task(_record_job_behavior(
                job.job_type, job.user_id, job.input_json, job.output_json,
            ))
        if job.job_type in ("generate", "upload") and status in ("success", "failed") and prev_status != status:
            asyncio.create_task(_notify_phase_f_job(
                job.job_type, status, job.user_id, job.id, job.input_json,
                job.output_json, job.error_message, job.current_step,
            ))
        return accepted


async def job_cancelled(job_id: str | None) -> bool:
    """True when the job row has been flipped to "cancelled" by the user.

    Cancellation (DELETE /api/jobs/{id} or the WS job_cancel message) only
    updates the DB row — it never interrupts the running coroutine. Long
    pipelines poll this at phase boundaries (after Whisper, after AI
    selection, before the ffmpeg fan-out) and raise JobCancelledError so a
    cancelled job stops burning CPU and its "cancelled" status isn't
    overwritten by a late terminal write.

    `None` / unknown ids return False — a pipeline invoked without a job
    (direct service call, tests) must never think it was cancelled. DB errors
    also return False: cancellation is best-effort, and a transient read
    failure must not kill a healthy job.
    """
    if not job_id:
        return False
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            row = (await db.execute(
                select(Job.status).where(Job.id == job_id)
            )).scalar_one_or_none()
        return row == "cancelled"
    except Exception:
        return False



async def claim_job_for_restart(
    job_id: str,
    *,
    allowed_statuses: set[str] | tuple[str, ...] | list[str] | None = None,
) -> dict | None:
    """Atomically claim an inactive job before mutating resumable state.

    The claim is persisted in task_id while status becomes pending. Crucially,
    prior progress/error/timestamps/current_step survive until a background
    runner really enters running. If the process dies between claim and
    dispatch, boot recovery can restore the previous quiescent status.
    """
    from datetime import datetime
    from uuid import uuid4
    from sqlalchemy import select, update

    allowed = set(allowed_statuses or ())
    async with AsyncSessionLocal() as db:
        job = (await db.execute(
            select(Job).where(Job.id == job_id)
        )).scalar_one_or_none()
        if job is None:
            return None
        if job.status in {"running", "pending"}:
            return None
        if allowed and job.status not in allowed:
            return None

        previous_status = str(job.status)
        marker = f"recovery_claim:{previous_status}:{uuid4().hex}"
        token = {
            "status": previous_status,
            "task_id": job.task_id,
            "progress_pct": job.progress_pct,
            "current_step": job.current_step,
            "error_message": job.error_message,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "updated_at": job.updated_at,
            "marker": marker,
        }

        predicates = [Job.id == job_id, Job.status == job.status]
        if job.updated_at is None:
            predicates.append(Job.updated_at.is_(None))
        else:
            predicates.append(Job.updated_at == job.updated_at)

        result = await db.execute(
            update(Job)
            .where(*predicates)
            .values(
                status="pending",
                task_id=marker,
                updated_at=datetime.utcnow(),
            )
        )
        await db.commit()
        if not result.rowcount:
            return None
        return token


async def restore_job_restart_claim(job_id: str, token: dict) -> bool:
    """Restore a claim that aborted before a runner was dispatched.

    The marker, not updated_at, proves ownership. Control-plane actions may
    legitimately update output_json while holding the claim, which touches the
    heartbeat. Once a runner starts, update_job_status consumes the marker.
    """
    from datetime import datetime
    from sqlalchemy import update

    marker = token.get("marker")
    if not marker:
        return False

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == "pending",
                Job.task_id == marker,
            )
            .values(
                status=token.get("status"),
                task_id=token.get("task_id"),
                progress_pct=token.get("progress_pct"),
                current_step=token.get("current_step"),
                error_message=token.get("error_message"),
                started_at=token.get("started_at"),
                completed_at=token.get("completed_at"),
                updated_at=datetime.utcnow(),
            )
        )
        await db.commit()
        return bool(result.rowcount)

async def restart_job(job_id: str) -> bool:
    """Atomically reopen one inactive job for an explicit rerun."""
    return await claim_job_for_restart(job_id) is not None
