# SPDX-License-Identifier: AGPL-3.0-only
"""F — durable batch admission, no orphan items, bounded recovery.

``POST /localization/batch`` admits N items by creating one Job row per file
and saving its upload. Two failure windows are pinned here:

* Admission failure — file 2 is rejected after file 1's Job row exists. The
  endpoint raises, so nothing is dispatched, and file 1's row is left
  ``pending`` forever: an orphan item that no runner will ever reach.
* Process death between admission and dispatch — every admitted row is
  ``pending`` with no runner attached. Recovery must re-dispatch them from the
  durable Job/manifest state instead of only flipping them to ``failed`` and
  dropping the queue.

The recovery contract itself is also pinned: recovery is idempotent, reclaims a
row at most once, and never revives a job whose upload bytes are gone.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api import localization as api
from backend.api import tools
from backend.database import AsyncSessionLocal, Base, engine
from backend.models.job import Job


# ── schema / DB ────────────────────────────────────────────────────────────


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


def _upload(name: str = "clip.mp4"):
    return SimpleNamespace(filename=name, read=_read)


def _read():
    async def _r():
        return b"video-bytes"

    return _r()


async def _rows():
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select

        rows = (await db.execute(select(Job))).scalars().all()
        return {str(row.id): row for row in rows}


def _patch_batch(monkeypatch, *, save_upload=None, dispatched=None, tmp_path=None):
    """Patch the batch endpoint's collaborators without a live upload/DB path."""
    from backend.agents import job_helper

    async def create_job(job_type, user_id="local", input_data=None):
        from uuid import uuid4

        async with AsyncSessionLocal() as db:
            job = Job(id=str(uuid4()), user_id=user_id, job_type=job_type,
                      status="pending", input_json=json.dumps(input_data or {}))
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return job

    async def fake_save_upload(file, job_id, allowed_exts, max_bytes, suffix=""):
        if save_upload is not None:
            return await save_upload(file, job_id, allowed_exts, max_bytes, suffix)
        target = tmp_path / "tools_in" / f"{job_id}.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video-bytes")
        return target

    monkeypatch.setattr(job_helper, "create_job", create_job)
    monkeypatch.setattr(tools, "save_upload", fake_save_upload)
    monkeypatch.setattr(
        api, "_run_localization_batch_sequential",
        lambda specs: dispatched.append(list(specs)) if dispatched is not None else None,
    )
    return create_job


# ══════════════════════════════════════════════════════════════════════════
# Batch admission failure must not orphan already-admitted items
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_upload_rejection_marks_earlier_items_failed(monkeypatch, tmp_path):
    """File 2 rejected -> file 1 must be terminal, not pending forever."""
    from fastapi import HTTPException

    calls = {"n": 0}

    async def save_upload(file, job_id, allowed_exts, max_bytes, suffix=""):
        calls["n"] += 1
        if calls["n"] == 2:
            raise HTTPException(413, "File too large")
        target = tmp_path / "tools_in" / f"{job_id}.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video-bytes")
        return target

    dispatched: list = []
    _patch_batch(monkeypatch, save_upload=save_upload,
                 dispatched=dispatched, tmp_path=tmp_path)

    with pytest.raises(HTTPException):
        await api.localization_batch(
            files=[_upload("a.mp4"), _upload("b.mp4")],
            target_language="vi", mode="full_dub", voice_id="",
            caption_style="viral", emoji_style="moderate",
            target_aspect=None, profile_id=None,
        )

    rows = await _rows()
    assert len(rows) == 2, "both admissions created a row before the rejection"
    for job in rows.values():
        assert job.status == "failed", (
            f"job {job.id} stayed {job.status} with no dispatcher attached"
        )
        assert job.error_message
    assert dispatched == [], "a rejected batch must not dispatch a partial queue"
    assert not list((tmp_path / "tools_in").glob("*.mp4")), (
        "rejected admission must reclaim earlier uploaded source files"
    )


