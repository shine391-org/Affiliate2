# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Job-table retention — keep the log bounded without deleting the library.

The `jobs` table is append-only in practice: every scout, download, analyze,
render and tool run adds a row and nothing ever removed them. On the author's dev
install it reached 2,260 rows (1,407 of them failures) in three months, and the
Activity panel reads it, so both the DB and that panel grow without limit.

⚠️ **THE TRAP.** A successful tool job IS a library item. `library_index` projects
`jobs.output_json.file` into the Library, and the asset endpoint resolves a file
by job id. Deleting an old `tool:captions` row would make the captioned video
vanish from every surface while the bytes sat on disk forever — a data-loss bug dressed as
housekeeping. So retention is defined by what a row IS, not by how old it is:

    prunable   = terminal status AND older than the window AND not a library item
    kept       = anything running/pending, and every row backing a file that
                 still exists on disk, at any age

That makes the sweep safe by construction: the only rows it can remove are
records of ACTIVITY (a scout that ran, a download that failed, an analyze that
completed) plus rows whose file is already gone, which the index skips anyway.

A hard ceiling backs the time window, because a heavy week can add thousands of
rows inside the retention period; it deletes the oldest prunable rows only, and
never touches the library either.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.job import Job
from backend.services.library_taxonomy import classify

logger = logging.getLogger(__name__)

# How long a finished job's RECORD is worth keeping. Long enough that "what
# happened last week?" is still answerable in Activity; short enough that the
# table doesn't carry a year of scout runs.
RETENTION_DAYS = 30

# Hard ceiling on prunable rows, independent of age — a heavy week can add
# thousands inside the window. Library-backing rows are never counted or cut.
MAX_PRUNABLE_ROWS = 2000

TERMINAL_STATUSES = ("success", "failed", "cancelled")


def is_library_item(job: Job) -> bool:
    """Does this row back a file the user owns (and therefore the Library shows)?

    True → never prune, at any age. The row is not a log entry, it is the item:
    delete it and the file is orphaned on disk while disappearing from the
    Library and the asset endpoint that serves it.
    """
    owns_last_good_localization = (
        job.job_type == "tool:translate"
        and job.status in {"failed", "cancelled"}
    )
    if (
        job.status != "success"
        and not owns_last_good_localization
    ) or not job.output_json:
        return False
    if classify(job.job_type) is None:
        return False  # scout / analyze / download / render — activity, not a file
    try:
        out = json.loads(job.output_json)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(out, dict):
        return False
    # Legacy localization review gates were incorrectly persisted as successful
    # JSON tool outputs. They are workflow checkpoints, never Library assets.
    if out.get("translation_review_pending"):
        return False
    # NOTE: the INDEX drops a job whose output also registered a generated_videos
    # row, to avoid showing the same bytes twice. Retention must NOT copy that
    # rule: the job row is still the record of the run, and deleting it while the
    # file lives on is how a sweep quietly loses history.
    file_str = out.get("file")
    if not file_str:
        return False
    try:
        return Path(file_str).is_file()
    except OSError:  # pragma: no cover — unresolvable path
        return False


def plan_deletions(prunable: list, cutoff: datetime, max_prunable: int) -> tuple[list, list]:
    """Split prunable rows (OLDEST FIRST) into (aged, over_cap).

    Pure, because the ceiling rule is the part worth testing exactly and doing it
    against the shared dev DB tests the database, not the logic (the first attempt
    did precisely that and failed for reasons that had nothing to do with the
    rule).
    """
    aged = [r for r in prunable if r.created_at and r.created_at < cutoff]
    remaining = [r for r in prunable if r not in aged]
    over = remaining[: max(0, len(remaining) - max_prunable)]
    return aged, over


