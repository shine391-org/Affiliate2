# SPDX-License-Identifier: AGPL-3.0-only
"""D2/D3 — smart retry must not carry stale targeting, and a failed dispatch
must roll the FILESYSTEM back, not just the Job row.

``rerun_localization_job`` mutates the manifest (targeting bookkeeping in
``request``, then ``invalidate_from`` which deletes artifact trees) BEFORE it
hands control to the background runner. Two consequences are pinned here:

* D2 — when the smart-retry path decides NOT to retarget (no overflow chunks,
  or a non-dubbing stage), a ``regenerate_chunk_ids`` list left by an EARLIER
  attempt survives. The runner is then told to reuse chunk WAVs this very
  rerun just deleted.
* D3 — when dispatch fails, only the Job row is restored. The manifest keeps
  the deleted artifacts and the rewritten request, so the job is left describing
  a rerun that never started.

These drive the real endpoint against lightweight run doubles; no media or LIVE
provider is involved.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.api import localization as api
from backend.services.localization_stage_service import LocalizationRun, ManifestTransaction


async def _fake_claim(job_id: str, **_kwargs):
    await api._assert_job_inactive(job_id)
    return {
        "status": "failed", "progress_pct": 0.0, "current_step": None,
        "error_message": None, "started_at": None, "completed_at": None,
        "claimed_at": None,
    }


async def _fake_restore(_job_id: str, _token: dict):
    return None


class _RunDouble:
    """Records the endpoint's manifest mutations in call order."""

    def __init__(self, *, dubbing_metadata=None, request=None):
        self.job_id = "job-smart-retry"
        self.request = dict(request or {"mode": "full_dub"})
        self._dubbing_metadata = dubbing_metadata
        self.calls: list[tuple] = []

    def first_incomplete_stage(self):
        return "dubbing"

    def record(self, stage):
        assert stage == "dubbing"
        return {"metadata": dict(self._dubbing_metadata or {})}

    def update_request(self, value):
        self.request = dict(value)
        self.calls.append(("request", dict(value)))

    def invalidate_from(self, stage, *, delete_artifacts, preserve_files):
        self.calls.append(
            ("invalidate", stage, delete_artifacts, set(preserve_files))
        )

    def refresh_source_identity(self):
        self.calls.append(("refresh_source_identity",))


async def _patch_endpoint(monkeypatch, run, *, dispatch=None):
    monkeypatch.setattr(api, "_assert_job_inactive", lambda job_id: _noop())
    monkeypatch.setattr(api, "_claim_job_mutation", _fake_claim)
    monkeypatch.setattr(api, "_restore_job_mutation", _fake_restore)
    monkeypatch.setattr(api, "_load_run", lambda job_id: run)
    if dispatch is not None:
        monkeypatch.setattr(api, "_dispatch_existing_run", dispatch)


async def _noop():
    return None


# ══════════════════════════════════════════════════════════════════════════
# D2 — stale regenerate_chunk_ids
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_smart_retry_without_overflow_clears_previous_targeting(monkeypatch):
    """A dubbing failure with no timing_overflow has nothing to regenerate.

    A ``regenerate_chunk_ids`` list from an earlier attempt must not survive:
    this rerun deletes the dubbing artifacts, so those ids point at bytes that
    no longer exist.
    """
    run = _RunDouble(
        dubbing_metadata={"issues": [
            {"chunk_id": 4, "reason": "tts_unavailable"},
            {"chunk_id": 5, "reason": "speed_warning"},
        ]},
        request={"mode": "full_dub", "regenerate_chunk_ids": [2, 3]},
    )

    async def fake_dispatch(r, **_kwargs):
        run.calls.append(("dispatch", r.job_id))

    await _patch_endpoint(monkeypatch, run, dispatch=fake_dispatch)

    result = await api.rerun_localization_job(
        run.job_id, api.RerunRequest(),
    )

    assert result["from_stage"] == "dubbing"
    assert result["smart_retry"] is True
    assert not run.request.get("regenerate_chunk_ids"), (
        "a full dubbing rebuild must not keep targeting chunks it just deleted"
    )
    assert ("invalidate", "dubbing", True, set()) in run.calls, (
        "with no overflow chunks the rebuild must delete artifacts"
    )


