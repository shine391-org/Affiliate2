# SPDX-License-Identifier: AGPL-3.0-only
"""F — durable localization BATCH lifecycle: admission atomicity + ownership.

Root defects pinned here, all of which are invisible to the happy path:

Admission (``POST /localization/batch``)
  * Markers were committed ONE ROW AT A TIME, so a crash mid-loop left a
    partially marked batch: recovery saw "some admitted, some not" and had no
    truthful answer. The marker set is now written in ONE transaction.
  * ``emoji_style`` was never persisted, so a recovered item silently fell
    back to ``moderate`` regardless of what the user asked for.
  * No immutable ``profile_override`` snapshot: a recovered item re-resolved
    the profile against LIVE Settings, which can differ from admission.
  * No stable ``batch_index``, so the recovered queue had no defined order.
  * ``dispatch()`` ran OUTSIDE the admission try, so a dispatch failure left
    every row admitted + ``pending`` with no owner — an orphan queue.

Ownership / boot hand-off
  * ``init_db`` ran the zombie sweep BEFORE batch recovery, so a stale
    admitted batch was failed before recovery ever saw it.
  * Nothing recorded WHICH process owns an admitted/claimed row, so an
    overlapping boot could fail or steal the queued items of a LIVE
    coordinator. Ownership is now a recorded pid + a standard-library
    liveness check — no new service, schema, or dependency.
  * The coordinator cleared the recovery claim while the row was still
    ``pending``, opening a window where a sweep could fail a row the
    coordinator was about to run. The pending->running attach and the claim
    release are now ONE conditional write.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
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
    async def _r():
        return b"video-bytes"

    return SimpleNamespace(filename=name, read=_r)


async def _rows():
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Job))).scalars().all()
        return {str(row.id): row for row in rows}


def _payload(row) -> dict:
    return json.loads(str(row.input_json or "{}"))


async def _seed_profile_settings():
    """Give the admission probe a real Settings row + one saved profile."""
    from backend.database import AsyncSessionLocal as _sess, engine as _engine
    from backend.models.user_settings import UserSettings
    from backend.services.localization_service import LocalizationProfile

    # The profile declares its own translation route so materialization
    # snapshots a ROUTE IDENTITY rather than resolving a live credential - the
    # snapshot must be reproducible offline, which is the whole point of it.
    profile = LocalizationProfile(
        id="profile-under-test", name="Under test",
        translation_model="frozen-translation-model",
    )
    profile = profile.model_copy(update={
        "translation_routes": [
            {"connection_id": "conn-frozen", "model": "frozen-translation-model",
             "base_url": None},
        ],
    })

    # The module fixture only created the tables it touched; create_all here so
    # user_settings actually exists.
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _sess() as db:
        from sqlalchemy import select

        row = (await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )).scalar_one_or_none()
        if row is None:
            row = UserSettings(user_id="local")
            db.add(row)
        row.localization_profiles_json = json.dumps({
            "default_profile_id": profile.id,
            "profiles": [json.loads(profile.model_dump_json())],
        })
        await db.commit()


def _patch_admission(monkeypatch, tmp_path, *, dispatched=None, dispatch_impl=None):
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
    if dispatch_impl is not None:
        monkeypatch.setattr("backend.core.task_runner.dispatch", dispatch_impl)
    return create_job


async def _seed_batch_row(
    job_id: str,
    upload: Path,
    *,
    batch_id: str = "b-lifecycle",
    index: int = 0,
    size: int = 1,
    admitted: bool = True,
    owner_pid: int | None = None,
    age_seconds: int = 0,
    emoji_style: str = "extreme",
    extra: dict | None = None,
):
    payload = {
        "target_language": "Vietnamese", "mode": "full_dub",
        "voice_id": "", "caption_style": "viral",
        "emoji_style": emoji_style, "target_aspect": None,
        "profile_id": None, "batch": True, "batch_id": batch_id,
        "batch_index": index, "batch_size": size,
        "filename": Path(str(upload)).name, "in_path": str(upload),
    }
    if admitted:
        payload["batch_admitted"] = True
    if owner_pid is not None:
        payload["batch_owner_pid"] = owner_pid
    if extra:
        payload.update(extra)
    stamp = datetime.utcnow() - timedelta(seconds=age_seconds)
    async with AsyncSessionLocal() as db:
        db.add(Job(id=job_id, user_id="local", job_type="tool:translate",
                   status="pending", title=job_id,
                   input_json=json.dumps(payload),
                   created_at=stamp, updated_at=stamp))
        await db.commit()


def _capture_dispatch(monkeypatch):
    """Capture the coordinator coroutines instead of running them.

    The coroutine is deliberately NOT closed: tests that care about the
    coordinator's behaviour await it themselves, which is the only way to see
    what the runner observed.
    """
    captured: list = []

    def capture(coro):
        captured.append(coro)

    monkeypatch.setattr("backend.core.task_runner.dispatch", capture)
    return captured


async def _drain(captured: list) -> None:
    for coro in captured:
        await coro


# ══════════════════════════════════════════════════════════════════════════
# Admission contract
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_admission_markers_are_written_in_one_transaction(monkeypatch, tmp_path):
    """The whole marker set must land in ONE transaction.

    Committed one row at a time, a crash partway through left a batch that was
    genuinely indistinguishable from a partially uploaded one: recovery could
    not tell "crashed while marking" from "crashed while uploading", so it
    could neither resume nor refuse it truthfully. The observable is the
    transaction COUNT - one write for N rows, not N writes.
    """
    _patch_admission(monkeypatch, tmp_path)

    import backend.database as database

    real_sessions = database.AsyncSessionLocal
    real_write = api._write_batch_markers
    marker_sessions: list = []

    def counting_sessions(*args, **kwargs):
        session = real_sessions(*args, **kwargs)
        marker_sessions.append(session)
        return session

    async def counted_write(admitted, markers, *, expect_batch_admitted=True):
        # The marker write resolves AsyncSessionLocal from backend.database at
        # call time, so this counts the marker's OWN transactions and nothing
        # else on the admission path.
        marker_sessions.clear()
        monkeypatch.setattr(database, "AsyncSessionLocal", counting_sessions)
        try:
            return await real_write(
                admitted, markers, expect_batch_admitted=expect_batch_admitted)
        finally:
            monkeypatch.setattr(database, "AsyncSessionLocal", real_sessions)

    monkeypatch.setattr(api, "_write_batch_markers", counted_write)

    await api.localization_batch(
        files=[_upload("a.mp4"), _upload("b.mp4"), _upload("c.mp4")],
        target_language="vi", mode="full_dub", voice_id="",
        caption_style="viral", emoji_style="moderate",
        target_aspect=None, profile_id=None,
    )

    rows = await _rows()
    assert len(rows) == 3
    assert all(_payload(row).get("batch_admitted") is True for row in rows.values())
    assert len(marker_sessions) == 1, (
        f"the marker set used {len(marker_sessions)} transactions for 3 rows; "
        f"a crash partway through would leave a half-marked batch"
    )


@pytest.mark.asyncio
async def test_marker_write_failure_leaves_no_admitted_rows(monkeypatch, tmp_path):
    """A crash inside the marker write must not leave a half-admitted batch."""
    _patch_admission(monkeypatch, tmp_path)

    async def boom(admitted, markers, *, expect_batch_admitted=True):
        raise RuntimeError("simulated crash while writing markers")

    monkeypatch.setattr(api, "_write_batch_markers", boom)

    with pytest.raises(RuntimeError):
        await api.localization_batch(
            files=[_upload("a.mp4"), _upload("b.mp4")],
            target_language="vi", mode="full_dub", voice_id="",
            caption_style="viral", emoji_style="extreme",
            target_aspect=None, profile_id=None,
        )

    rows = await _rows()
    assert len(rows) == 2
    for row in rows.values():
        assert _payload(row).get("batch_admitted") is not True, (
            "a partially written marker set survived"
        )
        assert row.status == "failed"
        assert row.error_message


@pytest.mark.asyncio
async def test_admission_persists_emoji_style_index_and_profile_snapshot(monkeypatch, tmp_path):
    """Everything the recovered item needs must be durable at admission.

    Seeded Settings + a saved profile so the snapshot is a REAL resolved
    profile. Without a Settings row the admission probe legitimately produces
    ``None``, which would make this assertion vacuous.
    """
    monkeypatch.setattr(
        "backend.services.localization_service._profile_ai_route_clients",
        lambda settings_row, prof, purpose="translation": [object()],
    )
    await _seed_profile_settings()
    _patch_admission(monkeypatch, tmp_path)
    dispatched: list = []
    monkeypatch.setattr(
        api, "_run_localization_batch_sequential",
        lambda specs: dispatched.append(list(specs)),
    )

    result = await api.localization_batch(
        files=[_upload("a.mp4"), _upload("b.mp4")],
        target_language="vi", mode="full_dub", voice_id="v",
        caption_style="viral", emoji_style="extreme",
        target_aspect=None, profile_id=None,
    )

    rows = await _rows()
    assert len(rows) == 2
    payloads = [_payload(row) for row in rows.values()]
    for payload in payloads:
        assert payload["batch_admitted"] is True
        assert payload["batch_size"] == 2
        assert payload["emoji_style"] == "extreme", (
            "emoji_style is part of the job; recovery must not guess it"
        )
        assert payload.get("batch_owner_pid") == os.getpid(), (
            "the admitting process must own the rows it created"
        )
        override = payload.get("profile_override")
        assert isinstance(override, dict), (
            f"the resolved profile snapshot must be durable at admission, got "
            f"{type(override).__name__}"
        )
        assert override.get("id") == "profile-under-test", (
            "the snapshot must be the profile RESOLVED at admission, so a later "
            "Settings edit cannot change what a recovered batch runs"
        )
        assert isinstance(payload.get("batch_index"), int)
    assert sorted(p["batch_index"] for p in payloads) == [0, 1]
    assert all(row.status == "pending" for row in rows.values())


@pytest.mark.asyncio
async def test_dispatch_failure_leaves_no_pending_orphan_rows(monkeypatch, tmp_path):
    """dispatch() runs inside the admission try, so its failure is terminal."""
    _patch_admission(
        monkeypatch, tmp_path,
        dispatch_impl=lambda coro: (_ for _ in ()).throw(RuntimeError("no worker slots")),
    )

    with pytest.raises(RuntimeError):
        await api.localization_batch(
            files=[_upload("a.mp4"), _upload("b.mp4")],
            target_language="vi", mode="full_dub", voice_id="",
            caption_style="viral", emoji_style="moderate",
            target_aspect=None, profile_id=None,
        )

    rows = await _rows()
    assert len(rows) == 2
    for row in rows.values():
        assert row.status == "failed", (
            f"{row.id} stayed {row.status} after dispatch failed — orphan queue"
        )
        assert row.error_message


# ══════════════════════════════════════════════════════════════════════════
# Recovery contract
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_recovery_refuses_a_partial_batch(tmp_path, monkeypatch):
    """A batch whose marker set is incomplete must not be auto-resumed."""
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "partial.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")

    await _seed_batch_row("job-p-ok", upload, index=0, size=2, admitted=True,
                          owner_pid=None, age_seconds=999)
    await _seed_batch_row("job-p-missing", upload, index=1, size=2, admitted=False,
                          owner_pid=None, age_seconds=999)

    captured = _capture_dispatch(monkeypatch)
    recovered = await recover_pending_localization_batch_items()

    assert recovered == [], "an incomplete batch must not be silently resumed"
    assert captured == []
    for job_id in ("job-p-ok", "job-p-missing"):
        row = (await _rows())[job_id]
        assert str(row.status) == "failed"
        assert row.error_message


@pytest.mark.asyncio
async def test_recovery_requires_a_matching_batch_size(tmp_path, monkeypatch):
    """batch_size is the admission truth; a short group is not resumable."""
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "short.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")
    await _seed_batch_row("job-short", upload, index=0, size=3, admitted=True,
                          owner_pid=None, age_seconds=999)

    captured = _capture_dispatch(monkeypatch)
    recovered = await recover_pending_localization_batch_items()

    assert recovered == []
    assert captured == []
    row = (await _rows())["job-short"]
    assert str(row.status) == "failed"
    assert row.error_message


@pytest.mark.asyncio
async def test_recovery_queues_in_stable_batch_index_order(tmp_path, monkeypatch):
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "ordered.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")
    for job_id, idx in (("job-c", 2), ("job-a", 0), ("job-b", 1)):
        await _seed_batch_row(job_id, upload, index=idx, size=3, admitted=True,
                              owner_pid=None, age_seconds=999)

    specs: list[list[dict]] = []

    # Capture the coordinator's SPECS by intercepting at the runner boundary,
    # which is what the queue order actually determines.
    monkeypatch.setattr(
        api, "_run_localization_batch_sequential",
        lambda specs_in: specs.append([dict(s) for s in specs_in]),
    )
    recovered = await recover_pending_localization_batch_items()

    assert recovered == ["job-a", "job-b", "job-c"]
    assert len(specs) == 1, "one coordinator per batch"
    assert [s["job_id"] for s in specs[0]] == ["job-a", "job-b", "job-c"], (
        "the recovered queue must run in admission order"
    )


@pytest.mark.asyncio
async def test_recovery_carries_the_admission_snapshot(tmp_path, monkeypatch):
    """The recovered spec uses the snapshotted profile/emoji, not live state."""
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "snap.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")
    snapshot = {"id": "p1", "target_language": "Vietnamese", "model": "frozen-model"}
    await _seed_batch_row("job-snap", upload, index=0, size=1, admitted=True,
                          owner_pid=None, age_seconds=999,
                          emoji_style="extreme",
                          extra={"profile_override": snapshot})

    captured = _capture_dispatch(monkeypatch)
    assert await recover_pending_localization_batch_items() == ["job-snap"]
    await _drain(captured)

    row = (await _rows())["job-snap"]
    payload = _payload(row)
    assert payload["emoji_style"] == "extreme"
    assert payload["profile_override"]["model"] == "frozen-model", (
        "recovery must replay the admission snapshot"
    )
    assert payload["batch_owner_pid"] == os.getpid()


@pytest.mark.asyncio
async def test_recovery_never_steals_a_live_coordinators_queue(tmp_path, monkeypatch):
    """An overlapping boot must leave a live coordinator's rows alone."""
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "live.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")
    await _seed_batch_row("job-live", upload, index=0, size=1, admitted=True,
                          owner_pid=os.getpid(), age_seconds=9999)

    captured = _capture_dispatch(monkeypatch)
    recovered = await recover_pending_localization_batch_items()

    assert recovered == [], "a live owner's row must never be reclaimed"
    assert captured == []
    row = (await _rows())["job-live"]
    assert str(row.status) == "pending"
    assert _payload(row)["batch_owner_pid"] == os.getpid(), "ownership must not move"