async def sweep(db: AsyncSession, *, retention_days: int = RETENTION_DAYS,
                max_prunable: int = MAX_PRUNABLE_ROWS) -> dict[str, int]:
    """Delete stale activity rows. Returns counts for the log.

    Two passes, both restricted to prunable rows:
      1. anything terminal and older than the window
      2. if still over the ceiling, the oldest of what is left

    Never deletes a running or pending job (it may be alive in another process —
    see the zombie-sweep note on Job.updated_at), and never deletes a row that
    backs a file on disk.
    """
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    rows = (
        await db.execute(
            select(Job)
            .where(Job.status.in_(TERMINAL_STATUSES))
            .order_by(Job.created_at.asc())
        )
    ).scalars().all()

    prunable: list[Job] = [j for j in rows if not is_library_item(j)]
    kept_library = len(rows) - len(prunable)

    aged, over_cap = plan_deletions(prunable, cutoff, max_prunable)
    localization_workspaces: list[str] = []
    deleted_old = 0
    deleted_over_cap = 0

    # Conditional DELETE closes the race with an explicit rerun claim. Recovery
    # always changes a terminal Job to pending before touching its manifest; if
    # that happened after our SELECT, status no longer matches and retention
    # leaves the resurrected row/workspace alone.
    for bucket, jobs in (("aged", aged), ("over", over_cap)):
        for job in jobs:
            result = await db.execute(
                delete(Job).where(
                    Job.id == job.id,
                    Job.status == job.status,
                )
            )
            if not result.rowcount:
                continue
            if bucket == "aged":
                deleted_old += 1
            else:
                deleted_over_cap += 1
            if job.job_type == "tool:translate":
                localization_workspaces.append(job.id)

    if deleted_old or deleted_over_cap:
        await db.commit()
        if localization_workspaces:
            from backend.services.localization_stage_service import cleanup_localization_workspace
            for job_id in localization_workspaces:
                try:
                    cleanup_localization_workspace(job_id)
                except (OSError, ValueError):
                    logger.warning(
                        "job retention: could not remove localization workspace for %s",
                        job_id,
                    )
        logger.info(
            "job retention: deleted %d aged + %d over-cap; kept %d library-backing row(s)",
            deleted_old, deleted_over_cap, kept_library,
        )

    total = (await db.execute(select(func.count(Job.id)))).scalar() or 0
    return {
        "deleted_aged": deleted_old,
        "deleted_over_cap": deleted_over_cap,
        "kept_library_items": kept_library,
        "rows_remaining": total,
    }


# ── Scout results ───────────────────────────────────────────────────────────
# Scout rows are leads, not files, and they accumulate fastest of anything in the
# database — up to 50 per niche per platform per run. They have their own page
# now, so they need their own ceiling.
SCOUT_RETENTION_DAYS = 60
MAX_SCOUT_ROWS = 5000


async def sweep_scout(db: AsyncSession, *, retention_days: int = SCOUT_RETENTION_DAYS,
                      max_rows: int = MAX_SCOUT_ROWS) -> dict[str, int]:
    """Trim scout_results, keeping every row something else points at.

    The guard here is REFERENCES, not age: `downloaded_videos.scout_result_id` and
    `generated_videos.source_scout_result_id` read back through these rows for the
    source URL and thumbnail (the title and platform are denormalised onto the
    download, deliberately, but the URL is not). Deleting a referenced lead would
    quietly break the "where did this come from?" link on a video the user still
    has.
    """
    from backend.models.downloaded_video import DownloadedVideo
    from backend.models.generated_video import GeneratedVideo
    from backend.models.scout_result import ScoutResult

    referenced: set[str] = set()
    for column in (DownloadedVideo.scout_result_id, GeneratedVideo.source_scout_result_id):
        referenced.update(
            v for (v,) in (await db.execute(select(column).where(column.isnot(None)))).all() if v
        )

    rows = (
        await db.execute(select(ScoutResult).order_by(ScoutResult.created_at.asc()))
    ).scalars().all()
    prunable = [r for r in rows if r.id not in referenced]

    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    aged, over_cap = plan_deletions(prunable, cutoff, max_rows)
    for row in [*aged, *over_cap]:
        await db.delete(row)

    if aged or over_cap:
        await db.commit()
        logger.info(
            "scout retention: deleted %d aged + %d over-cap; kept %d referenced lead(s)",
            len(aged), len(over_cap), len(rows) - len(prunable),
        )

    total = (await db.execute(select(func.count(ScoutResult.id)))).scalar() or 0
    return {
        "deleted_aged": len(aged),
        "deleted_over_cap": len(over_cap),
        "kept_referenced": len(rows) - len(prunable),
        "rows_remaining": total,
    }
