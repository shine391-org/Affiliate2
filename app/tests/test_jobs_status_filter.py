# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""`GET /api/jobs?status=` takes a comma-separated list.

The frontend's active-job restore wants exactly `running` and `pending`, and
was issuing one request per status on every page load. One filter answers
both.

Two properties worth pinning, because breaking either is silent:
  - a SINGLE status still means what it always did (every other caller), and
  - `total` is computed from the same filters as the rows. Those two used to be
    written out separately, which is the exact shape where a filter added to
    one makes the count disagree with the page.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from backend.api.jobs import list_jobs
from backend.database import init_db, AsyncSessionLocal
from backend.models.job import Job


PREFIX = "statusfilter-probe-"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    asyncio.run(init_db())


@pytest.fixture
def probe_jobs():
    """Three rows, one per status, tagged so they can be told from real data."""
    made = []

    async def _make():
        async with AsyncSessionLocal() as db:
            for st in ("running", "pending", "completed"):
                j = Job(id=str(uuid.uuid4()), job_type=f"{PREFIX}{st}",
                        status=st, title=f"{PREFIX}{st}")
                db.add(j)
                made.append(j.id)
            await db.commit()

    async def _drop():
        async with AsyncSessionLocal() as db:
            for jid in made:
                row = await db.get(Job, jid)
                if row:
                    await db.delete(row)
            await db.commit()

    asyncio.run(_make())
    yield made
    asyncio.run(_drop())


def _mine(payload):
    return [j for j in payload["jobs"] if str(j.get("job_type", "")).startswith(PREFIX)]


@pytest.mark.asyncio
async def test_a_single_status_still_filters_to_that_status(probe_jobs):
    out = await list_jobs(status="running", limit=200)
    mine = _mine(out)
    assert len(mine) == 1
    assert mine[0]["status"] == "running"


@pytest.mark.asyncio
async def test_a_comma_list_returns_every_named_status(probe_jobs):
    out = await list_jobs(status="running,pending", limit=200)
    got = sorted(j["status"] for j in _mine(out))
    assert got == ["pending", "running"]


@pytest.mark.asyncio
async def test_a_comma_list_excludes_everything_else(probe_jobs):
    """The point of the filter is that `completed` does NOT come back — the
    restore reconciles zombies against this list, so a stray terminal row
    would keep a finished job pinned in the banner."""
    out = await list_jobs(status="running,pending", limit=200)
    assert all(j["status"] != "completed" for j in _mine(out))


@pytest.mark.asyncio
async def test_whitespace_and_empty_entries_are_tolerated(probe_jobs):
    out = await list_jobs(status=" running , , pending ", limit=200)
    assert sorted(j["status"] for j in _mine(out)) == ["pending", "running"]


@pytest.mark.asyncio
async def test_no_status_is_unfiltered(probe_jobs):
    out = await list_jobs(limit=200)
    assert len(_mine(out)) == 3


@pytest.mark.asyncio
async def test_total_is_computed_from_the_same_filters_as_the_rows(probe_jobs):
    """`total` drives "showing the latest N of M". Computed off a different
    filter set, it silently overstates — the page says 2 rows of 500."""
    everything = await list_jobs(limit=500)
    running_only = await list_jobs(status="running", limit=500)
    both = await list_jobs(status="running,pending", limit=500)

    assert running_only["total"] < everything["total"]
    assert running_only["total"] <= both["total"] <= everything["total"]
    # With a limit that covers the table, total must equal the rows returned.
    for payload in (everything, running_only, both):
        if len(payload["jobs"]) < 500:
            assert payload["total"] == len(payload["jobs"])


@pytest.mark.asyncio
async def test_an_unknown_status_matches_nothing(probe_jobs):
    out = await list_jobs(status="no-such-status", limit=200)
    assert _mine(out) == []

