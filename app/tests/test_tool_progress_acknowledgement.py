# SPDX-License-Identifier: AGPL-3.0-only
"""Runner acknowledgement gate plus real SQLite/WS lifecycle regression."""
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from backend.agents import job_helper
from backend.core.tool_runners import _tool_progress
from backend.core.ws_manager import ws_manager
from backend.database import AsyncSessionLocal, Base, engine
from backend.models.job import Job


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [False, True])
async def test_tool_progress_respects_acknowledgement(monkeypatch, accepted):
    update = AsyncMock(return_value=accepted)
    send = AsyncMock()
    monkeypatch.setattr(job_helper, "update_job_status", update)
    monkeypatch.setattr(ws_manager, "send_progress", send)
    await _tool_progress("job", 42, "Translation", "local")
    update.assert_awaited_once_with("job", "running", progress_pct=42, current_step="Translation")
    assert send.await_count == int(accepted)
    if accepted:
        send.assert_awaited_once_with("job", 42, "Translation", "local")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "cancelled", "awaiting_review", "success", "failed"])
async def test_tool_progress_real_persistence_and_websocket(monkeypatch, status):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    job_id = str(uuid4())
    async with AsyncSessionLocal() as db:
        db.add(Job(id=job_id, job_type="tool:translate", status=status,
                   progress_pct=30, current_step="Prior state"))
        await db.commit()
    events = []

    async def send(message, user_id="local"):
        events.append((message, user_id))

    monkeypatch.setattr(ws_manager, "send", send)
    try:
        await _tool_progress(job_id, 45, "Late callback", "local")
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            assert row.status == status
            if status == "running":
                assert row.progress_pct == 45
                assert row.current_step == "Late callback"
                assert events == [({"type": "job_progress", "job_id": job_id,
                                   "percent": 45, "step": "Late callback"}, "local")]
            else:
                assert row.progress_pct == 30
                assert row.current_step == "Prior state"
                assert events == []
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            await db.delete(row)
            await db.commit()