@pytest.mark.asyncio
async def test_sweep_never_orphan_fails_a_live_owners_queued_item(tmp_path, monkeypatch):
    """The boot/handoff sweep must leave a live coordinator's queue intact."""
    from backend.database import sweep_stale_jobs

    upload = tmp_path / "tools_in" / "queued.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")
    await _seed_batch_row("job-queued", upload, index=1, size=2, admitted=True,
                          owner_pid=os.getpid(), age_seconds=9999)

    swept, still = await sweep_stale_jobs()

    assert swept == 0
    assert "job-queued" in still
    assert str((await _rows())["job-queued"].status) == "pending"


# ══════════════════════════════════════════════════════════════════════════
# Coordinator: atomic pending->running attach before the claim is released
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_coordinator_attaches_running_before_releasing_the_claim(monkeypatch, tmp_path):
    """The runner must never observe a pending row with no owner."""
    upload = tmp_path / "tools_in" / "attach.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")
    await _seed_batch_row("job-attach", upload, index=0, size=1, admitted=True,
                          owner_pid=None, age_seconds=999)

    from backend.core import tool_runners

    observed: dict = {}

    async def fake_run(**kwargs):
        rows = await _rows()
        row = rows["job-attach"]
        observed["status"] = str(row.status)
        observed["task_id"] = row.task_id
        observed["kwargs"] = kwargs

    monkeypatch.setattr(tool_runners, "run_tool_translate", fake_run)

    from backend.database import recover_pending_localization_batch_items
    captured = _capture_dispatch(monkeypatch)
    await recover_pending_localization_batch_items()
    assert len(captured) == 1
    await _drain(captured)       # run the recovered coordinator for real

    assert observed.get("status") == "running", (
        "the coordinator released the claim while the row was still pending"
    )
    assert observed.get("task_id") in (None, "")


