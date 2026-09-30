# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""REST /api/jobs — job CRUD + status."""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select, text

from backend.database import AsyncSessionLocal
from backend.models.job import Job
from backend.agents.job_helper import update_job_status

logger = logging.getLogger(__name__)
router = APIRouter()


def _drop_localization_workspace(job: Job) -> None:
    if job.job_type != "tool:translate":
        return
    try:
        from backend.services.localization_stage_service import cleanup_localization_workspace
        cleanup_localization_workspace(job.id)
    except (OSError, ValueError):
        logger.warning("Could not remove localization workspace for job %s", job.id)


class JobResponse(BaseModel):
    id: str
    job_type: str
    status: str
    title: Optional[str] = None
    progress_pct: float = 0.0
    current_step: Optional[str] = None
    error_message: Optional[str] = None
    input_json: Optional[str] = None
    output_json: Optional[str] = None
    estimated_cost_usd: float = 0.0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


@router.get("/jobs")
async def list_jobs(
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
):
    """List jobs, optionally filtered by status and/or type.

    `status` accepts a COMMA-SEPARATED list ("running,pending") as well as a
    single value. The frontend's active-job restore wants exactly those two and
    was issuing one request per status on every page load; one filter answers
    both. A single value still means what it always did, so every existing
    caller is unaffected.
    """
    async with AsyncSessionLocal() as db:
        statuses = [s.strip() for s in (status or "").split(",") if s.strip()]

        # Built once and applied to BOTH the page query and the count. They
        # used to be written out separately, which is the shape where a filter
        # added to one silently makes `total` disagree with the rows.
        filters = [Job.user_id == "local"]
        if statuses:
            filters.append(Job.status.in_(statuses))
        if job_type:
            filters.append(Job.job_type == job_type)

        base = select(Job).where(*filters)

        from sqlalchemy import func
        total = (await db.execute(
            select(func.count(Job.id)).where(*filters)
        )).scalar()

        query = base.order_by(Job.created_at.desc()).offset(offset).limit(limit)
        result = await db.execute(query)
        jobs = result.scalars().all()

    return {
        "total": total,
        "jobs": [
            {
                "id": j.id,
                "job_type": j.job_type,
                "status": j.status,
                "title": j.title,
                "progress_pct": j.progress_pct,
                "current_step": j.current_step,
                "error_message": j.error_message,
                "input_json": j.input_json,
                "output_json": j.output_json,
                "estimated_cost_usd": j.estimated_cost_usd,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in jobs
        ]
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """Get a single job by ID."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.id == job_id))
        j = result.scalar_one_or_none()

    if not j:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "id": j.id,
        "job_type": j.job_type,
        "status": j.status,
        "title": j.title,
        "progress_pct": j.progress_pct,
        "current_step": j.current_step,
        "error_message": j.error_message,
        "input_json": j.input_json,
        "output_json": j.output_json,
        "estimated_cost_usd": j.estimated_cost_usd,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "completed_at": j.completed_at.isoformat() if j.completed_at else None,
        "created_at": j.created_at.isoformat() if j.created_at else None,
    }


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    """Cancel a live job or delete an inactive activity row without rerun races."""
    workspace_job = None
    async with AsyncSessionLocal() as db:
        # Recovery claims also write the Job row. The SQLite write lock makes
        # "observe state -> cancel/delete" atomic across predecessor/replacement
        # backend processes, so a rerun cannot be claimed underneath deletion.
        await db.execute(text("BEGIN IMMEDIATE"))
        result = await db.execute(select(Job).where(Job.id == job_id))
        j = result.scalar_one_or_none()

        if not j:
            await db.rollback()
            raise HTTPException(status_code=404, detail="Job not found")

        if j.status in ("running", "pending", "awaiting_review"):
            j.status = "cancelled"
            j.task_id = None
            await db.commit()
            return {"message": "Job cancelled"}

        from backend.services.job_retention import is_library_item
        if is_library_item(j):
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail=("This job produced a file in your Library. Delete it from "
                        "there (that removes the file too)."),
            )

        workspace_job = j.id if j.job_type == "tool:translate" else None
        await db.delete(j)
        await db.commit()

    if workspace_job:
        try:
            from backend.services.localization_stage_service import cleanup_localization_workspace
            cleanup_localization_workspace(workspace_job)
        except (OSError, ValueError):
            logger.warning("Could not remove localization workspace for job %s", workspace_job)
    return {"message": "Job deleted"}


class BulkDeleteRequest(BaseModel):
    job_ids: list[str]


@router.post("/jobs/bulk-delete")
async def bulk_delete_jobs(body: BulkDeleteRequest):
    """Delete multiple completed/failed jobs at once.

    Rows that back a file in the Library are KEPT and counted separately.
    "Clear finished" is a request to tidy the activity log, and it must never
    be a way to delete the user's videos — which is what it would be now that a
    successful tool run is the Library item for its own output.
    """
    from backend.services.job_retention import is_library_item

    deleted = 0
    cancelled = 0
    kept_library = 0
    cleanup_ids: list[str] = []
    async with AsyncSessionLocal() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        result = await db.execute(select(Job).where(Job.id.in_(body.job_ids)))
        jobs = result.scalars().all()
        for j in jobs:
            if j.status in ("running", "pending", "awaiting_review"):
                j.status = "cancelled"
                j.task_id = None
                cancelled += 1
            elif is_library_item(j):
                kept_library += 1
            else:
                if j.job_type == "tool:translate":
                    cleanup_ids.append(j.id)
                await db.delete(j)
                deleted += 1
        await db.commit()

    if cleanup_ids:
        from backend.services.localization_stage_service import cleanup_localization_workspace
        for job_id in cleanup_ids:
            try:
                cleanup_localization_workspace(job_id)
            except (OSError, ValueError):
                logger.warning("Could not remove localization workspace for job %s", job_id)
    return {"deleted": deleted, "cancelled": cancelled, "kept_library": kept_library}