@pytest.mark.asyncio
async def test_accepted_batch_dispatches_every_item(monkeypatch, tmp_path):
    """Control case: nothing regresses on the happy path."""
    dispatched: list = []
    _patch_batch(monkeypatch, dispatched=dispatched, tmp_path=tmp_path)

    result = await api.localization_batch(
        files=[_upload("a.mp4"), _upload("b.mp4")],
        target_language="vi", mode="full_dub", voice_id="v",
        caption_style="viral", emoji_style="moderate",
        target_aspect=None, profile_id=None,
    )

    assert result["count"] == 2
    assert len(dispatched) == 1, "one coordinator for the whole batch"
    assert {spec["job_id"] for spec in dispatched[0]} == {
        item["job_id"] for item in result["jobs"]
    }
    rows = await _rows()
    assert all(job.status == "pending" for job in rows.values())


# ══════════════════════════════════════════════════════════════════════════
# Durable restart recovery from the existing Job rows
# ══════════════════════════════════════════════════════════════════════════


async def _seed_pending(job_id: str, *, in_path: Path | None, batch_id: str = "b1",
                        index: int = 0, size: int = 1, admitted: bool = True,
                        status: str = "pending"):
    """Seed one batch row as ADMISSION actually leaves it.

    Recovery now requires the full marker set (``batch_admitted`` + a matching
    ``batch_size`` + a stable ``batch_index``) and refuses to auto-resume a
    legacy ``batch``-without-marker row, because such a row cannot be told
    apart from a batch that crashed mid-upload. Rows seeded here therefore
    carry what ``POST /localization/batch`` writes in its single marker
    transaction; ``admitted=False`` covers the partial-admission case.
    """
    payload = {
        "target_language": "Vietnamese", "mode": "full_dub",
        "voice_id": "", "caption_style": "viral",
        "emoji_style": "moderate", "target_aspect": None,
        "profile_id": None, "batch": True, "batch_id": batch_id,
        "batch_index": index, "batch_size": size,
        "filename": Path(in_path).name if in_path else "clip.mp4",
        "in_path": str(in_path) if in_path else None,
    }
    if admitted:
        payload["batch_admitted"] = True
    async with AsyncSessionLocal() as db:
        db.add(Job(
            id=job_id, user_id="local", job_type="tool:translate",
            status=status, title=job_id,
            input_json=json.dumps(payload),
        ))
        await db.commit()


@pytest.mark.asyncio
async def test_pending_batch_rows_carry_recoverable_input(tmp_path):
    """Admission must persist the input path BEFORE dispatch.

    Without it a restarted process has a pending Job row and no way to know
    which upload belongs to it, so the item can only be failed, never resumed.
    """
    dispatched: list = []
    from backend.agents import job_helper

    async def create_job(job_type, user_id="local", input_data=None):
        from uuid import uuid4

        async with AsyncSessionLocal() as db:
            job = Job(id=str(uuid4()), user_id=user_id, job_type=job_type,
                      status="pending", input_json=json.dumps(input_data or {}))
            db.add(job)
            await db.commit()
            await db.refresh(job)
            return job

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(job_helper, "create_job", create_job)

        async def fake_save_upload(file, job_id, allowed_exts, max_bytes, suffix=""):
            target = tmp_path / "tools_in" / f"{job_id}.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"video-bytes")
            return target

        monkey.setattr(tools, "save_upload", fake_save_upload)
        monkey.setattr(api, "_run_localization_batch_sequential",
                       lambda specs: dispatched.append(list(specs)))
        result = await api.localization_batch(
            files=[_upload("a.mp4")], target_language="vi", mode="full_dub",
            voice_id="", caption_style="viral", emoji_style="moderate",
            target_aspect=None, profile_id=None,
        )
    finally:
        monkey.undo()

    row = (await _rows())[result["jobs"][0]["job_id"]]
    payload = json.loads(str(row.input_json))
    assert payload["batch"] is True
    assert payload["in_path"], "the admitted upload path must be durable"
    assert Path(payload["in_path"]).is_file()