# ══════════════════════════════════════════════════════════════════════════
# Boot-level evidence: two interpreters, real init_db, isolated data dir
# ══════════════════════════════════════════════════════════════════════════

_CHILD = r'''
import json, os, sqlite3, sys
from pathlib import Path

phase = sys.argv[1]
data_dir = Path(sys.argv[2])
upload = sys.argv[3]
out = Path(sys.argv[4])

def dump():
    conn = sqlite3.connect(str(data_dir / "viralmint.db"))
    try:
        return conn.execute(
            "SELECT id, status, task_id, input_json FROM jobs ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

if phase == "seed":
    from backend.database import Base, engine
    import backend.models.job  # noqa: F401

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    import asyncio
    asyncio.run(_create())

    # Stand in for the PREVIOUS process: an admitted batch, coordinator
    # owner recorded as THIS pid, then death. The rows are stale on purpose so
    # the boot sweep would fail them if it ran first.
    stamp = "2020-01-01 00:00:00.000000"
    conn = sqlite3.connect(str(data_dir / "viralmint.db"))
    rows = []
    for idx, job_id in enumerate(["job-dead-0", "job-dead-1"]):
        payload = {
            "target_language": "Vietnamese", "mode": "full_dub", "voice_id": "",
            "caption_style": "viral", "emoji_style": "extreme",
            "target_aspect": None, "profile_id": None, "batch": True,
            "batch_id": "b-dead", "batch_index": idx, "batch_size": 2,
            "batch_admitted": True, "batch_owner_pid": os.getpid(),
            "filename": "dead.mp4", "in_path": upload,
        }
        rows.append((job_id, "local", "tool:translate", "pending", job_id,
                     json.dumps(payload), stamp, stamp))
    conn.executemany(
        "INSERT INTO jobs (id, user_id, job_type, status, title, input_json,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    out.write_text(json.dumps({"seeded": [r[0] for r in rows]}), encoding="utf-8")
    raise SystemExit(0)

if phase == "seed-live":
    from backend.database import Base, engine
    import backend.models.job  # noqa: F401

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    import asyncio
    asyncio.run(_create())

    stamp = "2020-01-01 00:00:00.000000"
    conn = sqlite3.connect(str(data_dir / "viralmint.db"))
    payload = {
        "target_language": "Vietnamese", "mode": "full_dub", "voice_id": "",
        "caption_style": "viral", "emoji_style": "extreme",
        "target_aspect": None, "profile_id": None, "batch": True,
        "batch_id": "b-live", "batch_index": 0, "batch_size": 1,
        "batch_admitted": True, "batch_owner_pid": int(sys.argv[5]),
        "filename": "live.mp4", "in_path": upload,
    }
    conn.execute(
        "INSERT INTO jobs (id, user_id, job_type, status, title, input_json,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("job-livepred", "local", "tool:translate", "pending", "job-livepred",
         json.dumps(payload), stamp, stamp))
    conn.commit()
    conn.close()
    out.write_text(json.dumps({"seeded": True}), encoding="utf-8")
    raise SystemExit(0)

# phase == "boot": a genuinely cold boot. init_db decides the outcome.
captured = []
import backend.core.task_runner as tr
tr.dispatch = lambda coro: captured.append(coro) or coro.close()

from backend.database import init_db, BOOT_FRESH_JOB_IDS
import asyncio
asyncio.run(init_db())

out.write_text(json.dumps({
    "boot_pid": os.getpid(),
    "rows": [
        {"id": r[0], "status": r[1], "task_id": r[2],
         "payload": json.loads(r[3] or "{}")}
        for r in dump()
    ],
    "dispatched": len(captured),
    "boot_fresh": list(BOOT_FRESH_JOB_IDS),
}), encoding="utf-8")
'''


