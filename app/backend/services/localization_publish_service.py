# SPDX-License-Identifier: AGPL-3.0-only
"""Bridge a finished localization artifact into the canonical Publish model.

Localization is owned by a resumable Job/manifest while publishing is owned by
GeneratedVideo. This module is the single handoff between those two models.

Important invariant:
- a rerun never mutates/deletes the last published/approved artifact in place;
- QC FAILED is never publishable;
- QC NEEDS_REVIEW becomes publishable only after explicit human acknowledgement.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models.downloaded_video import DownloadedVideo
from backend.models.generated_video import GeneratedVideo
from backend.models.job import Job
from backend.models.phase_g import PublicationRecord


_PROMOTION_LOCK = asyncio.Lock()


def _json_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _already_published(video: GeneratedVideo) -> bool:
    return bool(
        _json_list(video.uploaded_platforms_json)
        or video.youtube_video_id
        or video.tiktok_publish_id
        or video.instagram_media_id
        or video.status == "uploaded"
    )


async def _active_upload_job_exists(db, video_id: str) -> bool:
    rows = (await db.execute(
        select(Job).where(
            Job.job_type == "upload",
            Job.status.in_(["pending", "running"]),
        )
    )).scalars().all()
    for row in rows:
        try:
            submitted = json.loads(row.input_json or "{}")
        except (TypeError, ValueError):
            continue
        if (
            isinstance(submitted, dict)
            and str(submitted.get("generated_video_id") or "") == video_id
        ):
            return True
    return False


async def _publication_is_live_or_uncertain(db, video_id: str) -> bool:
    """True only while provider side effects can still be in flight/uncertain."""
    risky = (await db.execute(
        select(PublicationRecord.id).where(
            PublicationRecord.generated_video_id == video_id,
            PublicationRecord.status.in_(
                ["PUBLISHING", "PERSISTENCE_ERROR", "OUTCOME_UNKNOWN"]
            ),
        )
    )).first()
    return bool(risky or await _active_upload_job_exists(db, video_id))


async def _publication_identity_locked(db, video_id: str) -> bool:
    """Freeze artifact identity after ANY publication attempt or queued upload."""
    history = (await db.execute(
        select(PublicationRecord.id).where(
            PublicationRecord.generated_video_id == video_id,
        )
    )).first()
    return bool(history or await _active_upload_job_exists(db, video_id))


def _safe_storage_file(path_value: str | Path | None) -> Path | None:
    if not path_value:
        return None
    try:
        path = Path(path_value).resolve()
        root = settings.STORAGE_ROOT.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        return path
    except (OSError, ValueError):
        return None


def _publishable_qc(output: dict) -> bool:
    status = str(output.get("qc_status") or "").upper()
    if status == "READY":
        return True
    return status == "NEEDS_REVIEW" and output.get("qc_human_approved") is True


async def _promote_localization_output_unlocked(
    job_id: str,
    *,
    existing_generated_video_id: str | None = None,
    previous_output_path: str | Path | None = None,
    expected_output_path: str | Path | None = None,
) -> dict:
    """Create/update the GeneratedVideo that Publish consumes.

    File hashing is intentionally done before taking SQLite's cross-process
    write lock. Delivery files are immutable; the locked transaction then
    re-reads Job.output_json and rejects a changed file pointer.
    """
    async with AsyncSessionLocal() as pre_db:
        pre_job = await pre_db.get(Job, job_id)
        if pre_job is None:
            raise RuntimeError(f"Localization job not found: {job_id}")
        try:
            pre_output = json.loads(pre_job.output_json or "{}")
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Localization job output is not valid JSON") from exc
        if not isinstance(pre_output, dict):
            raise RuntimeError("Localization job output is not an object")
        video_path = _safe_storage_file(pre_output.get("file"))
        if video_path is None:
            raise RuntimeError("Localized output file is missing or outside storage")
        if expected_output_path is not None:
            try:
                expected = Path(expected_output_path).resolve()
            except (OSError, ValueError):
                expected = None
            if expected is None or video_path.resolve() != expected:
                return {
                    "promoted": False,
                    "generated_video_id": pre_output.get("generated_video_id"),
                    "publish_ready": bool(pre_output.get("publish_ready")),
                    "stale": True,
                }
        artifact_hash = _sha256(video_path)

    async with AsyncSessionLocal() as db:
        # The asyncio lock only serializes one interpreter. During desktop
        # restart handoff two backend processes can briefly coexist, so use the
        # SQLite write lock as the cross-process promotion CAS.
        await db.execute(text("BEGIN IMMEDIATE"))

        job = await db.get(Job, job_id)
        if job is None:
            raise RuntimeError(f"Localization job not found: {job_id}")
        try:
            output = json.loads(job.output_json or "{}")
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Localization job output is not valid JSON") from exc
        if not isinstance(output, dict):
            raise RuntimeError("Localization job output is not an object")

        locked_path = _safe_storage_file(output.get("file"))
        if locked_path is None or locked_path.resolve() != video_path.resolve():
            await db.rollback()
            return {
                "promoted": False,
                "generated_video_id": output.get("generated_video_id"),
                "publish_ready": bool(output.get("publish_ready")),
                "stale": True,
            }

        publish_ready = _publishable_qc(output)
        existing_id = (
            # Re-read Job.output_json under BEGIN IMMEDIATE and trust that
            # canonical pointer before any caller snapshot. Another backend
            # process may have completed the same promotion while this one was
            # waiting for the DB write lock.
            output.get("generated_video_id")
            or existing_generated_video_id
            or output.get("supersedes_generated_video_id")
        )
        existing = (
            await db.get(GeneratedVideo, str(existing_id))
            if existing_id else None
        )

        # Exact artifact identity is idempotent even when the row is already
        # uploaded or currently being uploaded. Rerun startup backfills the
        # previous last-good artifact and must not fork a duplicate version.
        if existing is not None:
            existing_path = _safe_storage_file(existing.video_path)
            same_existing_artifact = bool(
                existing_path is not None
                and existing_path.resolve() == video_path.resolve()
                and str(existing.artifact_sha256 or "") == artifact_hash
            )
            if same_existing_artifact:
                if not _already_published(existing) and not await _publication_is_live_or_uncertain(db, existing.id):
                    existing.status = "ready" if publish_ready else "draft"
                    existing.review_status = (
                        "qc_ready"
                        if str(output.get("qc_status") or "").upper() == "READY"
                        else "qc_human_approved"
                        if publish_ready
                        else "qc_failed"
                        if str(output.get("qc_status") or "").upper() == "FAILED"
                        else "qc_needs_review"
                    )
                    existing.reviewed_at = datetime.utcnow()
                output["generated_video_id"] = existing.id
                output["publish_ready"] = publish_ready
                output.pop("supersedes_generated_video_id", None)
                output.pop("publish_registration_error", None)
                job.output_json = json.dumps(output)
                await db.commit()
                return {
                    "promoted": True,
                    "generated_video_id": existing.id,
                    "publish_ready": publish_ready,
                    "reused_generated_video": True,
                    "artifact_unchanged": True,
                }

        source_id = output.get("downloaded_video_id")
        source = await db.get(DownloadedVideo, str(source_id)) if source_id else None
        target_language = str(output.get("target_language") or "").strip()
        default_title = (
            f"{source.title} · Bản địa hóa"
            if source is not None and source.title
            else f"Bản địa hóa{(' · ' + target_language) if target_language else ''}"
        )
        aspect = str(output.get("target_aspect") or "9:16")
        duration_raw = output.get("duration_seconds") or output.get("final_duration")
        try:
            duration = (
                max(0, int(round(float(duration_raw))))
                if duration_raw is not None else None
            )
        except (TypeError, ValueError):
            duration = None

        existing_locked = bool(
            existing is not None
            and (
                _already_published(existing)
                or await _publication_is_live_or_uncertain(db, str(existing.id))
                or await _publication_identity_locked(db, str(existing.id))
            )
        )
        # A new unresolved/FAILED QC version must not replace the last READY
        # artifact either. It gets its own draft row and can later be promoted.
        reused = bool(
            existing is not None
            and not existing_locked
            and not (existing.status == "ready" and not publish_ready)
        )
        old_video_path = (
            Path(existing.video_path).resolve()
            if reused and existing.video_path else None
        )

        review_status = (
            "qc_ready"
            if str(output.get("qc_status") or "").upper() == "READY"
            else "qc_human_approved"
            if publish_ready
            else "qc_failed"
            if str(output.get("qc_status") or "").upper() == "FAILED"
            else "qc_needs_review"
        )

        if reused:
            video = existing
            video.video_path = str(video_path)
            video.artifact_sha256 = artifact_hash
            video.source_downloaded_video_id = (
                str(source_id) if source_id else None
            )
            video.source_type = "localization"
            video.gen_tier = "localization"
            video.aspect_ratio = aspect
            video.duration_seconds = duration
            video.status = "ready" if publish_ready else "draft"
            video.review_status = review_status
            video.reviewed_at = datetime.utcnow()
            video.publish_approved_at = None
            video.publish_approval_artifact_sha256 = None
            if not video.title:
                video.title = default_title
        else:
            video = GeneratedVideo(
                user_id=job.user_id or "local",
                source_downloaded_video_id=(
                    str(source_id) if source_id else None
                ),
                title=default_title,
                video_path=str(video_path),
                artifact_sha256=artifact_hash,
                aspect_ratio=aspect,
                duration_seconds=duration,
                gen_tier="localization",
                source_type="localization",
                status="ready" if publish_ready else "draft",
                review_status=review_status,
                reviewed_at=datetime.utcnow(),
            )
            db.add(video)

        await db.flush()

        output["generated_video_id"] = video.id
        output["publish_ready"] = publish_ready
        output.pop("supersedes_generated_video_id", None)
        output.pop("publish_registration_error", None)
        job.output_json = json.dumps(output)
        await db.commit()
        await db.refresh(video)
        new_id = video.id

    # Only an unpublished/unlocked row may be rewritten in place. Delete an old
    # unreferenced file after commit; never delete bytes referenced by Publish.
    old_path = _safe_storage_file(old_video_path)
    previous_path = _safe_storage_file(previous_output_path)
    cleanup_candidate = old_path or previous_path
    if cleanup_candidate and cleanup_candidate.resolve() != video_path.resolve():
        async with AsyncSessionLocal() as db:
            referenced = (await db.execute(
                select(GeneratedVideo.id).where(
                    GeneratedVideo.video_path == str(cleanup_candidate)
                )
            )).scalar_one_or_none()
        if referenced is None:
            try:
                cleanup_candidate.unlink(missing_ok=True)
            except OSError:
                pass

    return {
        "promoted": True,
        "generated_video_id": new_id,
        "publish_ready": publish_ready,
        "reused_generated_video": reused,
    }

async def promote_localization_output(
    job_id: str,
    *,
    existing_generated_video_id: str | None = None,
    previous_output_path: str | Path | None = None,
    expected_output_path: str | Path | None = None,
) -> dict:
    """Serialize registration so one job cannot create duplicate Publish rows."""
    async with _PROMOTION_LOCK:
        return await _promote_localization_output_unlocked(
            job_id,
            existing_generated_video_id=existing_generated_video_id,
            previous_output_path=previous_output_path,
            expected_output_path=expected_output_path,
        )


async def set_localization_publish_eligibility(
    job_id: str,
    *,
    eligible: bool,
) -> dict:
    """Sync QC acknowledgement with Publish eligibility without enqueue races."""
    if eligible:
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            if job is None:
                raise RuntimeError(f"Localization job not found: {job_id}")
            try:
                output = json.loads(job.output_json or "{}")
            except (TypeError, ValueError):
                output = {}
            if not isinstance(output, dict):
                output = {}
            existing_id = output.get("generated_video_id") or output.get(
                "supersedes_generated_video_id"
            )
        return await promote_localization_output(
            job_id,
            existing_generated_video_id=str(existing_id) if existing_id else None,
        )

    # Revocation and /videos/{id}/upload both take BEGIN IMMEDIATE before their
    # read/decision/write sequence. Whichever wins first establishes the truth:
    # either upload is queued and revocation is rejected, or approval is revoked
    # and the later enqueue sees a non-publishable artifact.
    async with AsyncSessionLocal() as db:
        await db.execute(text("BEGIN IMMEDIATE"))

        job = await db.get(Job, job_id)
        if job is None:
            raise RuntimeError(f"Localization job not found: {job_id}")
        try:
            output = json.loads(job.output_json or "{}")
        except (TypeError, ValueError):
            output = {}
        if not isinstance(output, dict):
            output = {}

        existing_id = output.get("generated_video_id") or output.get(
            "supersedes_generated_video_id"
        )
        if not existing_id:
            output["publish_ready"] = False
            job.output_json = json.dumps(output)
            await db.commit()
            return {
                "promoted": False,
                "generated_video_id": None,
                "publish_ready": False,
            }

        video = await db.get(GeneratedVideo, str(existing_id))
        if video is None:
            output["publish_ready"] = False
            job.output_json = json.dumps(output)
            await db.commit()
            return {
                "promoted": False,
                "generated_video_id": str(existing_id),
                "publish_ready": False,
            }

        if await _publication_is_live_or_uncertain(db, video.id):
            await db.rollback()
            raise ValueError(
                "Không thể thu hồi duyệt QC khi video đang xuất bản hoặc kết quả nền tảng chưa chắc chắn. Hãy đối soát tác vụ xuất bản trước."
            )

        video.publish_approved_at = None
        video.publish_approval_artifact_sha256 = None
        video.review_status = "qc_review_revoked"
        video.reviewed_at = datetime.utcnow()
        if not _already_published(video):
            video.status = "draft"

        output["publish_ready"] = False
        job.output_json = json.dumps(output)
        await db.commit()

    return {
        "promoted": False,
        "generated_video_id": str(existing_id),
        "publish_ready": False,
    }