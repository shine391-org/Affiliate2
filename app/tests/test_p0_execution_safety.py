# SPDX-License-Identifier: AGPL-3.0-only
"""P0 regression coverage for execution-safety idempotency."""

import asyncio
from uuid import uuid4

import pytest

from backend.agents.job_helper import (
    create_or_reuse_active_job,
    update_job_status,
)
from backend.database import init_db


@pytest.fixture(scope="module", autouse=True)
def _schema():
    asyncio.run(init_db())


def test_active_job_signature_is_reused_until_terminal():
    async def run():
        key = f"qa15-{uuid4()}"
        first, reused_first = await create_or_reuse_active_job(
            "tool:translate",
            "local",
            {"source": "video-a"},
            dedupe_key=key,
        )
        second, reused_second = await create_or_reuse_active_job(
            "tool:translate",
            "local",
            {"source": "video-a"},
            dedupe_key=key,
        )
        await update_job_status(first.id, "success")
        third, reused_third = await create_or_reuse_active_job(
            "tool:translate",
            "local",
            {"source": "video-a"},
            dedupe_key=key,
        )
        return first, reused_first, second, reused_second, third, reused_third

    first, reused_first, second, reused_second, third, reused_third = asyncio.run(run())

    assert reused_first is False
    assert reused_second is True
    assert second.id == first.id
    assert reused_third is False
    assert third.id != first.id


def test_concurrent_duplicate_claims_create_only_one_active_job():
    async def run():
        key = f"qa15-concurrent-{uuid4()}"

        async def claim():
            return await create_or_reuse_active_job(
                "tool:translate",
                "local",
                {"source": "video-b"},
                dedupe_key=key,
            )

        results = await asyncio.gather(*(claim() for _ in range(5)))
        ids = [job.id for job, _ in results]
        reused_flags = [reused for _, reused in results]
        return ids, reused_flags

    ids, reused_flags = asyncio.run(run())

    assert len(set(ids)) == 1
    assert reused_flags.count(False) == 1
    assert reused_flags.count(True) == 4
