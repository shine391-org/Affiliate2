# SPDX-License-Identifier: AGPL-3.0-only
"""R3 — `/api/jobs` keyset (cursor) pagination is lossless under churn.

The active-job restore pages a set filtered on `status`. Offset paging cannot
serve it: a job that finishes LEAVES the filtered set, every later row shifts
toward the head, and reading page 2 at a fixed offset skips real, still-active
rows. Reproduced on the restore algorithm: 10 of 250 jobs silently dropped when
10 finished between two pages, with no error anywhere.

These tests drive the REAL endpoint against the REAL database - the simulated
JS suite proves the algorithm, this one proves the query the backend actually
executes, including the (created_at, id) tie-break.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from backend.database import AsyncSessionLocal, Base, engine
from backend.models.job import Job


async def _create_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    asyncio.run(_create_schema())


@pytest.fixture(autouse=True)
def _clean_rows():
    async def _wipe():
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            for row in (await db.execute(select(Job))).scalars().all():
                await db.delete(row)
            await db.commit()

    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


async def _seed(count: int, *, tie_pairs: bool = False) -> list[str]:
    """Seed N running jobs, oldest first.

    `tie_pairs` gives consecutive rows the SAME created_at so the (created_at,
    id) tie-break is exercised - without it a cursor can skip or repeat rows
    admitted in the same tick, which is the same class of loss.
    """
    base = datetime.utcnow() - timedelta(hours=count)
    ids: list[str] = []
    async with AsyncSessionLocal() as db:
        for i in range(count):
            stamp = base + timedelta(minutes=(i // 2 if tie_pairs else i))
            job = Job(
                id=f"job-{i:04d}", user_id="local", job_type="tool:translate",
                status="running", title=f"job {i}",
                created_at=stamp, updated_at=stamp,
            )
            db.add(job)
            ids.append(job.id)
        await db.commit()
    return ids


async def _call(**kwargs):
    from backend.api.jobs import list_jobs

    return await list_jobs(**kwargs)


# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cursor_paging_covers_every_active_job():
    ids = await _seed(250)
    seen: list[str] = []
    cursor = None
    for _ in range(20):
        res = await _call(
            status="running,pending,queued", limit=100, cursor=cursor,
        )
        page = [j["id"] for j in res["jobs"]]
        if not page:
            break
        seen.extend(page)
        cursor = res.get("next_cursor")
        if not cursor:
            break
    assert set(seen) == set(ids), (
        f"cursor paging missed {len(set(ids) - set(seen))} job(s) on a stable set"
    )
    assert len(seen) == len(set(seen)), "a cursor must never repeat a row"


@pytest.mark.asyncio
async def test_cursor_paging_survives_rows_leaving_the_set_mid_walk():
    """The reproduced defect, against the real query.

    250 active jobs; 10 rows past the first page finish after page 1. Offset
    paging loses part of the set whenever rows leave it; the cursor must still
    return every job that is active when it is reached.

    The churn is applied to rows NOT yet served, otherwise the scenario tests
    nothing: page 1 already carries the newest 100, so "the newest finished"
    would only retire rows the restore has legitimately already seen. The rows
    that matter are the ones past the first page.
    """
    ids = await _seed(250)
    # Middle-of-set rows: past page 1 (100 rows) and inside page 2.
    finishing = [f"job-{i:04d}" for i in range(120, 130)]

    res1 = await _call(status="running,pending,queued", limit=100)
    assert len(res1["jobs"]) == 100
    cursor = res1["next_cursor"]
    assert cursor, "the first keyset page must offer a continuation"
    served_first = {j["id"] for j in res1["jobs"]}
    assert not (set(finishing) & served_first), (
        "the churn rows must not already be on page 1, or the scenario is vacuous"
    )

    # The churn: those rows leave the filtered set between pages.
    async with AsyncSessionLocal() as db:
        for job_id in finishing:
            row = await db.get(Job, job_id)
            row.status = "success"
        await db.commit()

    seen = [j["id"] for j in res1["jobs"]]
    while cursor:
        res = await _call(status="running,pending,queued", limit=100, cursor=cursor)
        page = [j["id"] for j in res["jobs"]]
        if not page:
            break
        seen.extend(page)
        cursor = res.get("next_cursor")

    expected = set(ids) - set(finishing)
    missing = expected - set(seen)
    assert not missing, (
        f"cursor paging lost {len(missing)} still-active job(s) when rows left "
        f"the set: {sorted(missing)[:10]}"
    )
    # A row that finished mid-walk must not be handed to the restore as active.
    assert not (set(finishing) & set(seen)), (
        "a job that finished mid-walk must not reappear as active"
    )


@pytest.mark.asyncio
async def test_cursor_paging_tie_breaks_on_identical_timestamps():
    """Rows sharing created_at must not be skipped or repeated.

    created_at alone is not a total order. Without the id tie-break a cursor
    landing inside a tie group can drop or duplicate half of it - the same
    silent-loss class this whole change exists to remove.
    """
    ids = await _seed(60, tie_pairs=True)
    seen: list[str] = []
    cursor = None
    for _ in range(20):
        res = await _call(
            status="running,pending,queued", limit=10, cursor=cursor,
        )
        page = [j["id"] for j in res["jobs"]]
        if not page:
            break
        seen.extend(page)
        cursor = res.get("next_cursor")
        if not cursor:
            break
    assert len(seen) == len(set(seen)), "tie group repeated a row"
    assert set(seen) == set(ids), (
        f"tie group dropped {len(set(ids) - set(seen))} job(s)"
    )


@pytest.mark.asyncio
async def test_offset_path_keeps_its_previous_meaning():
    """Existing callers pass no cursor and must get the same window as before.

    The window and `total` are the contract; `next_cursor` is additive, so an
    offset caller ignoring the token is not a behaviour change. What must NOT
    change is WHICH rows an offset window returns.
    """
    await _seed(30)
    first = await _call(limit=20, offset=0)
    second = await _call(limit=20, offset=20)
    third = await _call(limit=20, offset=40)

    assert len(first["jobs"]) == 20
    assert len(second["jobs"]) == 10
    assert third["jobs"] == []
    assert not (set(j["id"] for j in first["jobs"])
                & set(j["id"] for j in second["jobs"]))
    # Ordering is unchanged: newest first, by (created_at, id).
    stamps = [j["created_at"] for j in first["jobs"]]
    assert stamps == sorted(stamps, reverse=True)
    # A short final page carries no continuation - the keyset walk's end signal.
    assert second.get("next_cursor") is None
    assert first.get("next_cursor"), (
        "a full page must offer a continuation so a keyset walk can start"
    )


@pytest.mark.asyncio
async def test_a_row_without_created_at_does_not_strand_the_rest_of_the_walk():
    """A legacy row (written before the created_at column existed) must not
    break the restore.

    Its cursor encodes an empty timestamp. Decoding that as malformed would 400
    the whole walk, so one undated row would take down every active job behind
    it - trading a cosmetic gap for a visible outage. The undated row sorts
    last and must be served exactly once.
    """
    from sqlalchemy import select as sa_select

    await _seed(12)
    # One row with no created_at: the pre-migration shape.
    async with AsyncSessionLocal() as db:
        stamp = datetime.utcnow()
        db.add(Job(
            id="job-legacy", user_id="local", job_type="tool:translate",
            status="running", title="legacy", updated_at=stamp,
        ))
        await db.commit()

    seen: list[str] = []
    cursor = None
    for _ in range(20):
        res = await _call(status="running,pending,queued", limit=5, cursor=cursor)
        page = [j["id"] for j in res["jobs"]]
        if not page:
            break
        seen.extend(page)
        cursor = res.get("next_cursor")
        if not cursor:
            break

    assert len(seen) == len(set(seen)), "the legacy row must not be served twice"
    assert "job-legacy" in seen, (
        "a row with no created_at must still be restored, not 400 the walk"
    )
    assert len(seen) == 13, f"expected all 13 active jobs, got {len(seen)}: {seen}"


@pytest.mark.asyncio
async def test_malformed_cursor_is_rejected_not_silently_ignored():
    """A corrupt cursor must 400, never fall back to offset 0.

    Falling back would return page 1 again forever: the restore loop would see
    the same rows until its page bound, and the user would get an endless
    silent re-scan instead of an error.
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await _call(status="running", limit=10, cursor="not-a-cursor!!")
    assert excinfo.value.status_code == 400