def _run_child(tmp_path, data_dir: Path, phase: str, upload: Path, *, extra=None):
    script = tmp_path / "lifecycle_child.py"
    script.write_text(_CHILD, encoding="utf-8")
    out = tmp_path / f"child_{phase}.json"
    app_dir = str(Path(__file__).resolve().parents[1])
    env = {
        **os.environ,
        "VIRALMINT_DATA_DIR": str(data_dir),
        "PYTHONPATH": app_dir,
    }
    cmd = [sys.executable, str(script), phase, str(data_dir), str(upload), str(out)]
    if extra:
        cmd.append(str(extra))
    proc = subprocess.run(cmd, cwd=app_dir, env=env, capture_output=True,
                          text=True, timeout=300)
    assert proc.returncode == 0, (
        f"{phase} child failed:\n{proc.stdout}\n{proc.stderr}"
    )
    assert out.is_file(), f"{phase} child wrote no result:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(out.read_text(encoding="utf-8"))


def test_boot_recovers_a_dead_admitted_batch_instead_of_sweeping_it(tmp_path):
    """Cold boot: recovery runs BEFORE the sweep, so nothing is thrown away."""
    data_dir = tmp_path / "boot-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    upload = tmp_path / "tools_in" / "boot.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")

    seeded = _run_child(tmp_path, data_dir, "seed", upload)
    assert seeded["seeded"] == ["job-dead-0", "job-dead-1"]

    result = _run_child(tmp_path, data_dir, "boot", upload)
    rows = {r["id"]: r for r in result["rows"]}

    for job_id in ("job-dead-0", "job-dead-1"):
        assert rows[job_id]["status"] == "pending", (
            f"{job_id} was failed by the boot sweep before recovery ran"
        )
        assert str(rows[job_id]["task_id"] or "").startswith("recovery_claim:batch:")
        assert rows[job_id]["payload"]["batch_owner_pid"] == result["boot_pid"], (
            "recovery must record ITSELF as the new owner"
        )
    assert result["dispatched"] == 1, "one coordinator for the recovered batch"