@pytest.mark.asyncio
async def test_restart_recovers_queued_batch_items(tmp_path, monkeypatch):
    """A restart must resume admitted-but-undispatched batch items."""
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "restored.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")
    await _seed_pending("job-recover", in_path=upload)

    dispatched: list = []
    monkeypatch.setattr(
        api, "_run_localization_batch_sequential",
        lambda specs: dispatched.append(list(specs)),
    )

    recovered = await recover_pending_localization_batch_items()

    assert recovered == ["job-recover"]
    assert len(dispatched) == 1
    assert {spec["job_id"] for spec in dispatched[0]} == {"job-recover"}


@pytest.mark.asyncio
async def test_restart_does_not_revive_item_with_missing_upload(tmp_path):
    """A vanished upload cannot be resumed; it must be failed with a reason."""
    from backend.database import recover_pending_localization_batch_items

    await _seed_pending("job-gone", in_path=tmp_path / "absent.mp4")

    dispatched: list = []
    import backend.core.task_runner as task_runner

    real_dispatch = task_runner.dispatch
    try:
        task_runner.dispatch = lambda coro: dispatched.append(coro)
        recovered = await recover_pending_localization_batch_items()
    finally:
        task_runner.dispatch = real_dispatch

    assert recovered == []
    assert dispatched == []
    row = (await _rows())["job-gone"]
    assert str(row.status) == "failed"
    assert row.error_message


@pytest.mark.asyncio
async def test_restart_recovery_claims_each_row_once(tmp_path, monkeypatch):
    """Two recovery passes must not produce two runs of the same item."""
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "once.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")
    await _seed_pending("job-once", in_path=upload)

    seen: list = []
    import backend.core.task_runner as task_runner

    real_dispatch = task_runner.dispatch
    try:
        task_runner.dispatch = lambda coro: seen.append(coro)
        first = await recover_pending_localization_batch_items()
        second = await recover_pending_localization_batch_items()
    finally:
        task_runner.dispatch = real_dispatch

    assert first == ["job-once"]
    assert second == [], "a claimed row must not be recovered again"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_restart_recovery_ignores_non_batch_and_active_rows(tmp_path):
    """Recovery is scoped to queued localization BATCH items only."""
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "scoped.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")

    # A queued batch item that IS recoverable.
    await _seed_pending("job-batch-pending", in_path=upload, batch_id="b-pending")
    # A single (non-batch) localization job.
    await _seed_pending("job-single", in_path=upload, batch_id="b-single")
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select

        row = (await db.execute(
            select(Job).where(Job.id == "job-single"))).scalar_one()
        payload = json.loads(str(row.input_json))
        payload.pop("batch", None)
        row.input_json = json.dumps(payload)
        await db.commit()
    # An already-running batch item (its own batch - batch ids group rows).
    await _seed_pending("job-batch-running", in_path=upload, batch_id="b-running")
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select

        row = (await db.execute(
            select(Job).where(Job.id == "job-batch-running"))).scalar_one()
        row.status = "running"
        await db.commit()

    dispatched: list = []
    import backend.core.task_runner as task_runner

    real_dispatch = task_runner.dispatch
    try:
        task_runner.dispatch = lambda coro: dispatched.append(coro)
        recovered = await recover_pending_localization_batch_items()
    finally:
        task_runner.dispatch = real_dispatch

    assert recovered == ["job-batch-pending"]
    assert len(dispatched) == 1


# ══════════════════════════════════════════════════════════════════════════
# Real process-restart evidence (isolated subprocess, own DATA_DIR)
# ══════════════════════════════════════════════════════════════════════════


