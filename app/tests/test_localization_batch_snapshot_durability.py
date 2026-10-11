# SPDX-License-Identifier: AGPL-3.0-only
"""R2 — batch admission must not silently lose the reproducibility snapshot.

``POST /localization/batch`` snapshots the RESOLVED localization profile at
admission so a recovered item replays exactly what was admitted instead of
re-resolving against live Settings after a crash/restart.

The snapshot probe wrapped itself in ``except Exception`` and continued with
``profile_override=None``. That made TWO very different outcomes
indistinguishable in the persisted row:

* a genuinely absent Settings row, where the runner resolving ``profile_id``
  at recovery time is exactly as reproducible as the snapshot; and
* a MISCONFIGURED profile (deleted connection, bad route, unresolvable model),
  which is deterministic and would raise the SAME way on every recovery
  attempt - yet the admission swallowed it and admitted the batch anyway,
  burning an upload and a heavy slot to produce a job that can never resolve.

Both outcomes persisted the identical durable marker set, so the row, the
recovery log and the user all had no way to tell "no snapshot yet" from
"snapshot is unreproducible". Nothing crashed; the failure was invisible.

These tests pin the classification the admission must record and the
durability of that record.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api import localization as api
from backend.agents import job_helper
from backend.api import tools
from backend.database import AsyncSessionLocal, Base, engine
from backend.models.job import Job
# Imported at module scope so its table is registered in Base.metadata BEFORE
# create_all runs. A create_all that runs before the model is imported simply
# omits user_settings, and the admission probe then fails with "no such table"
# instead of exercising the branch under test.
from backend.models.user_settings import UserSettings


async def _create_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())


@pytest.fixture(autouse=True)
def _clean_rows():
    """Jobs AND Settings must be wiped: the admission probe reads the Settings
    row, so a row left by a sibling test turns the next test's setup into a
    duplicate-key failure instead of the branch it intends to exercise."""
    async def _wipe():
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select

            for row in (await db.execute(select(Job))).scalars().all():
                await db.delete(row)
            for row in (await db.execute(
                select(UserSettings).where(UserSettings.user_id == "local")
            )).scalars().all():
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


async def _wipe_settings():
    from sqlalchemy import select

    from backend.models.user_settings import UserSettings

    async with AsyncSessionLocal() as db:
        for row in (await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )).scalars().all():
            await db.delete(row)
        await db.commit()


def _patch_admission(monkeypatch, tmp_path, *, dispatched=None, dispatch_impl=None):
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
    # dispatch is a no-op: the stubbed coordinator returns the result of
    # ``list.append`` (None), and the real dispatcher would then schedule
    # ``None`` as a task - a harness artefact, not product behaviour.
    monkeypatch.setattr(
        "backend.core.task_runner.dispatch",
        dispatch_impl if dispatch_impl is not None else _drop,
    )


def _drop(work):
    """Close the coordinator coroutine without scheduling it.

    The stubbed coordinator returns ``None`` (the result of ``list.append``),
    which the real dispatcher would then schedule as a task. Closing is the
    cheapest way to keep the unawaited-coroutine warning away without running
    anything the test did not ask for.
    """
    if work is not None and hasattr(work, "close"):
        work.close()


async def _admit(monkeypatch, tmp_path):
    dispatched: list = []
    _patch_admission(monkeypatch, tmp_path, dispatched=dispatched)
    result = await api.localization_batch(
        files=[_upload("a.mp4")],
        target_language="vi", mode="full_dub", voice_id="",
        caption_style="viral", emoji_style="moderate",
        target_aspect=None, profile_id=None,
    )
    return result, dispatched


# ══════════════════════════════════════════════════════════════════════════
# R2 — the snapshot failure must be classified and recorded, not swallowed
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_absent_settings_row_records_the_benign_reason(monkeypatch, tmp_path):
    """No Settings row is BENIGN and must say so explicitly.

    The runner resolves ``profile_id`` at execution time exactly as it would
    have without a snapshot, so admission succeeding is correct - but the row
    must still carry WHY there is no snapshot. An unlabelled ``None`` is the
    state the reviewer could not audit.
    """
    await _wipe_settings()

    result, dispatched = await _admit(monkeypatch, tmp_path)

    assert result["jobs"], "admission itself must still succeed"
    rows = await _rows()
    assert len(rows) == 1
    payload = _payload(next(iter(rows.values())))

    assert payload.get("profile_override") is None
    assert payload.get("profile_snapshot_status") == "no_settings", (
        "an absent Settings row is a distinct, benign outcome and must be "
        f"labelled as such; got {payload.get('profile_snapshot_status')!r}"
    )
    assert not payload.get("profile_snapshot_error"), (
        "the benign path is not an error and must not be dressed as one"
    )


@pytest.mark.asyncio
async def test_misconfigured_profile_is_admitted_but_labelled_unreproducible(
    monkeypatch, tmp_path,
):
    """A deterministic resolution failure must be VISIBLE, not swallowed.

    The batch still admits - the snapshot is an optimisation for recovery and
    its absence is not the user's fault - but the failure is deterministic, so
    claiming nothing went wrong sends recovery into the identical failure with
    no explanation. The durable marker must name the reason and the outcome.
    """
    from backend.services import localization_service as svc

    def _boom(*args, **kwargs):
        raise ValueError(
            "Invalid translation AI route configuration: "
            "route 1 (conn-deleted): connection not found"
        )

    monkeypatch.setattr(svc, "_profile_ai_route_clients", _boom)
    monkeypatch.setattr(svc, "_profile_ai_route_clients_compat", _boom)

    # A Settings row must exist, otherwise this proves the no_settings branch.
    from backend.models.user_settings import UserSettings
    from backend.services.localization_service import LocalizationProfile

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    profile = LocalizationProfile(id="broken-profile", name="Broken")
    async with AsyncSessionLocal() as db:
        db.add(UserSettings(
            user_id="local",
            localization_profiles_json=json.dumps({
                "default_profile_id": profile.id,
                "profiles": [json.loads(profile.model_dump_json())],
            }),
        ))
        await db.commit()

    result, dispatched = await _admit(monkeypatch, tmp_path)

    assert result["jobs"], (
        "a missing snapshot must not reject the batch - profile_id stays "
        "durable and the runner resolves it as it always did"
    )
    rows = await _rows()
    payload = _payload(next(iter(rows.values())))

    assert payload.get("profile_override") is None
    assert payload.get("profile_snapshot_status") == "unreproducible", (
        "a configuration error is NOT the same as 'no Settings row'; it must "
        f"be distinguishable in the durable row, got "
        f"{payload.get('profile_snapshot_status')!r}"
    )
    error = payload.get("profile_snapshot_error") or ""
    assert "connection not found" in error, (
        "the reason must survive to the durable row, otherwise a recovery "
        f"failure has nothing to explain itself with: {error!r}"
    )


@pytest.mark.asyncio
async def test_successful_snapshot_is_marked_complete(monkeypatch, tmp_path):
    """The positive case must be labelled too.

    Otherwise 'present' and 'absent' are distinguished only by the presence of
    a non-null dict, and a future writer that stores ``{}`` would be
    indistinguishable from a real failure.
    """
    from backend.models.user_settings import UserSettings
    from backend.services import localization_service as svc
    from backend.services.localization_service import LocalizationProfile

    monkeypatch.setattr(
        svc, "_profile_ai_route_clients",
        lambda settings_row, prof, purpose="translation": [object()],
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
    async with AsyncSessionLocal() as db:
        db.add(UserSettings(
            user_id="local",
            localization_profiles_json=json.dumps({
                "default_profile_id": profile.id,
                "profiles": [json.loads(profile.model_dump_json())],
            }),
        ))
        await db.commit()

    result, dispatched = await _admit(monkeypatch, tmp_path)

    rows = await _rows()
    payload = _payload(next(iter(rows.values())))

    assert isinstance(payload.get("profile_override"), dict), (
        "the snapshot itself must still be produced - this test pins the "
        "status label, not a regression of the snapshot"
    )
    assert payload.get("profile_override", {}).get("id") == "profile-under-test"
    assert payload.get("profile_snapshot_status") == "captured"
    assert not payload.get("profile_snapshot_error")


@pytest.mark.asyncio
async def test_snapshot_status_survives_recovery_replay(monkeypatch, tmp_path):
    """The label must reach the RECOVERY spec, not just sit in the row.

    Otherwise the operator-facing failure at recovery time is still
    unexplained, which is the whole point of recording it.
    """
    from backend.database import recover_pending_localization_batch_items

    upload = tmp_path / "tools_in" / "labelled.mp4"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"video-bytes")

    from datetime import datetime, timedelta

    payload = {
        "target_language": "Vietnamese", "mode": "full_dub",
        "voice_id": "", "caption_style": "viral",
        "emoji_style": "extreme", "target_aspect": None,
        "profile_id": None, "batch": True, "batch_id": "b-snapshot",
        "batch_index": 0, "batch_size": 1, "batch_admitted": True,
        "filename": "labelled.mp4", "in_path": str(upload),
        "profile_override": None,
        "profile_snapshot_status": "unreproducible",
        "profile_snapshot_error": "Invalid translation AI route configuration: route 1 (conn-deleted): connection not found",
    }
    stamp = datetime.utcnow() - timedelta(seconds=999)
    async with AsyncSessionLocal() as db:
        db.add(Job(id="job-snapshot", user_id="local", job_type="tool:translate",
                   status="pending", title="job-snapshot",
                   input_json=json.dumps(payload),
                   created_at=stamp, updated_at=stamp))
        await db.commit()

    # Capture the spec the recovery pass ACTUALLY hands to the coordinator.
    # Patching the coordinator attribute (not dispatch) means the argument is
    # the real list object, so the assertion reads production output rather
    # than a coroutine's frame.
    captured: list = []
    monkeypatch.setattr(
        api, "_run_localization_batch_sequential",
        lambda specs: captured.append(specs),
    )
    monkeypatch.setattr(
        "backend.core.task_runner.dispatch", lambda work: None,
    )

    recovered = await recover_pending_localization_batch_items()

    assert recovered == ["job-snapshot"], (
        f"the item is a complete, on-disk batch and must stay resumable; "
        f"got {recovered}"
    )
    assert len(captured) == 1, (
        f"recovery must still dispatch the coordinator exactly once; "
        f"got {len(captured)}"
    )
    specs = captured[0]
    assert specs, "the recovery spec must carry the item to re-run"
    first = specs[0]
    assert first["job_id"] == "job-snapshot"
    assert first["profile_override"] is None, (
        "an unreproducible snapshot stays None - recovery must not invent one"
    )

    row = (await _rows())["job-snapshot"]
    stored = _payload(row)
    assert stored.get("profile_snapshot_status") == "unreproducible", (
        "recovery must not erase the admission diagnosis when it re-owns the row"
    )
    assert "connection not found" in str(stored.get("profile_snapshot_error"))