def test_boot_leaves_a_live_predecessors_batch_untouched(tmp_path):
    """Negative: a fresh owner still running keeps its queue, claimed or not."""
    data_dir = tmp_path / "boot-live"
    data_dir.mkdir(parents=True, exist_ok=True)
    upload = tmp_path / "tools_in" / "bootlive.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")

    # The pytest interpreter is very much alive — it stands in for a draining
    # predecessor still working through its own batch.
    _run_child(tmp_path, data_dir, "seed-live", upload, extra=os.getpid())

    result = _run_child(tmp_path, data_dir, "boot", upload)
    rows = {r["id"]: r for r in result["rows"]}

    assert rows["job-livepred"]["status"] == "pending"
    assert rows["job-livepred"]["task_id"] is None, "a live owner was stripped of its claim"
    assert rows["job-livepred"]["payload"]["batch_owner_pid"] == os.getpid(), (
        "ownership of a live predecessor's row must not move"
    )
    assert result["dispatched"] == 0, "a live predecessor's batch was stolen"


# ══════════════════════════════════════════════════════════════════════════
# Windows liveness: the probe must OBSERVE, never signal
# ══════════════════════════════════════════════════════════════════════════
#
# `_process_is_alive` gates ownership, and every caller passes it a pid that was
# recorded in the database - i.e. the pid of a LIVE coordinator. On Windows
# `os.kill(pid, 0)` is not the POSIX existence probe: CPython's os_kill_impl
# branches on `sig`, and `CTRL_C_EVENT == 0`, so signal 0 goes out as
# GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid) against that pid's console
# process group - a real console event aimed at the process the gate is
# protecting. Every other signal value (not 0, not CTRL_BREAK_EVENT) is
# OpenProcess(PROCESS_ALL_ACCESS) + TerminateProcess(handle, sig).
#
# These tests therefore pin the property that matters: a throwaway child we
# spawn ourselves must still be ALIVE after the production probe has run, and
# must keep answering True. The child is ours; no foreign pid is ever touched.