_SUBPROCESS_SCRIPT = r"""
import asyncio, json, sys
from pathlib import Path

phase = sys.argv[1]
data_dir = Path(sys.argv[2])
db_file = data_dir / "viralmint.db"
upload = sys.argv[3]
out = Path(sys.argv[4])

if phase == "seed":
    # Stand in for the PREVIOUS process: bring the schema up, then admit one
    # batch item and die before any runner is attached.
    from backend.database import Base, engine
    import backend.models.job  # noqa: F401  (register the table)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())

    import sqlite3
    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO jobs (id, user_id, job_type, status, title, input_json,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,datetime('now'),datetime('now'))",
        ("job-restart", "local", "tool:translate", "pending", "job-restart",
         json.dumps({
             "target_language": "Vietnamese", "mode": "full_dub",
             "voice_id": "", "caption_style": "viral", "emoji_style": "moderate",
             "target_aspect": None, "profile_id": None, "batch": True,
             "batch_id": "b-restart", "filename": "restart.mp4",
             "batch_index": 0, "batch_size": 1, "batch_admitted": True,
             "in_path": upload,
         })),
    )
    conn.commit()
    conn.close()
    out.write_text(json.dumps({"seeded": True}), encoding="utf-8")
    raise SystemExit(0)

# phase == "recover": a genuinely separate interpreter on the same DB file.
captured = []
import backend.core.task_runner as tr
tr.dispatch = lambda coro: captured.append(coro) or coro.close()

from backend.database import recover_pending_localization_batch_items

async def main():
    first = await recover_pending_localization_batch_items()
    second = await recover_pending_localization_batch_items()
    return first, second

first, second = asyncio.run(main())

import sqlite3
conn = sqlite3.connect(db_file)
status, task_id = conn.execute(
    "SELECT status, task_id FROM jobs WHERE id='job-restart'"
).fetchone()
conn.close()

out.write_text(json.dumps({
    "first": first, "second": second, "dispatched": len(captured),
    "status": status, "task_id": task_id,
}), encoding="utf-8")
"""


def test_real_process_restart_recovers_batch_item(tmp_path):
    """End-to-end: a separate process recovers a batch item a dead one admitted.

    Two interpreters, one DB file. The first creates the schema, commits one
    admitted-but-undispatched batch row and exits; the second is a cold boot
    with no in-process knowledge of anything. Recovery must re-queue that row
    exactly once and leave a second pass with nothing to do.
    """
    import os
    import subprocess
    import sys

    scratch = Path(os.environ["VIRALMINT_DATA_DIR"])
    data_dir = tmp_path / "restart-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    upload = tmp_path / "tools_in" / "restart.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")

    script = tmp_path / "restart_child.py"
    script.write_text(_SUBPROCESS_SCRIPT, encoding="utf-8")
    app_dir = str(Path(__file__).resolve().parents[1])

    def _run(phase: str, out: Path) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "VIRALMINT_DATA_DIR": str(data_dir),
            "PYTHONPATH": app_dir,
        }
        return subprocess.run(
            [sys.executable, str(script), phase, str(data_dir), str(upload),
             str(out)],
            cwd=app_dir, env=env, capture_output=True, text=True, timeout=300,
        )

    seeded_out = tmp_path / "seed.json"
    seed_proc = _run("seed", seeded_out)
    assert seed_proc.returncode == 0, (
        f"seed process failed:\n{seed_proc.stdout}\n{seed_proc.stderr}"
    )

    out = tmp_path / "child_result.json"
    proc = _run("recover", out)
    assert proc.returncode == 0, (
        f"restart process failed:\n{proc.stdout}\n{proc.stderr}"
    )
    assert out.is_file(), f"child wrote no result:\n{proc.stdout}\n{proc.stderr}"

    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["first"] == ["job-restart"], result
    assert result["second"] == [], "a restarted process must not re-run it"
    assert result["dispatched"] == 1
    assert result["status"] == "pending"
    assert str(result["task_id"]).startswith("recovery_claim:batch:")
    assert scratch.is_dir()


