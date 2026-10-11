# SPDX-License-Identifier: AGPL-3.0-only
"""Runner cancellation uses real SQLite and manifests; media leaves are mocked."""
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from backend.agents.job_helper import update_job_status
from backend.config import Settings
from backend.core.exceptions import JobCancelledError
from backend.database import AsyncSessionLocal
from backend.models.job import Job
from backend.services import localization_service as svc
from backend.services.localization_stage_service import LocalizationRun
from test_localization_auto_repair_integration import (
    _create_schema, _make_run, _patch_leaves, _run, _seed_job, _transcript,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["service_check", "progress", "publication"])
async def test_cancelled_c6_never_publishes_or_starts_c7(monkeypatch, tmp_path, boundary):
    await _create_schema()
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(Settings, "STORAGE_ROOT", property(lambda self: storage))
    job_id = str(uuid4())
    run = _make_run(job_id, storage, complete_upstream=("separation", "asr", "semantic"),
                    request_extra={"auto_repair": True})
    await _seed_job(job_id)
    synth = AsyncMock(side_effect=AssertionError("Cancellation must not enter C7"))
    _patch_leaves(monkeypatch, tmp_path, synth=synth)
    observed = []

    async def translate(transcript, **kwargs):
        observed.append("C6")
        await update_job_status(job_id, "cancelled", current_step="Cancelled")
        if boundary == "service_check":
            assert callable(kwargs.get("cancel_check")), "Runner must pass a live cancel check"
            assert await kwargs["cancel_check"]()
            observed.append("live_cancel_observed")
            raise JobCancelledError("Cancelled in C6")
        if boundary == "progress":
            await kwargs["progress_cb"]({"event": "attempt", "attempt": 1, "max_attempts": 2})
            observed.append("late_progress_continued")
        return _transcript()

    monkeypatch.setattr(svc, "translate_transcript", translate)
    try:
        await _run(job_id, run)
        assert observed == (["C6", "live_cancel_observed"] if boundary == "service_check" else ["C6"])
        synth.assert_not_awaited()
        fresh = LocalizationRun.load(job_id)
        assert not fresh.is_complete("translation", ("transcript",))
        assert not (fresh.stage_dir("translation") / "transcript.json").exists()
        assert not fresh.request.get("auto_repair_attempts")
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            assert row is not None
            assert row.status == "cancelled"
            assert row.current_step == "Cancelled"
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            await db.delete(row)
            await db.commit()
