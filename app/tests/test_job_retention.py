# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Retention and job deletion — the guards that stop tidying from deleting files.

A successful tool run used to be a log entry. Since the Library index, it IS the
item for the file it wrote: the grid renders it, and the asset endpoint resolves
the file BY THAT ROW's id. So every path that removes a job row is now a path
that can make a user's video disappear while the bytes sit on disk forever.

Three of them exist — the retention sweep, `DELETE /api/jobs/{id}`, and
`POST /api/jobs/bulk-delete` — and all three are pinned here.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

PREFIX = "ret-probe-"


async def _session():
    from backend.database import AsyncSessionLocal, init_db
    await init_db()
    return AsyncSessionLocal()


def _real_file(name: str) -> Path:
    from backend.config import settings
    out = settings.STORAGE_ROOT / "tools" / "out"
    out.mkdir(parents=True, exist_ok=True)
    p = out / name
    p.write_bytes(b"x" * 32)
    return p


async def _cleanup(db):
    from backend.models.job import Job
    rows = (await db.execute(select(Job).where(Job.id.startswith(PREFIX)))).scalars().all()
    for r in rows:
        await db.delete(r)
    await db.commit()


async def test_a_row_backing_a_file_is_never_swept():
    """The sweep is defined by what a row IS, not how old it is. An ancient
    `tool:captions` row is the user's captioned video, not activity."""
    from backend.models.job import Job
    from backend.services import job_retention

    db = await _session()
    f = _real_file(f"{PREFIX}kept.mp4")
    old = datetime.utcnow() - timedelta(days=400)
    try:
        db.add(Job(id=f"{PREFIX}keep", job_type="tool:captions", status="success",
                   output_json=json.dumps({"file": str(f)}), created_at=old))
        db.add(Job(id=f"{PREFIX}log", job_type="scout", status="success",
                   output_json=json.dumps({"results": 12}), created_at=old))
        await db.commit()

        stats = await job_retention.sweep(db)
        assert stats["kept_library_items"] >= 1
        assert await db.get(Job, f"{PREFIX}keep") is not None, "the sweep deleted a library item"
        assert await db.get(Job, f"{PREFIX}log") is None, "the sweep kept a pure activity row"
    finally:
        await _cleanup(db)
        f.unlink(missing_ok=True)
        await db.close()


async def test_a_row_whose_file_is_gone_is_prunable_again():
    """Once the file is gone the row is a log entry like any other — otherwise
    a deleted asset would pin its row forever."""
    from backend.models.job import Job
    from backend.services import job_retention

    db = await _session()
    old = datetime.utcnow() - timedelta(days=400)
    try:
        db.add(Job(id=f"{PREFIX}dangling", job_type="tool:captions", status="success",
                   output_json=json.dumps({"file": "/nope/gone.mp4"}), created_at=old))
        await db.commit()
        await job_retention.sweep(db)
        assert await db.get(Job, f"{PREFIX}dangling") is None
    finally:
        await _cleanup(db)
        await db.close()


async def test_deleting_a_library_backed_job_is_refused_with_a_pointer():
    """`DELETE /api/jobs/{id}` is the Activity panel's remove button. It must
    not be a second, unguarded door to deleting a Library file — and refusing
    silently would leave the user with no way to remove it, so the message
    names the door that removes both."""
    from backend.api.jobs import delete_job
    from backend.models.job import Job

    db = await _session()
    f = _real_file(f"{PREFIX}guarded.mp4")
    try:
        db.add(Job(id=f"{PREFIX}g1", job_type="tool:captions", status="success",
                   output_json=json.dumps({"file": str(f)})))
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await delete_job(f"{PREFIX}g1")
        assert exc.value.status_code == 409
        assert "Library" in exc.value.detail
        assert await db.get(Job, f"{PREFIX}g1") is not None
        assert f.is_file()
    finally:
        await _cleanup(db)
        f.unlink(missing_ok=True)
        await db.close()


async def test_clearing_the_log_keeps_library_rows_and_says_how_many():
    """"Clear finished" is a request to tidy the log. Reporting a count that
    included rows it deliberately kept would make the toast lie."""
    from backend.api.jobs import BulkDeleteRequest, bulk_delete_jobs
    from backend.models.job import Job

    db = await _session()
    f = _real_file(f"{PREFIX}bulk.mp4")
    try:
        db.add(Job(id=f"{PREFIX}b1", job_type="tool:captions", status="success",
                   output_json=json.dumps({"file": str(f)})))
        db.add(Job(id=f"{PREFIX}b2", job_type="scout", status="failed"))
        await db.commit()

        res = await bulk_delete_jobs(BulkDeleteRequest(job_ids=[f"{PREFIX}b1", f"{PREFIX}b2"]))
        assert res["deleted"] == 1
        assert res["kept_library"] == 1
        assert await db.get(Job, f"{PREFIX}b1") is not None
        assert f.is_file()
    finally:
        await _cleanup(db)
        f.unlink(missing_ok=True)
        await db.close()


