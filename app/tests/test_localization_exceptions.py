# SPDX-License-Identifier: AGPL-3.0-only
"""Regression coverage for the canonical localization review queue."""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from backend.api import localization
from backend.database import AsyncSessionLocal, Base, engine
from backend.models.job import Job
from backend.services.localization_stage_service import MANIFEST_VERSION


@pytest.fixture
def localization_queue(tmp_path, monkeypatch):
    storage_root = tmp_path / "storage"
    monkeypatch.setattr(
        localization.settings.__class__,
        "STORAGE_ROOT",
        property(lambda self: storage_root),
    )

    async def initialize() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(initialize())

    def write_manifest(
        job_id: str | None,
        *,
        qc_status: str | None = None,
        failed_stage: bool = False,
        qc_human_approved: bool = False,
        raw: str | None = None,
    ) -> None:
        manifest_dir = storage_root / "localization_jobs" / (job_id or f"missing-{uuid4()}")
        manifest_dir.mkdir(parents=True, exist_ok=True)
        path = manifest_dir / "manifest.json"
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
            return
        stages = {}
        if qc_status:
            stages["qc"] = {"status": "completed", "metadata": {"status": qc_status}}
        if failed_stage:
            stages["dubbing"] = {"status": "failed", "error": "timing overflow"}
        path.write_text(
            json.dumps({
                "version": MANIFEST_VERSION,
                "job_id": job_id,
                "updated_at": "2026-09-28T08:00:00Z",
                "stages": stages,
                "request": {
                    "source_title": job_id or "missing",
                    "qc_human_approved": qc_human_approved,
                },
            }),
            encoding="utf-8",
        )

    def add_job(job_id: str, status: str = "failed") -> None:
        async def insert() -> None:
            async with AsyncSessionLocal() as db:
                db.add(Job(id=job_id, job_type="translate", status=status, title=job_id))
                await db.commit()

        asyncio.run(insert())

    def load_items() -> list[dict]:
        return asyncio.run(localization.localization_exceptions())["items"]

    return write_manifest, add_job, load_items


def test_failed_manifest_with_canonical_job_is_returned(localization_queue):
    write_manifest, add_job, load_items = localization_queue
    add_job("live-failed")
    write_manifest("live-failed", failed_stage=True)

    assert [item["job_id"] for item in load_items()] == ["live-failed"]


def test_needs_review_manifest_with_canonical_job_is_returned(localization_queue):
    write_manifest, add_job, load_items = localization_queue
    add_job("live-review", status="success")
    write_manifest("live-review", qc_status="NEEDS_REVIEW")

    assert [item["job_id"] for item in load_items()] == ["live-review"]


def test_orphan_manifest_is_not_returned(localization_queue):
    write_manifest, _add_job, load_items = localization_queue
    write_manifest("orphan-production", failed_stage=True)

    assert load_items() == []


def test_mixed_live_and_orphan_manifests_return_only_live_jobs(localization_queue):
    write_manifest, add_job, load_items = localization_queue
    add_job("live-mixed")
    write_manifest("live-mixed", qc_status="FAILED")
    write_manifest("orphan-mixed", qc_status="FAILED")

    assert [item["job_id"] for item in load_items()] == ["live-mixed"]


def test_malformed_manifest_is_skipped_safely(localization_queue):
    write_manifest, _add_job, load_items = localization_queue
    write_manifest("malformed", raw="{not-json")

    assert load_items() == []


def test_manifest_without_job_id_is_skipped_safely(localization_queue):
    write_manifest, _add_job, load_items = localization_queue
    write_manifest(None, qc_status="FAILED")

    assert load_items() == []

def test_human_approved_warning_only_qc_leaves_review_queue(localization_queue):
    write_manifest, add_job, load_items = localization_queue
    add_job("approved-review", status="success")
    write_manifest(
        "approved-review",
        qc_status="NEEDS_REVIEW",
        qc_human_approved=True,
    )

    assert [item["job_id"] for item in load_items()] == []


def test_failed_qc_cannot_be_hidden_by_human_approval_flag(localization_queue):
    write_manifest, add_job, load_items = localization_queue
    add_job("failed-even-approved", status="success")
    write_manifest(
        "failed-even-approved",
        qc_status="FAILED",
        qc_human_approved=True,
    )

    assert [item["job_id"] for item in load_items()] == ["failed-even-approved"]