# A marker FILE, not a pipe: Windows `select()` only accepts sockets, and the
# event channel has to survive a child that is being probed for death.
_PROBE_CHILD = r'''
import os, signal, sys, time
from pathlib import Path

flag = Path(sys.argv[1])
exit_code = int(sys.argv[2]) if len(sys.argv) > 2 else None

def _note(which):
    def handler(signum, frame):
        flag.write_text(flag.read_text() + which + "\n", encoding="utf-8")
    return handler

# Default SIGINT/SIGBREAK disposition would let a stray console event kill the
# child outright; recording instead means a delivered event is still caught.
signal.signal(signal.SIGINT, _note("SIGINT"))
try:
    signal.signal(signal.SIGBREAK, _note("SIGBREAK"))
except (AttributeError, ValueError):
    pass
# The pid the interpreter ACTUALLY runs under, which is not necessarily the pid
# Popen hands back on Windows (a venv launcher can be a separate process).
flag.write_text(f"up\n{os.getpid()}\n", encoding="utf-8")
if exit_code is not None:
    time.sleep(0.2)
    raise SystemExit(exit_code)
time.sleep(300)
'''


def _child_interpreter() -> str:
    """The interpreter that really executes the child code.

    In a Windows venv, ``sys.executable`` can be a launcher shim that spawns the
    real interpreter as a separate process, so ``Popen(...).pid`` may name the
    shim rather than the process whose liveness we care about. ``_base_executable``
    is the underlying real interpreter.
    """
    return getattr(sys, "_base_executable", None) or sys.executable


class _LiveChild:
    """A throwaway child process of OUR OWN that must outlive the probe.

    Signals are recorded in ``marker`` so a delivered console event is detected
    even when the child survives it (a pipe would also deadlock, since Windows
    ``select()`` rejects pipes). ``pid`` is the pid the child REPORTED, i.e. the
    process actually executing it.
    """

    def __init__(self, tmp_path, *, exit_code: int | None = None):
        self.marker = Path(str(tmp_path)) / f"probe_child_{exit_code}.txt"
        self.marker.write_text("", encoding="utf-8")
        argv = [_child_interpreter(), "-u", "-c", _PROBE_CHILD, str(self.marker)]
        if exit_code is not None:
            argv.append(str(exit_code))
        self._proc = subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.pid = 0
        deadline = time.time() + 120
        while time.time() < deadline:
            fields = self.marker.read_text(encoding="utf-8").split()
            if fields[:1] == ["up"] and len(fields) > 1:
                self.pid = int(fields[1])
                break
            if self._proc.poll() is not None:
                raise AssertionError(
                    f"probe child exited at startup: rc={self._proc.returncode}"
                )
            time.sleep(0.05)
        else:
            self.close()
            raise AssertionError("probe child never reported readiness")
        if self.pid <= 0:
            self.close()
            raise AssertionError("probe child reported no pid")

    def wait_for_exit(self, timeout_s: float = 60) -> int | None:
        return self._proc.wait(timeout=timeout_s)

    def delivered_events(self) -> list[str]:
        """Console events the child received. Any entry is a FAILURE."""
        seen = self.marker.read_text(encoding="utf-8").split()
        return [line for line in seen if line not in ("up", str(self.pid))]

    def alive(self) -> bool:
        return self._proc.poll() is None

    def close(self) -> None:
        if self.alive():
            self._proc.terminate()
            try:
                self._proc.wait(timeout=30)
            except Exception:
                self._proc.kill()
                self._proc.wait(timeout=30)


