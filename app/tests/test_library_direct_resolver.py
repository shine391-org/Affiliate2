# SPDX-License-Identifier: AGPL-3.0-only
"""Single-ID resolution must preserve index eligibility without scanning it."""
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import event

from backend.config import Settings
from backend.database import AsyncSessionLocal, init_db, engine
from backend.models.generated_video import GeneratedVideo
from backend.models.job import Job
from backend.services import library_index as idx


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected", [
    ("success", True), ("failed_localization", True), ("cancelled_localization", True),
    ("running", False), ("pending", False), ("paused", False),
    ("failed_tool", False), ("cancelled_tool", False), ("review_marker", False),
    ("missing_file", False), ("outside_storage", False), ("unknown_tool", False),
    ("audio", False), ("document", False), ("archive", False),
    ("duplicate_generated", False), ("missing_generated", True),
    ("missing_generated_file", True), ("suffix_video", True), ("corrupt_json", False),
])
async def test_direct_job_resolution_preserves_index_policy(monkeypatch, tmp_path, case, expected):
    await init_db()
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setattr(Settings, "STORAGE_ROOT", property(lambda self: root))
    path = root / "output.mp4"
    status, kind = "success", "tool:captions"
    if case in {"failed_localization", "cancelled_localization"}:
        status, kind = case.split("_", 1)[0], "tool:translate"
    elif case in {"running", "pending"}:
        status = case
    elif case == "paused":
        status = "awaiting_review"
    elif case in {"failed_tool", "cancelled_tool"}:
        status = case.split("_", 1)[0]
    elif case == "unknown_tool":
        kind = "tool:unknown-future"
    elif case == "suffix_video":
        kind = "tool:voiceover"
    elif case in {"audio", "document", "archive"}:
        path = path.with_suffix({"audio": ".wav", "document": ".json", "archive": ".zip"}[case])
    elif case == "outside_storage":
        path = tmp_path / "outside.mp4"
    if case != "missing_file":
        path.write_bytes(b"real-filesystem-fixture")
    output = {"file": str(path)}
    if case == "review_marker":
        output["translation_review_pending"] = True
    job_id, generated_id = str(uuid4()), str(uuid4())
    generated = None
    if case in {"duplicate_generated", "missing_generated", "missing_generated_file"}:
        output["generated_video_id"] = generated_id
        if case != "missing_generated":
            generated = GeneratedVideo(id=generated_id, video_path=str(path if case == "duplicate_generated" else root / "missing.mp4"))
    queries = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    async def forbidden(*args, **kwargs):
        pytest.fail("single-ID resolver must not gather the Library")

    async with AsyncSessionLocal() as db:
        job = Job(id=job_id, status=status, job_type=kind,
                  output_json="{" if case == "corrupt_json" else json.dumps(output))
        db.add(job)
        if generated is not None:
            db.add(generated)
        await db.commit()
        # Compare the canonical index's policy before forbidding its scan.
        listed = any(item["source"] == "job" and item["id"] == job_id
                     and item["media"] == "video" for item in await idx.gather(db))
        assert (listed and case != "outside_storage") == expected
        monkeypatch.setattr(idx, "gather", forbidden)
        db.expunge_all()
        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            result = await idx.resolve_job_video(db, job_id)
            assert result == (path.resolve() if expected else None)
            assert len(queries) <= 2
            assert all("WHERE" in sql.upper() for sql in queries)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", record)
            row = await db.get(Job, job_id)
            await db.delete(row)
            if generated is not None:
                await db.delete(await db.get(GeneratedVideo, generated_id))
            await db.commit()


@pytest.mark.asyncio
async def test_direct_resolver_tracks_delete_rerun_and_missing_id(monkeypatch, tmp_path):
    await init_db()
    monkeypatch.setattr(Settings, "STORAGE_ROOT", property(lambda self: tmp_path))
    first, second = tmp_path / "old.mp4", tmp_path / "new.mp4"
    first.write_bytes(b"old")
    second.write_bytes(b"new")
    job_id = str(uuid4())
    async with AsyncSessionLocal() as db:
        row = Job(id=job_id, job_type="tool:translate", status="failed",
                  output_json=json.dumps({"file": str(first)}))
        db.add(row)
        await db.commit()
        try:
            assert await idx.resolve_job_video(db, job_id) == first
            row.status = "running"
            await db.commit()
            assert await idx.resolve_job_video(db, job_id) is None
            row.status = "success"
            row.output_json = json.dumps({"file": str(second)})
            await db.commit()
            assert await idx.resolve_job_video(db, job_id) == second
            second.unlink()
            assert await idx.resolve_job_video(db, job_id) is None
            await db.delete(row)
            await db.commit()
            assert await idx.resolve_job_video(db, job_id) is None
        finally:
            existing = await db.get(Job, job_id)
            if existing is not None:
                await db.delete(existing)
                await db.commit()
