# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""REST /api/jobs — job CRUD + status."""
import base64
import binascii
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import and_, or_, select, text

from backend.database import AsyncSessionLocal
from backend.models.job import Job
from backend.agents.job_helper import update_job_status

logger = logging.getLogger(__name__)
router = APIRouter()


def _encode_job_cursor(row) -> str:
    """Anchor a keyset page on the last row's immutable identity.

    (created_at, id) is stable for the life of a row, unlike a position in a
    filtered list. Encoded rather than exposed as two query params so a caller
    cannot pair a timestamp from one page with an id from another.

    A row with no created_at (pre-migration data, written before the column
    existed) encodes the empty string, which decodes back to the SAME
    sentinel rather than raising. The keyset predicate sorts such a row to the
    very end of the walk, so it is served exactly once and cannot strand the
    pages before it - failing the whole restore on one legacy row would trade
    a cosmetic gap for a visible outage.
    """
    raw = f"{row.created_at.isoformat() if row.created_at else ''}|{row.id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_job_cursor(cursor: str) -> tuple[datetime | None, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created_at_raw, _, cursor_id = raw.partition("|")
        if not cursor_id:
            raise ValueError("malformed cursor")
        created_at = datetime.fromisoformat(created_at_raw) if created_at_raw else None
        return created_at, cursor_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("Malformed pagination cursor") from exc


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
    cursor: Optional[str] = None,
):
    """List jobs, optionally filtered by status and/or type.

    `status` accepts a COMMA-SEPARATED list ("running,pending") as well as a
    single value. The frontend's active-job restore wants exactly those two and
    was issuing one request per status on every page load; one filter answers
    both. A single value still means what it always did, so every existing
    caller is unaffected.

    `cursor` is an OPTIONAL keyset continuation for callers paging a set that
    changes while they read it. Offset paging cannot be used there: the restore
    filter is on `status`, so a job that finishes LEAVES the result set and
    every later row shifts one position toward the head. Reading page 2 at a
    fixed offset then skips real, still-active rows - measured at 10 jobs lost
    out of 250 when 10 finished between two pages. The cursor anchors on the
    last row's immutable (created_at, id) instead of on a POSITION, so rows
    leaving the set cannot move the window.

    `offset` keeps its previous meaning: same window, same `total`, same
    newest-first order. The only change it can observe is `next_cursor`, which
    is additive and ignored by offset callers. The `(created_at, id)` tie-break
    added to the ORDER BY makes the order deterministic where it previously
    was not - rows sharing a created_at used to come back in whatever order
    SQLite produced, which is exactly the instability a keyset cursor cannot
    tolerate.
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

        # `id` is the tie-breaker on both sides. created_at alone is not a
        # total order: rows admitted in the same tick share a timestamp, and an
        # unstable tie-break makes a cursor skip or repeat those rows.
        if cursor:
            try:
                created_at, cursor_id = _decode_job_cursor(cursor)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            # A NULL anchor means every dated row precedes it, so the legacy
            # rows behind it are all still served. `IS NULL` rows sort last
            # under `created_at DESC` on SQLite, so they must be included
            # explicitly rather than dropped by `<` against NULL.
            if created_at is None:
                base = base.where(or_(
                    Job.created_at.is_(None),
                    and_(Job.created_at.is_not(None), Job.id < cursor_id),
                ))
            else:
                base = base.where(or_(
                    Job.created_at < created_at,
                    and_(Job.created_at == created_at, Job.id < cursor_id),
                ))
            query = base.order_by(
                Job.created_at.desc(), Job.id.desc(),
            ).limit(limit)
        else:
            # The offset path gets the SAME deterministic ordering, so a
            # caller that pages by offset and one that pages by cursor agree
            # on which rows exist - only the windowing differs.
            query = base.order_by(
                Job.created_at.desc(), Job.id.desc(),
            ).offset(offset).limit(limit)
        result = await db.execute(query)
        jobs = result.scalars().all()

    # A continuation is offered whenever the page came back FULL.
    #
    # It is NOT gated on "did the caller send a cursor": the FIRST keyset
    # page is sent WITHOUT one, and gating there would leave it without a
    # token, so a caller walking from the start would stop after one page -
    # the exact truncation this replaced. Gating on `offset` is no better:
    # offset=0 is both the default and a real window, so no value of it can
    # express intent.
    #
    # An offset caller simply ignores the token - its next call carries an
    # offset, and the window it receives is unchanged either way. The
    # restore is the keyset consumer; `total` and the row window keep their
    # previous meaning for everyone.
    next_cursor = (
        _encode_job_cursor(jobs[-1]) if jobs and len(jobs) >= limit else None
    )
    return {
        "total": total,
        "next_cursor": next_cursor,
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