def test_liveness_probe_does_not_kill_the_live_process_it_probes(tmp_path):
    """The probe is an OBSERVATION: the child survives it, repeatedly."""
    from backend.database import _process_is_alive

    child = _LiveChild(tmp_path)
    try:
        assert child.alive(), "child died before the probe ran"

        answers = [_process_is_alive(child.pid) for _ in range(5)]

        assert answers == [True] * 5, (
            f"a live child was reported dead by the probe: {answers}"
        )
        assert child.alive(), (
            "liveness probing KILLED the process it probed - on Windows "
            "os.kill(pid, 0) is a console event / TerminateProcess, not a probe"
        )
        time.sleep(1.0)      # give a delivered console event time to land
        assert not child.delivered_events(), (
            f"the probe delivered console events to the child: "
            f"{child.delivered_events()}"
        )
    finally:
        child.close()


def test_the_probed_pid_is_the_interpreter_that_actually_runs_the_child(tmp_path):
    """The proof must name the real process, not a venv launcher shim.

    On Windows a venv ``python.exe`` can be a launcher that spawns the real
    interpreter, so ``Popen(...).pid`` may be the shim's pid. The child reports
    ``os.getpid()`` itself, and that reported pid is what every liveness
    assertion in this file uses - so the evidence is about the process actually
    executing the child code.
    """
    from backend.database import _process_is_alive

    child = _LiveChild(tmp_path)
    try:
        assert child.pid > 0, "no pid reported by the child"
        assert _process_is_alive(child.pid) is True, (
            f"the real executing child pid {child.pid} was not seen as alive"
        )
        # Independent confirmation that this pid is a live python process: the
        # child itself is still running, and the reported pid is not this
        # interpreter (no foreign/self-pid ambiguity in the evidence).
        assert child._proc.poll() is None
        assert child.pid != os.getpid(), (
            "the child reported the test runner's own pid, so the evidence "
            "would be measuring this process instead of a separate one"
        )
    finally:
        child.close()


def test_liveness_probe_reports_an_exited_child_as_dead(tmp_path):
    """A pid that has actually exited must read as dead, not leaked as alive."""
    from backend.database import _process_is_alive

    child = _LiveChild(tmp_path)
    dead_pid = child.pid
    child._proc.terminate()
    child._proc.wait(timeout=60)
    assert not child.alive()

    # A reaped pid is exactly the shape recovery must not mistake for an owner.
    assert _process_is_alive(dead_pid) is False, (
        "an exited pid was reported alive, so recovery would never reclaim it"
    )


def test_liveness_probe_rejects_pids_that_cannot_name_a_process():
    """Unparseable, non-positive and out-of-range pids are never alive."""
    from backend.database import _process_is_alive

    for bad in (None, "", "nope", 0, -1, -99999, object()):
        assert _process_is_alive(bad) is False, f"{bad!r} must not be alive"


def test_liveness_probe_never_uses_os_kill_on_windows(monkeypatch, tmp_path):
    """Structural guard: no os.kill at all on Windows, whatever the pid."""
    import backend.database as database

    if os.name != "nt":
        pytest.skip("Windows-only guarantee")

    def _explode(pid, sig):  # pragma: no cover - must never run
        raise AssertionError(
            f"os.kill({pid}, {sig}) on Windows is a console event or "
            f"TerminateProcess, not a liveness probe"
        )

    monkeypatch.setattr(database.os, "kill", _explode)
    child = _LiveChild(tmp_path)
    try:
        assert database._process_is_alive(child.pid) is True
        assert database._process_is_alive(999_999) is False
        assert child.alive(), "the child died during a no-kill probe"
    finally:
        child.close()


def test_windows_probe_closes_the_handle_and_asks_for_least_privilege():
    """The handle must be closed on every path, and the access mask minimal."""
    import backend.database as database

    if os.name != "nt":
        pytest.skip("Windows-only guarantee")

    seen: dict = {"closed": 0, "opened": [], "waits": []}

    class _FakeKernel32:
        def OpenProcess(self, access, inherit, pid):
            seen["opened"].append((access, pid))
            return 4242

        def GetExitCodeProcess(self, handle, code_ptr):  # pragma: no cover
            raise AssertionError(
                "WaitForSingleObject must be the PRIMARY test; exit code 259 "
                "(STILL_ACTIVE) is indistinguishable from a live process"
            )

        def WaitForSingleObject(self, handle, ms):
            seen["waits"].append((handle, ms))
            return database._WIN_WAIT_TIMEOUT

        def CloseHandle(self, handle):
            seen["closed"] += 1
            return 1

    assert database._windows_process_is_alive(1234, _api=_FakeKernel32()) is True

    access, pid = seen["opened"][0]
    assert access & database._WIN_SYNCHRONIZE, (
        "WaitForSingleObject on a process handle requires SYNCHRONIZE"
    )
    assert access & database._WIN_PROCESS_QUERY_LIMITED_INFORMATION, (
        "PROCESS_QUERY_LIMITED_INFORMATION is the least privilege that answers "
        "the question; PROCESS_ALL_ACCESS is not needed and not acceptable"
    )
    assert access & 0x000F0000 == 0, (
        "PROCESS_ALL_ACCESS bits must never be requested for a liveness read"
    )
    assert pid == 1234
    assert seen["waits"] == [(4242, 0)], (
        f"the wait must be a zero-timeout ASK on the opened handle: {seen['waits']}"
    )
    assert seen["closed"] == 1, "the process handle leaked"