@pytest.mark.asyncio
async def test_two_overlapping_recovery_passes_dispatch_one_owner(tmp_path,monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.selectable import Select
    from backend import database
    from backend.core import task_runner
    path=tmp_path/'concurrent.mp4'
    path.write_bytes(b'real admission bytes')
    await _seed_pending('job-concurrent',in_path=path)
    original=AsyncSession.execute
    both_read=asyncio.Event()
    readers=0
    async def synchronized_read(session,statement,*args,**kwargs):
        nonlocal readers
        result=await original(session,statement,*args,**kwargs)
        if isinstance(statement,Select) and readers<2:
            readers+=1
            if readers==2:
                both_read.set()
            await asyncio.wait_for(both_read.wait(),timeout=5)
        return result
    monkeypatch.setattr(AsyncSession,'execute',synchronized_read)
    dispatched=[]
    def capture(coro):
        dispatched.append(coro)
        coro.close()
    monkeypatch.setattr(task_runner,'dispatch',capture)
    first,second=await asyncio.gather(
        database.recover_pending_localization_batch_items(),
        database.recover_pending_localization_batch_items())
    assert first+second==['job-concurrent']
    assert len(dispatched)==1


# ══════════════════════════════════════════════════════════════════════════
# Terminal sibling bug: recovery must use ALL original batch members
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_recovery_with_terminal_success_sibling(tmp_path, monkeypatch):
    """A batch with a terminal SUCCESS sibling must still recover the pending item.

    The original bug: recovery only queried status='pending' rows, so a
    terminal sibling (success/failed/cancelled) was invisible. The completeness
    check compared batch_size against the count of PENDING rows only, making
    size != len(members) true whenever any sibling had already completed.
    Recovery then failed the remaining pending items as 'incomplete'.
    """
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "sibling.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")

    # Three-item batch: success plus two pending, seeded out of index order.
    await _seed_pending("job-sib-success", in_path=upload, batch_id="b-sib",
                        index=0, size=3, admitted=True, status="success")
    await _seed_pending("job-sib-pending-last", in_path=upload, batch_id="b-sib",
                        index=2, size=3, admitted=True, status="pending")
    await _seed_pending("job-sib-pending", in_path=upload, batch_id="b-sib",
                        index=1, size=3, admitted=True, status="pending")

    dispatched: list = []
    monkeypatch.setattr(
        api, "_run_localization_batch_sequential",
        lambda specs: dispatched.append(list(specs)),
    )

    recovered = await recover_pending_localization_batch_items()

    assert recovered == ["job-sib-pending", "job-sib-pending-last"], (
        "a terminal success sibling must not block recovery of pending items"
    )
    assert len(dispatched) == 1
    assert [spec["job_id"] for spec in dispatched[0]] == recovered
    assert str((await _rows())["job-sib-success"].status) == "success"
    assert await recover_pending_localization_batch_items() == []
    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_recovery_with_terminal_failed_sibling(tmp_path, monkeypatch):
    """A batch with a terminal FAILED sibling must still recover the pending item."""
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "sibling-f.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")

    await _seed_pending("job-sib-failed", in_path=upload, batch_id="b-sibf",
                        index=0, size=2, admitted=True, status="failed")
    await _seed_pending("job-sib-pending2", in_path=upload, batch_id="b-sibf",
                        index=1, size=2, admitted=True, status="pending")

    dispatched: list = []
    monkeypatch.setattr(
        api, "_run_localization_batch_sequential",
        lambda specs: dispatched.append(list(specs)),
    )

    recovered = await recover_pending_localization_batch_items()

    assert recovered == ["job-sib-pending2"], (
        "a terminal failed sibling must not block recovery of the pending item"
    )
    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_recovery_with_terminal_cancelled_sibling(tmp_path, monkeypatch):
    """A batch with a terminal CANCELLED sibling must still recover the pending item."""
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "sibling-c.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")

    await _seed_pending("job-sib-cancelled", in_path=upload, batch_id="b-sibc",
                        index=0, size=2, admitted=True, status="cancelled")
    await _seed_pending("job-sib-pending3", in_path=upload, batch_id="b-sibc",
                        index=1, size=2, admitted=True, status="pending")

    dispatched: list = []
    monkeypatch.setattr(
        api, "_run_localization_batch_sequential",
        lambda specs: dispatched.append(list(specs)),
    )

    recovered = await recover_pending_localization_batch_items()

    assert recovered == ["job-sib-pending3"], (
        "a terminal cancelled sibling must not block recovery of the pending item"
    )
    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_recovery_all_siblings_terminal_still_recovers_pending(tmp_path, monkeypatch):
    """A batch where ALL siblings are terminal must still recover the pending item.

    This is the extreme case: only one pending row exists in the DB, but the
    batch_size says 3. The original code saw 1 != 3 and failed the row.
    """
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "allterm.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")

    await _seed_pending("job-term-0", in_path=upload, batch_id="b-allterm",
                        index=0, size=3, admitted=True, status="success")
    await _seed_pending("job-term-1", in_path=upload, batch_id="b-allterm",
                        index=1, size=3, admitted=True, status="failed")
    await _seed_pending("job-term-pending", in_path=upload, batch_id="b-allterm",
                        index=2, size=3, admitted=True, status="pending")

    dispatched: list = []
    monkeypatch.setattr(
        api, "_run_localization_batch_sequential",
        lambda specs: dispatched.append(list(specs)),
    )

    recovered = await recover_pending_localization_batch_items()

    assert recovered == ["job-term-pending"], (
        "even when all siblings are terminal, the pending item must be recovered"
    )
    assert len(dispatched) == 1