@pytest.mark.asyncio
async def test_smart_retry_on_non_dubbing_stage_clears_targeting(monkeypatch):
    """Smart retry from translation must not carry dubbing targeting forward."""

    class TranslationRun(_RunDouble):
        def first_incomplete_stage(self):
            return "translation"

        def record(self, stage):
            raise AssertionError("translation smart retry must not read dubbing issues")

    run = TranslationRun(
        request={"mode": "full_dub", "regenerate_chunk_ids": [7, 8],
                 "review_translation": True},
    )

    async def fake_dispatch(r, **_kwargs):
        run.calls.append(("dispatch", r.job_id))

    await _patch_endpoint(monkeypatch, run, dispatch=fake_dispatch)

    result = await api.rerun_localization_job(run.job_id, api.RerunRequest())

    assert result["from_stage"] == "translation"
    assert not run.request.get("regenerate_chunk_ids")
    assert ("invalidate", "translation", True, {"checkpoint.json"}) in run.calls


@pytest.mark.asyncio
async def test_smart_retry_with_overflow_still_targets_only_overflow_chunks(monkeypatch):
    """The targeted case must keep working: reuse every non-target raw chunk."""
    run = _RunDouble(
        dubbing_metadata={"issues": [
            {"chunk_id": 7, "reason": "timing_overflow"},
            {"chunk_id": 9, "reason": "speed_warning"},
        ]},
        request={"mode": "full_dub", "regenerate_chunk_ids": [1]},
    )

    async def fake_dispatch(r, **_kwargs):
        run.calls.append(("dispatch", r.job_id))

    await _patch_endpoint(monkeypatch, run, dispatch=fake_dispatch)

    result = await api.rerun_localization_job(run.job_id, api.RerunRequest())

    assert result["targeted_chunk_ids"] == [7]
    assert run.request["regenerate_chunk_ids"] == [7]
    assert ("invalidate", "dubbing", False, set()) in run.calls