@pytest.mark.asyncio
async def test_awaiting_review_is_paused_not_finished_and_can_resume():
    """C6 human review must be quiescent without pretending the job finished."""
    from backend.agents.job_helper import restart_job, update_job_status

    job_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        db.add(Job(id=job_id, job_type="tool:translate", status="running",
                   progress_pct=36, title="review pause probe"))
        await db.commit()

    try:
        await update_job_status(
            job_id, "awaiting_review", progress_pct=50,
            current_step="Chờ duyệt bản dịch",
            output_data={"translation_review_pending": True},
        )
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            assert row.status == "awaiting_review"
            assert row.progress_pct == 50
            assert row.completed_at is None

        await update_job_status(job_id, "running", progress_pct=55, current_step="late tick")
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            assert row.status == "awaiting_review"
            assert row.progress_pct == 50

        assert await restart_job(job_id) is True
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            assert row.status == "pending"
            assert row.completed_at is None
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            if row:
                await db.delete(row)
                await db.commit()

@pytest.mark.asyncio
async def test_identical_request_reuses_review_paused_job():
    from backend.agents.job_helper import create_or_reuse_active_job

    job_id = str(uuid.uuid4())
    dedupe = "review-paused-dedupe-probe"
    async with AsyncSessionLocal() as db:
        db.add(Job(
            id=job_id,
            job_type="tool:translate",
            status="awaiting_review",
            input_json='{"_dedupe_key":"review-paused-dedupe-probe","target_language":"vi"}',
        ))
        await db.commit()

    try:
        job, reused = await create_or_reuse_active_job(
            "tool:translate",
            "local",
            {"target_language": "vi"},
            dedupe_key=dedupe,
        )
        assert reused is True
        assert job.id == job_id
        assert job.status == "awaiting_review"
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            if row:
                await db.delete(row)
                await db.commit()


@pytest.mark.asyncio
async def test_cancelled_job_cannot_be_overwritten_by_late_success():
    from backend.agents.job_helper import restart_job, update_job_status

    job_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        db.add(Job(id=job_id, job_type="tool:translate", status="running"))
        await db.commit()

    try:
        await update_job_status(job_id, "cancelled", current_step="Cancelled by user")
        await update_job_status(job_id, "success", progress_pct=100, current_step="late success")
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            assert row.status == "cancelled"
            assert row.current_step == "Cancelled by user"

        # Explicit retry remains possible because restart_job is the only door
        # allowed to reopen a cancelled row.
        assert await restart_job(job_id) is True
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            assert row.status == "pending"
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            if row:
                await db.delete(row)
                await db.commit()

@pytest.mark.asyncio
async def test_legacy_review_success_is_migrated_and_reused():
    from backend.agents.job_helper import create_or_reuse_active_job

    job_id = str(uuid.uuid4())
    dedupe = "legacy-review-success-probe"
    async with AsyncSessionLocal() as db:
        db.add(Job(
            id=job_id,
            job_type="tool:translate",
            status="success",
            progress_pct=100,
            input_json=json.dumps({"_dedupe_key": dedupe}),
            output_json=json.dumps({
                "file": "legacy-review.json",
                "translation_review_pending": True,
            }),
        ))
        await db.commit()

    try:
        job, reused = await create_or_reuse_active_job(
            "tool:translate", "local", {}, dedupe_key=dedupe,
        )
        assert reused is True
        assert job.id == job_id
        assert job.status == "awaiting_review"
        assert job.completed_at is None
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            if row:
                await db.delete(row)
                await db.commit()


@pytest.mark.asyncio
async def test_newer_real_success_prevents_older_legacy_review_resurrection():
    from datetime import datetime, timedelta
    from backend.agents.job_helper import create_or_reuse_active_job

    dedupe = "completed-wins-over-old-review"
    old_id = str(uuid.uuid4())
    new_id = str(uuid.uuid4())
    now = datetime.utcnow()
    async with AsyncSessionLocal() as db:
        db.add(Job(
            id=old_id,
            job_type="tool:translate",
            status="success",
            created_at=now - timedelta(minutes=2),
            input_json=json.dumps({"_dedupe_key": dedupe}),
            output_json=json.dumps({"translation_review_pending": True, "file": "old.json"}),
        ))
        db.add(Job(
            id=new_id,
            job_type="tool:translate",
            status="success",
            created_at=now - timedelta(minutes=1),
            input_json=json.dumps({"_dedupe_key": dedupe}),
            output_json=json.dumps({"file": "finished.mp4", "qc_status": "READY"}),
        ))
        await db.commit()

    created_id = None
    try:
        job, reused = await create_or_reuse_active_job(
            "tool:translate", "local", {}, dedupe_key=dedupe,
        )
        created_id = job.id
        assert reused is False
        assert job.id not in {old_id, new_id}
        async with AsyncSessionLocal() as db:
            old = await db.get(Job, old_id)
            assert old.status == "success"
    finally:
        async with AsyncSessionLocal() as db:
            for job_id in (old_id, new_id, created_id):
                if not job_id:
                    continue
                row = await db.get(Job, job_id)
                if row:
                    await db.delete(row)
            await db.commit()