@pytest.mark.asyncio
async def test_recovery_all_siblings_pending_still_works(tmp_path, monkeypatch):
    """Regression: a batch where ALL items are pending must still recover all of them."""
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "allpend.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")

    await _seed_pending("job-allp-0", in_path=upload, batch_id="b-allp",
                        index=0, size=3, admitted=True, status="pending")
    await _seed_pending("job-allp-1", in_path=upload, batch_id="b-allp",
                        index=1, size=3, admitted=True, status="pending")
    await _seed_pending("job-allp-2", in_path=upload, batch_id="b-allp",
                        index=2, size=3, admitted=True, status="pending")

    dispatched: list = []
    monkeypatch.setattr(
        api, "_run_localization_batch_sequential",
        lambda specs: dispatched.append(list(specs)),
    )

    recovered = await recover_pending_localization_batch_items()

    assert recovered == ["job-allp-0", "job-allp-1", "job-allp-2"]
    assert len(dispatched) == 1
    assert {spec["job_id"] for spec in dispatched[0]} == {
        "job-allp-0", "job-allp-1", "job-allp-2"
    }



@pytest.mark.asyncio
async def test_batch_auto_repair_and_cleanup_are_durable_at_admission(
    monkeypatch, tmp_path,
):
    dispatched = []
    _patch_batch(monkeypatch, dispatched=dispatched, tmp_path=tmp_path)
    result = await api.localization_batch(
        files=[_upload("clip.mp4")], target_language="vi",
        mode="full_dub", voice_id="", caption_style="viral",
        emoji_style="moderate", target_aspect=None, profile_id=None,
        auto_repair=True,
    )
    rows = await _rows()
    assert len(rows) == 1
    payload = json.loads(next(iter(rows.values())).input_json)
    assert payload["auto_repair"] is True
    assert payload["cleanup_input"] is True
    assert dispatched[0][0]["auto_repair"] is True
    assert dispatched[0][0]["cleanup_input"] is True
    assert dispatched[0][0]["job_id"] == result["jobs"][0]["job_id"]