# ══════════════════════════════════════════════════════════════════════════
# D3 — filesystem rollback when dispatch fails
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def real_run(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from backend.services import localization_stage_service as stages

    monkeypatch.setattr(
        stages, "settings", SimpleNamespace(STORAGE_ROOT=tmp_path),
    )
    source = tmp_path / "origin.mp4"
    source.write_bytes(b"rollback-source")

    def build(job_id="job-rollback"):
        run = LocalizationRun(
            job_id, source_path=str(source),
            effective_profile={"id": "hq"},
            request={"mode": "full_dub", "regenerate_chunk_ids": [3]},
        )
        run.ensure_source_copy(source)
        for stage in ("separation", "asr", "semantic", "translation"):
            if stage == "separation":
                run.mark_complete_without_artifacts(stage)
                continue
            path = run.save_json(stage, "transcript.json", {"segments": []})
            run.complete(stage, artifacts={"transcript": path})
        dub = run.stage_dir("dubbing")
        (dub / "chunk_0001.wav").write_bytes(b"raw-one")
        (dub / "chunk_0003.wav").write_bytes(b"raw-three")
        run.complete(
            "dubbing",
            artifacts={
                "transcript": run.save_json(
                    "dubbing", "transcript.json", {"segments": []}),
                "dub_track": _write(dub / "dub_track.wav", b"track"),
                "plan": run.save_json("dubbing", "plan.json", {"plan": []}),
                "chunks": run.save_json("dubbing", "chunks.json", {"chunks": []}),
            },
        )
        return run

    return build


def _write(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


def _manifest_state(run: LocalizationRun) -> dict:
    """Everything a failed rerun must leave exactly as it found it."""
    data = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    return {
        "stages": data["stages"],
        "request": data["request"],
        "dubbing_files": sorted(
            (p.name, p.read_bytes())
            for p in (run.root / "dubbing").iterdir() if p.is_file()
        ) if (run.root / "dubbing").is_dir() else None,
    }


@pytest.mark.asyncio
async def test_failed_dispatch_restores_deleted_artifacts(real_run, monkeypatch):
    """Dispatch failure must roll the filesystem back, not only the Job row.

    Without filesystem rollback the job is left claiming a full dubbing rebuild
    while the raw chunks this rerun deleted are simply gone — a targeted retry
    against a manifest that no longer matches the disk.
    """
    run = real_run()
    before = _manifest_state(run)

    async def fake_dispatch(_run, **_kwargs):
        raise api.HTTPException(status_code=409, detail="claim lost before dispatch")

    await _patch_endpoint(monkeypatch, run, dispatch=fake_dispatch)

    with pytest.raises(api.HTTPException):
        await api.rerun_localization_job(
            run.job_id, api.RerunRequest(from_stage="dubbing"),
        )

    after = _manifest_state(LocalizationRun.load(run.job_id))
    assert after["request"] == before["request"], (
        "a dispatch that never started must not leave rewritten targeting"
    )
    assert after["dubbing_files"] == before["dubbing_files"], (
        "deleted artifact bytes must be restored"
    )
    for stage, record in before["stages"].items():
        if stage in {"dubbing", "audio_mix", "final_render", "qc"}:
            continue
        assert after["stages"][stage] == record, f"{stage} must be untouched"


@pytest.mark.asyncio
async def test_successful_dispatch_keeps_the_mutation(real_run, monkeypatch):
    """The transaction must COMMIT on success, not roll back a real rerun."""
    run = real_run()

    async def fake_dispatch(_run, **_kwargs):
        return None

    await _patch_endpoint(monkeypatch, run, dispatch=fake_dispatch)

    result = await api.rerun_localization_job(
        run.job_id, api.RerunRequest(from_stage="dubbing"),
    )

    assert result["ok"] is True
    fresh = LocalizationRun.load(run.job_id)
    assert fresh.record("dubbing")["status"] == "invalidated"
    assert not fresh.record("translation")["status"] == "invalidated", (
        "upstream stages must be preserved on a successful rerun"
    )
    assert not (run.root / "dubbing" / "chunk_0001.wav").exists()


@pytest.mark.asyncio
async def test_manifest_transaction_rolls_back_its_own_writes(real_run):
    """The rollback primitive itself restores manifest bytes and artifacts."""
    run = real_run()
    before = _manifest_state(run)

    tx = ManifestTransaction(run, ["dubbing", "audio_mix"])
    tx.begin()
    run.update_request({**run.request, "regenerate_chunk_ids": [99]})
    run.invalidate_from("dubbing", delete_artifacts=True)
    tx.rollback()

    assert _manifest_state(LocalizationRun.load(run.job_id)) == before
    assert not (run.root / ".rollback").exists()


# ══════════════════════════════════════════════════════════════════════════
# P1 — CancelledError during dispatch must roll back and re-raise
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cancelled_dispatch_restores_manifest_and_releases_claim(
    real_run, monkeypatch,
):
    """A CancelledError during dispatch must roll back the manifest, restore
    artifact bytes, release the mutation claim, and re-raise the cancellation.

    ``asyncio.CancelledError`` inherits from ``BaseException`` (not ``Exception``)
    on Python 3.8+, so an ``except Exception:`` handler silently skips rollback
    and claim release. The endpoint must catch it explicitly, restore state, and
    re-raise so the caller's cancellation is never swallowed.
    """
    run = real_run()
    before = _manifest_state(run)
    original_manifest_bytes = run.manifest_path.read_bytes()
    original_request = dict(run.request)

    async def cancel_dispatch(_run, **_kwargs):
        raise asyncio.CancelledError()

    await _patch_endpoint(monkeypatch, run, dispatch=cancel_dispatch)

    with pytest.raises(asyncio.CancelledError):
        await api.rerun_localization_job(
            run.job_id, api.RerunRequest(from_stage="dubbing"),
        )

    # Manifest bytes must be exactly the original
    after_bytes = run.manifest_path.read_bytes()
    assert after_bytes == original_manifest_bytes, (
        "cancelled dispatch must restore the exact original manifest bytes"
    )

    # Request must be exactly the original
    after = _manifest_state(LocalizationRun.load(run.job_id))
    assert after["request"] == original_request, (
        "cancelled dispatch must restore the exact original request"
    )

    # Artifact bytes must be exactly the original
    assert after["dubbing_files"] == before["dubbing_files"], (
        "cancelled dispatch must restore deleted artifact bytes"
    )

    # Rollback backup must be cleaned up
    assert not (run.root / ".rollback").exists(), (
        "rollback backup must be cleaned up after cancellation rollback"
    )


@pytest.mark.asyncio
async def test_cancelled_dispatch_restores_exact_artifact_bytes(
    real_run, monkeypatch,
):
    """Verify exact byte-level restoration of artifacts after cancellation."""
    run = real_run()
    before = _manifest_state(run)

    async def cancel_dispatch(_run, **_kwargs):
        raise asyncio.CancelledError()

    await _patch_endpoint(monkeypatch, run, dispatch=cancel_dispatch)

    with pytest.raises(asyncio.CancelledError):
        await api.rerun_localization_job(
            run.job_id, api.RerunRequest(from_stage="dubbing"),
        )

    after = _manifest_state(LocalizationRun.load(run.job_id))
    assert after["dubbing_files"] == before["dubbing_files"], (
        "every artifact file must be restored to its exact original bytes"
    )
    # Verify specific known files
    dub_dir = run.root / "dubbing"
    assert (dub_dir / "chunk_0001.wav").read_bytes() == b"raw-one"
    assert (dub_dir / "chunk_0003.wav").read_bytes() == b"raw-three"


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [asyncio.CancelledError, RuntimeError])
async def test_rerun_rollback_restores_real_sqlite_claim_and_original_exception(
    real_run, monkeypatch, error_type,
):
    """Only dispatch is stubbed: real SQLite CAS, manifest and artifact rollback."""
    from datetime import datetime
    from backend.database import AsyncSessionLocal, Base, engine
    from backend.models.job import Job

    run = real_run(job_id="real-claim-" + error_type.__name__)
    original_manifest = run.manifest_path.read_bytes()
    original_files = _manifest_state(run)
    fields = ("status", "task_id", "progress_pct", "current_step", "error_message",
              "started_at", "completed_at", "input_json", "output_json")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        row = Job(id=run.job_id, user_id="local", job_type="tool:translate",
            status="failed", task_id="original-task", progress_pct=62.5,
            current_step="dubbing", error_message="original failure",
            started_at=datetime(2026, 1, 1), completed_at=datetime(2026, 1, 2),
            input_json='{"original":true}', output_json='{"saved":true}')
        db.add(row)
        await db.commit()
        await db.refresh(row)
        original_row = {field: getattr(row, field) for field in fields}

    error = error_type("dispatch interrupted")

    async def fail_dispatch(current, **kwargs):
        assert kwargs["already_claimed"] is True
        async with AsyncSessionLocal() as db:
            claimed = await db.get(Job, run.job_id)
            assert claimed is not None
            assert str(claimed.status) == "pending"
            assert str(claimed.task_id).startswith("recovery_claim:failed:")
        assert current.request != original_files["request"]
        assert not (run.root / "dubbing" / "chunk_0001.wav").exists()
        raise error

    monkeypatch.setattr(api, "_load_run", lambda _: run)
    monkeypatch.setattr(api, "_dispatch_existing_run", fail_dispatch)
    try:
        with pytest.raises(error_type) as raised:
            await api.rerun_localization_job(run.job_id, api.RerunRequest(from_stage="dubbing"))
        assert raised.value is error, "ordinary exception/cancellation must propagate unchanged"
        assert run.manifest_path.read_bytes() == original_manifest
        assert _manifest_state(LocalizationRun.load(run.job_id)) == original_files
        assert not (run.root / ".rollback").exists()
        async with AsyncSessionLocal() as db:
            restored = await db.get(Job, run.job_id)
            assert restored is not None
            assert {field: getattr(restored, field) for field in fields} == original_row
        # A fresh real claim proves cancellation did not leave the job locked.
        claim = await api._claim_job_mutation(run.job_id)
        await api._restore_job_mutation(run.job_id, claim)
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, run.job_id)
            if row is not None:
                await db.delete(row)
                await db.commit()