def test_windows_probe_reads_a_signalled_object_as_dead():
    """WAIT_OBJECT_0 means the process has exited."""
    import backend.database as database

    if os.name != "nt":
        pytest.skip("Windows-only guarantee")

    class _FakeKernel32:
        def OpenProcess(self, access, inherit, pid):
            return 77

        def GetExitCodeProcess(self, handle, code_ptr):  # pragma: no cover
            raise AssertionError("must not consult the exit code first")

        def WaitForSingleObject(self, handle, ms):
            return database._WIN_WAIT_OBJECT_0

        def CloseHandle(self, handle):
            return 1

    assert database._windows_process_is_alive(1234, _api=_FakeKernel32()) is False


def test_windows_probe_does_not_mistake_exit_code_259_for_a_live_process(tmp_path):
    """STILL_ACTIVE == 259, so an exit code of 259 must still read as DEAD.

    This is the reason ``WaitForSingleObject`` is primary: the exit code of a
    process that exited with 259 is 259, which is exactly what a RUNNING
    process reports. Only the object state tells them apart.
    """
    from backend.database import _process_is_alive

    if os.name != "nt":
        pytest.skip("Windows-only guarantee")

    child = _LiveChild(tmp_path, exit_code=259)
    assert child.wait_for_exit(60) == 259, "child did not exit with 259"
    assert not child.alive()

    assert _process_is_alive(child.pid) is False, (
        "a process that exited with code 259 (== STILL_ACTIVE) was reported "
        "alive; recovery would never reclaim its rows"
    )


def test_windows_probe_falls_back_to_the_exit_code_only_after_wait_failed():
    """WAIT_FAILED is the only path that may consult GetExitCodeProcess."""
    import backend.database as database

    if os.name != "nt":
        pytest.skip("Windows-only guarantee")

    class _StuckStillActive:
        def OpenProcess(self, access, inherit, pid):
            return 55

        def GetExitCodeProcess(self, handle, code_ptr):
            code_ptr._obj.value = database._WIN_STILL_ACTIVE
            return 1

        def WaitForSingleObject(self, handle, ms):
            return database._WIN_WAIT_FAILED

        def CloseHandle(self, handle):
            return 1

    class _FinishedExitCode:
        def OpenProcess(self, access, inherit, pid):
            return 56

        def GetExitCodeProcess(self, handle, code_ptr):
            code_ptr._obj.value = 3
            return 1

        def WaitForSingleObject(self, handle, ms):
            return database._WIN_WAIT_FAILED

        def CloseHandle(self, handle):
            return 1

    # Nothing proven -> do not take the row away from its owner.
    assert database._windows_process_is_alive(1234, _api=_StuckStillActive()) is True
    # A real exit code is proof of death even when the wait failed.
    assert database._windows_process_is_alive(1234, _api=_FinishedExitCode()) is False


def test_windows_probe_fails_safe_to_alive_when_the_answer_is_unobtainable():
    """Ownership must be PROVABLE before a row is taken - so default to alive."""
    import ctypes

    import backend.database as database

    if os.name != "nt":
        pytest.skip("Windows-only guarantee")

    class _Denied:
        def OpenProcess(self, access, inherit, pid):
            ctypes.set_last_error(database._WIN_ERROR_ACCESS_DENIED)
            return 0

        def CloseHandle(self, handle):  # pragma: no cover
            raise AssertionError("CloseHandle on a failed open")

    class _NoSuchPid:
        def OpenProcess(self, access, inherit, pid):
            ctypes.set_last_error(database._WIN_ERROR_INVALID_PARAMETER)
            return 0

        def CloseHandle(self, handle):  # pragma: no cover
            raise AssertionError("CloseHandle on a failed open")

    assert database._windows_process_is_alive(1234, _api=_Denied()) is True, (
        "an existing but unopenable process must read as alive"
    )
    assert database._windows_process_is_alive(1234, _api=_NoSuchPid()) is False