@pytest.mark.asyncio
async def test_restart_claim_is_compare_and_swap_under_concurrency():
    """Two recovery clicks may read the same quiescent row; only one may own it."""
    from backend.agents.job_helper import (
        claim_job_for_restart,
        restore_job_restart_claim,
    )

    job_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        db.add(Job(
            id=job_id,
            job_type="tool:translate",
            status="failed",
            progress_pct=73,
            current_step="QC failed",
            error_message="probe",
        ))
        await db.commit()

    try:
        first, second = await asyncio.gather(
            claim_job_for_restart(job_id),
            claim_job_for_restart(job_id),
        )
        winners = [token for token in (first, second) if token is not None]
        assert len(winners) == 1

        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            assert row.status == "pending"
            assert row.current_step == "QC failed"
            assert str(row.task_id or "").startswith("recovery_claim:failed:")

        assert await restore_job_restart_claim(job_id, winners[0]) is True
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            assert row.status == "failed"
            assert row.progress_pct == 73
            assert row.current_step == "QC failed"
            assert row.error_message == "probe"
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            if row:
                await db.delete(row)
                await db.commit()


@pytest.mark.asyncio
async def test_restart_claim_restore_cannot_rewind_a_started_runner():
    from backend.agents.job_helper import (
        claim_job_for_restart,
        restore_job_restart_claim,
        update_job_status,
    )

    job_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        db.add(Job(id=job_id, job_type="tool:translate", status="failed"))
        await db.commit()

    try:
        token = await claim_job_for_restart(job_id)
        assert token is not None
        assert await update_job_status(
            job_id, "running", progress_pct=5, current_step="live runner",
        ) is True

        assert await restore_job_restart_claim(job_id, token) is False
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            assert row.status == "running"
            assert row.current_step == "live runner"
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            if row:
                await db.delete(row)
                await db.commit()

@pytest.mark.asyncio
async def test_restart_claim_restore_survives_owned_output_metadata_update():
    from backend.agents.job_helper import (
        claim_job_for_restart,
        restore_job_restart_claim,
    )

    job_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        db.add(Job(
            id=job_id,
            job_type="tool:translate",
            status="success",
            current_step="Done",
            output_json=json.dumps({"file": "v1.mp4"}),
        ))
        await db.commit()

    try:
        token = await claim_job_for_restart(job_id)
        assert token is not None

        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            row.output_json = json.dumps({"file": "v1.mp4", "publish_ready": True})
            await db.commit()

        assert await restore_job_restart_claim(job_id, token) is True
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            assert row.status == "success"
            assert row.current_step == "Done"
            assert row.task_id is None
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            if row:
                await db.delete(row)
                await db.commit()

@pytest.mark.asyncio
async def test_stale_pre_dispatch_claim_fails_explicitly_without_rewinding_partial_manifest():
    from datetime import datetime, timedelta
    from backend.database import sweep_stale_jobs

    job_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        db.add(Job(
            id=job_id,
            job_type="tool:translate",
            status="pending",
            task_id="recovery_claim:success:deadprocess",
            current_step="Done",
            progress_pct=100,
            completed_at=datetime.utcnow() - timedelta(minutes=5),
            updated_at=datetime.utcnow() - timedelta(minutes=5),
        ))
        await db.commit()

    try:
        swept, still = await sweep_stale_jobs(
            grace_seconds=0,
            only_ids=[job_id],
        )
        assert swept == 1
        assert still == []

        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            assert row.status == "failed"
            assert row.task_id is None
            assert "recovery action" in row.error_message
            assert row.completed_at is not None
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            if row:
                await db.delete(row)
                await db.commit()