async def test_scout_rows_something_points_at_are_kept():
    """A download reads back through its scout row for the source URL, so age
    is not enough to decide a lead is disposable."""
    from backend.models.downloaded_video import DownloadedVideo
    from backend.models.scout_result import ScoutResult
    from backend.services import job_retention

    db = await _session()
    old = datetime.utcnow() - timedelta(days=400)
    try:
        db.add(ScoutResult(id=f"{PREFIX}s1", title="Referenced lead", platform="youtube",
                           video_id="v1", video_url="https://example.test/v1", created_at=old))
        db.add(ScoutResult(id=f"{PREFIX}s2", title="Orphan lead", platform="youtube",
                           video_id="v2", video_url="https://example.test/v2", created_at=old))
        db.add(DownloadedVideo(id=f"{PREFIX}d1", title="From that lead",
                               scout_result_id=f"{PREFIX}s1"))
        await db.commit()

        stats = await job_retention.sweep_scout(db)
        assert stats["kept_referenced"] >= 1
        assert await db.get(ScoutResult, f"{PREFIX}s1") is not None
        assert await db.get(ScoutResult, f"{PREFIX}s2") is None
    finally:
        from backend.models.downloaded_video import DownloadedVideo as DV
        from backend.models.scout_result import ScoutResult as SR
        for Model in (DV, SR):
            rows = (await db.execute(select(Model).where(Model.id.startswith(PREFIX)))).scalars().all()
            for r in rows:
                await db.delete(r)
        await db.commit()
        await db.close()

async def test_failed_localization_last_good_file_is_not_pruned():
    from backend.models.job import Job
    from backend.services import job_retention

    db = await _session()
    f = _real_file(f"{PREFIX}localize-last-good.mp4")
    old = datetime.utcnow() - timedelta(days=400)
    try:
        db.add(Job(
            id=f"{PREFIX}localize-last-good",
            job_type="tool:translate",
            status="failed",
            output_json=json.dumps({"file": str(f), "qc_status": "READY"}),
            created_at=old,
        ))
        await db.commit()

        await job_retention.sweep(db)
        assert await db.get(Job, f"{PREFIX}localize-last-good") is not None
        assert f.is_file()
    finally:
        await _cleanup(db)
        f.unlink(missing_ok=True)
        await db.close()

async def test_old_localization_library_item_compacts_heavy_workspace_but_keeps_source():
    from backend.config import settings
    from backend.models.job import Job
    from backend.services import job_retention

    db = await _session()
    job_id = f"{PREFIX}compact-localize"
    f = _real_file(f"{PREFIX}compact-final.mp4")
    root = settings.STORAGE_ROOT / "localization_jobs" / job_id
    source = root / "source.mp4"
    old = datetime.utcnow() - timedelta(days=400)
    try:
        root.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"source")
        for stage in ("separation", "asr", "semantic"):
            d = root / stage
            d.mkdir(parents=True, exist_ok=True)
            (d / "blob.bin").write_bytes(b"x" * 64)

        dubbing = root / "dubbing"
        dubbing.mkdir(parents=True, exist_ok=True)
        dub_track = dubbing / "dub_track.wav"
        transcript = dubbing / "transcript.json"
        plan = dubbing / "plan.json"
        chunks = dubbing / "chunks.json"
        for artifact in (dub_track, transcript, plan, chunks):
            artifact.write_bytes(b"keep")
        (dubbing / "chunk_0001.wav").write_bytes(b"raw-tts")
        fitted = dubbing / "fitted"
        fitted.mkdir()
        (fitted / "chunk_0001.wav").write_bytes(b"fitted")

        audio_mix = root / "audio_mix"
        audio_mix.mkdir(parents=True, exist_ok=True)
        mixed_audio = audio_mix / "mixed_audio.wav"
        mixed_audio.write_bytes(b"keep")
        (audio_mix / "scratch.wav").write_bytes(b"scratch")

        (root / "manifest.json").write_text(json.dumps({
            "stages": {
                "dubbing": {"artifacts": {
                    "transcript": str(transcript),
                    "dub_track": str(dub_track),
                    "plan": str(plan),
                    "chunks": str(chunks),
                }},
                "audio_mix": {"artifacts": {
                    "mixed_audio": str(mixed_audio),
                }},
            },
        }), encoding="utf-8")

        db.add(Job(
            id=job_id,
            job_type="tool:translate",
            status="success",
            output_json=json.dumps({"file": str(f), "qc_status": "READY"}),
            created_at=old,
        ))
        await db.commit()

        stats = await job_retention.sweep(db)
        assert stats["compacted_localization_workspaces"] >= 1
        assert await db.get(Job, job_id) is not None
        assert source.is_file()
        for stage in ("separation", "asr", "semantic"):
            assert not (root / stage).exists()
        for artifact in (dub_track, transcript, plan, chunks, mixed_audio):
            assert artifact.is_file()
        assert not (dubbing / "chunk_0001.wav").exists()
        assert not fitted.exists()
        assert not (audio_mix / "scratch.wav").exists()
        assert f.is_file()
    finally:
        await _cleanup(db)
        f.unlink(missing_ok=True)
        import shutil
        shutil.rmtree(root, ignore_errors=True)
        await db.close()


async def test_conditional_retention_delete_cannot_remove_a_reclaimed_job():
    from sqlalchemy import delete
    from backend.models.job import Job

    db = await _session()
    job_id = f"{PREFIX}retention-race"
    try:
        db.add(Job(id=job_id, job_type="scout", status="failed"))
        await db.commit()

        snapshot = await db.get(Job, job_id)
        assert snapshot.status == "failed"

        from backend.database import AsyncSessionLocal
        async with AsyncSessionLocal() as other:
            current = await other.get(Job, job_id)
            current.status = "pending"
            await other.commit()

        result = await db.execute(
            delete(Job).where(Job.id == job_id, Job.status == snapshot.status)
        )
        await db.commit()
        assert result.rowcount == 0
        current = await db.get(Job, job_id)
        assert current is not None
        assert current.status == "pending"
    finally:
        await _cleanup(db)
        await db.close()

