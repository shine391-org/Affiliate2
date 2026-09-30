# SPDX-License-Identifier: AGPL-3.0-only
"""Regression coverage for Localization -> GeneratedVideo -> Publish handoff."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.database import AsyncSessionLocal, Base, engine
from backend.models.generated_video import GeneratedVideo
from backend.models.job import Job
from backend.models.phase_g import PublicationRecord
from backend.services import localization_publish_service as publish


@pytest.fixture(scope="module", autouse=True)
def _schema():
    asyncio.run(_init_schema())


async def _init_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        publish.settings.__class__,
        "STORAGE_ROOT",
        property(lambda self: root),
    )
    return root


async def _make_job(storage_root, *, qc_status="READY", approved=False):
    job_id = str(uuid4())
    path = storage_root / f"{job_id}.mp4"
    path.write_bytes(b"localized-version-1")
    async with AsyncSessionLocal() as db:
        db.add(Job(
            id=job_id,
            job_type="tool:translate",
            status="success",
            output_json=json.dumps({
                "file": str(path),
                "target_language": "Vietnamese",
                "target_aspect": "9:16",
                "duration_seconds": 12.4,
                "qc_status": qc_status,
                "qc_human_approved": approved,
            }),
        ))
        await db.commit()
    return job_id, path


async def _cleanup(job_id):
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        generated_ids = []
        if job and job.output_json:
            try:
                out = json.loads(job.output_json)
                for key in ("generated_video_id", "supersedes_generated_video_id"):
                    if out.get(key):
                        generated_ids.append(str(out[key]))
            except (TypeError, ValueError):
                pass
        for generated_id in generated_ids:
            row = await db.get(GeneratedVideo, generated_id)
            if row:
                await db.delete(row)
        # Also remove any localization versions sourced from this job's output
        # that a published-version test intentionally detached from output_json.
        rows = (await db.execute(
            select(GeneratedVideo).where(
                GeneratedVideo.source_type == "localization"
            )
        )).scalars().all()
        for row in rows:
            if row.id not in generated_ids and row.video_path and job_id in row.video_path:
                await db.delete(row)
        if job:
            await db.delete(job)
        await db.commit()


@pytest.mark.asyncio
async def test_qc_ready_localization_becomes_publishable_generated_video(storage_root):
    job_id, path = await _make_job(storage_root)
    try:
        result = await publish.promote_localization_output(job_id)
        assert result["promoted"] is True
        assert result["publish_ready"] is True

        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            out = json.loads(job.output_json)
            video = await db.get(GeneratedVideo, out["generated_video_id"])
            assert video is not None
            assert video.source_type == "localization"
            assert video.gen_tier == "localization"
            assert video.status == "ready"
            assert video.video_path == str(path.resolve())
            assert video.aspect_ratio == "9:16"
            assert video.duration_seconds == 12
    finally:
        await _cleanup(job_id)


@pytest.mark.asyncio
async def test_unreviewed_qc_warning_is_not_publishable_until_human_approval(storage_root):
    job_id, _path = await _make_job(
        storage_root,
        qc_status="NEEDS_REVIEW",
        approved=False,
    )
    try:
        blocked = await publish.promote_localization_output(job_id)
        assert blocked["promoted"] is True
        assert blocked["publish_ready"] is False

        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            out = json.loads(job.output_json)
            draft_id = out["generated_video_id"]
            draft = await db.get(GeneratedVideo, draft_id)
            assert draft.status == "draft"
            assert draft.review_status == "qc_needs_review"
            out["qc_human_approved"] = True
            job.output_json = json.dumps(out)
            await db.commit()

        promoted = await publish.promote_localization_output(job_id)
        assert promoted["promoted"] is True
        assert promoted["generated_video_id"] == draft_id
        assert promoted["publish_ready"] is True
        async with AsyncSessionLocal() as db:
            video = await db.get(GeneratedVideo, promoted["generated_video_id"])
            assert video.status == "ready"
            assert video.review_status == "qc_human_approved"
    finally:
        await _cleanup(job_id)


@pytest.mark.asyncio
async def test_unpublished_localization_version_is_reused_without_losing_last_good(storage_root):
    job_id, old_path = await _make_job(storage_root)
    try:
        first = await publish.promote_localization_output(job_id)
        generated_id = first["generated_video_id"]

        async with AsyncSessionLocal() as db:
            video = await db.get(GeneratedVideo, generated_id)
            video.publish_approved_at = datetime.utcnow()
            video.publish_approval_artifact_sha256 = video.artifact_sha256
            await db.commit()

        new_path = storage_root / f"{job_id}-v2.mp4"
        new_path.write_bytes(b"localized-version-2")
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            job.output_json = json.dumps({
                "file": str(new_path),
                "target_language": "Vietnamese",
                "target_aspect": "16:9",
                "duration_seconds": 13,
                "qc_status": "READY",
                "qc_human_approved": False,
                "supersedes_generated_video_id": generated_id,
            })
            await db.commit()

        second = await publish.promote_localization_output(
            job_id,
            existing_generated_video_id=generated_id,
            previous_output_path=old_path,
        )
        assert second["generated_video_id"] == generated_id
        assert second["reused_generated_video"] is True

        async with AsyncSessionLocal() as db:
            video = await db.get(GeneratedVideo, generated_id)
            assert video.video_path == str(new_path.resolve())
            assert video.aspect_ratio == "16:9"
            assert video.publish_approved_at is None
            assert video.publish_approval_artifact_sha256 is None
        assert not old_path.exists()
    finally:
        await _cleanup(job_id)


@pytest.mark.asyncio
async def test_published_localization_version_is_never_mutated_in_place(storage_root):
    job_id, old_path = await _make_job(storage_root)
    try:
        first = await publish.promote_localization_output(job_id)
        old_id = first["generated_video_id"]
        async with AsyncSessionLocal() as db:
            old = await db.get(GeneratedVideo, old_id)
            old.status = "uploaded"
            old.uploaded_platforms_json = json.dumps(["youtube"])
            old.youtube_video_id = "published-id"
            await db.commit()

        new_path = storage_root / f"{job_id}-new.mp4"
        new_path.write_bytes(b"new-publish-version")
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            job.output_json = json.dumps({
                "file": str(new_path),
                "target_language": "Vietnamese",
                "target_aspect": "9:16",
                "duration_seconds": 14,
                "qc_status": "READY",
                "supersedes_generated_video_id": old_id,
            })
            await db.commit()

        second = await publish.promote_localization_output(
            job_id,
            existing_generated_video_id=old_id,
            previous_output_path=old_path,
        )
        assert second["generated_video_id"] != old_id
        assert second["reused_generated_video"] is False

        async with AsyncSessionLocal() as db:
            old = await db.get(GeneratedVideo, old_id)
            new = await db.get(GeneratedVideo, second["generated_video_id"])
            assert old.video_path == str(old_path.resolve())
            assert old.youtube_video_id == "published-id"
            assert new.video_path == str(new_path.resolve())
            assert new.status == "ready"
        assert old_path.exists()
    finally:
        await _cleanup(job_id)

@pytest.mark.asyncio
async def test_same_published_artifact_promotion_is_idempotent(storage_root):
    job_id, old_path = await _make_job(storage_root)
    try:
        first = await publish.promote_localization_output(job_id)
        old_id = first["generated_video_id"]

        async with AsyncSessionLocal() as db:
            old = await db.get(GeneratedVideo, old_id)
            old.status = "uploaded"
            old.uploaded_platforms_json = json.dumps(["youtube"])
            old.youtube_video_id = "published-same-bytes"
            await db.commit()

        second = await publish.promote_localization_output(job_id)
        assert second["generated_video_id"] == old_id
        assert second["artifact_unchanged"] is True

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(GeneratedVideo).where(
                    GeneratedVideo.source_type == "localization",
                    GeneratedVideo.video_path == str(old_path.resolve()),
                )
            )).scalars().all()
            assert [row.id for row in rows] == [old_id]
            assert rows[0].youtube_video_id == "published-same-bytes"
    finally:
        await _cleanup(job_id)

@pytest.mark.asyncio
async def test_failed_qc_is_library_draft_not_publish_ready(storage_root):
    job_id, _path = await _make_job(
        storage_root,
        qc_status="FAILED",
        approved=False,
    )
    try:
        result = await publish.promote_localization_output(job_id)
        assert result["promoted"] is True
        assert result["publish_ready"] is False

        async with AsyncSessionLocal() as db:
            video = await db.get(GeneratedVideo, result["generated_video_id"])
            assert video.status == "draft"
            assert video.review_status == "qc_failed"
    finally:
        await _cleanup(job_id)


@pytest.mark.asyncio
async def test_qc_regression_does_not_replace_last_ready_unpublished_version(storage_root):
    job_id, old_path = await _make_job(storage_root)
    try:
        first = await publish.promote_localization_output(job_id)
        ready_id = first["generated_video_id"]

        new_path = storage_root / f"{job_id}-qc-failed.mp4"
        new_path.write_bytes(b"quality-regression")
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            job.output_json = json.dumps({
                "file": str(new_path),
                "target_language": "Vietnamese",
                "target_aspect": "9:16",
                "duration_seconds": 13,
                "qc_status": "FAILED",
                "supersedes_generated_video_id": ready_id,
            })
            await db.commit()

        second = await publish.promote_localization_output(
            job_id,
            existing_generated_video_id=ready_id,
            previous_output_path=old_path,
        )
        assert second["publish_ready"] is False
        assert second["generated_video_id"] != ready_id

        async with AsyncSessionLocal() as db:
            ready = await db.get(GeneratedVideo, ready_id)
            draft = await db.get(GeneratedVideo, second["generated_video_id"])
            assert ready.status == "ready"
            assert ready.video_path == str(old_path.resolve())
            assert draft.status == "draft"
            assert draft.video_path == str(new_path.resolve())
        assert old_path.exists()
    finally:
        await _cleanup(job_id)

@pytest.mark.asyncio
async def test_active_upload_makes_previous_generated_video_immutable(storage_root):
    job_id, old_path = await _make_job(storage_root)
    upload_job_id = str(uuid4())
    try:
        first = await publish.promote_localization_output(job_id)
        old_id = first["generated_video_id"]

        async with AsyncSessionLocal() as db:
            db.add(Job(
                id=upload_job_id,
                job_type="upload",
                status="pending",
                input_json=json.dumps({
                    "generated_video_id": old_id,
                    "platforms": ["youtube"],
                }),
            ))
            await db.commit()

        new_path = storage_root / f"{job_id}-while-uploading.mp4"
        new_path.write_bytes(b"localized-version-during-upload")
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            job.output_json = json.dumps({
                "file": str(new_path),
                "target_language": "Vietnamese",
                "target_aspect": "9:16",
                "duration_seconds": 15,
                "qc_status": "READY",
                "supersedes_generated_video_id": old_id,
            })
            await db.commit()

        second = await publish.promote_localization_output(
            job_id,
            existing_generated_video_id=old_id,
            previous_output_path=old_path,
        )
        assert second["generated_video_id"] != old_id
        assert second["reused_generated_video"] is False

        async with AsyncSessionLocal() as db:
            old = await db.get(GeneratedVideo, old_id)
            new = await db.get(GeneratedVideo, second["generated_video_id"])
            assert old.video_path == str(old_path.resolve())
            assert new.video_path == str(new_path.resolve())
        assert old_path.exists()
    finally:
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, upload_job_id)
            if row:
                await db.delete(row)
                await db.commit()
        await _cleanup(job_id)


@pytest.mark.asyncio
async def test_concurrent_promotion_of_same_artifact_is_idempotent(storage_root):
    job_id, path = await _make_job(storage_root)
    try:
        first, second = await asyncio.gather(
            publish.promote_localization_output(job_id),
            publish.promote_localization_output(job_id),
        )
        assert first["generated_video_id"] == second["generated_video_id"]

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(GeneratedVideo).where(
                    GeneratedVideo.source_type == "localization",
                    GeneratedVideo.video_path == str(path.resolve()),
                )
            )).scalars().all()
            assert len(rows) == 1
    finally:
        await _cleanup(job_id)


def test_localization_publish_gate_rejects_revoked_qc():
    from backend.agents.uploader import localization_qc_publish_error

    video = GeneratedVideo(
        source_type="localization",
        review_status="qc_review_revoked",
    )
    assert localization_qc_publish_error(video)

    video.review_status = "qc_ready"
    assert localization_qc_publish_error(video) is None

@pytest.mark.asyncio
async def test_stale_caller_pointer_cannot_duplicate_cross_process_promotion(storage_root):
    job_id, old_path = await _make_job(storage_root)
    try:
        first = await publish.promote_localization_output(job_id)
        old_id = first["generated_video_id"]
        async with AsyncSessionLocal() as db:
            old = await db.get(GeneratedVideo, old_id)
            old.status = "uploaded"
            old.uploaded_platforms_json = json.dumps(["youtube"])
            old.youtube_video_id = "already-public"
            await db.commit()

        new_path = storage_root / f"{job_id}-cross-process.mp4"
        new_path.write_bytes(b"cross-process-version")
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            job.output_json = json.dumps({
                "file": str(new_path),
                "target_language": "Vietnamese",
                "target_aspect": "9:16",
                "duration_seconds": 16,
                "qc_status": "READY",
                "supersedes_generated_video_id": old_id,
            })
            await db.commit()

        winner = await publish.promote_localization_output(
            job_id,
            existing_generated_video_id=old_id,
        )
        # Simulate the second backend process waking after the DB lock with its
        # stale caller-side old_id snapshot.
        loser = await publish.promote_localization_output(
            job_id,
            existing_generated_video_id=old_id,
        )
        assert loser["generated_video_id"] == winner["generated_video_id"]

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(GeneratedVideo).where(
                    GeneratedVideo.video_path == str(new_path.resolve())
                )
            )).scalars().all()
            assert len(rows) == 1
    finally:
        await _cleanup(job_id)


@pytest.mark.asyncio
async def test_failed_publication_attempt_freezes_generated_video_artifact_identity(storage_root):
    job_id, old_path = await _make_job(storage_root)
    old_id = None
    try:
        first = await publish.promote_localization_output(job_id)
        old_id = first["generated_video_id"]
        async with AsyncSessionLocal() as db:
            db.add(PublicationRecord(
                generated_video_id=old_id,
                platform="youtube",
                status="FAILED",
                mapping_status="mapped",
            ))
            await db.commit()

        new_path = storage_root / f"{job_id}-after-failed-publish.mp4"
        new_path.write_bytes(b"new-after-failed-publication")
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            job.output_json = json.dumps({
                "file": str(new_path),
                "target_language": "Vietnamese",
                "target_aspect": "9:16",
                "duration_seconds": 17,
                "qc_status": "READY",
                "supersedes_generated_video_id": old_id,
            })
            await db.commit()

        second = await publish.promote_localization_output(
            job_id,
            existing_generated_video_id=old_id,
        )
        assert second["generated_video_id"] != old_id
        async with AsyncSessionLocal() as db:
            old = await db.get(GeneratedVideo, old_id)
            assert old.video_path == str(old_path.resolve())
    finally:
        if old_id:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(PublicationRecord).where(
                        PublicationRecord.generated_video_id == old_id
                    )
                )).scalars().all()
                for row in rows:
                    await db.delete(row)
                await db.commit()
        await _cleanup(job_id)

