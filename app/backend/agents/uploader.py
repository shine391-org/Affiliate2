# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Agent 5: Upload Orchestrator
Uploads generated videos to YouTube and/or TikTok.
"""
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.generated_video import GeneratedVideo
from backend.models.phase_g import PublicationRecord
from backend.models.user_settings import UserSettings
from backend.core.crypto import decrypt, DecryptionError
from backend.core.ws_manager import ws_manager
from backend.agents.job_helper import update_job_status
from backend.core.exceptions import UploadAuthError, UploadOutcomeUncertainError, safe_json_loads

logger = logging.getLogger(__name__)


ALLOWED_PUBLISH_PLATFORMS = {"youtube", "tiktok", "instagram", "facebook"}


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def approved_artifact_error(video: GeneratedVideo) -> str | None:
    """Return why the current bytes are not the exact approved artifact."""
    if not video.video_path:
        return "Video file path is missing"
    path = Path(video.video_path)
    if not path.is_file():
        return "Video file not found on disk"
    if not video.publish_approval_artifact_sha256:
        return "Publish approval is not bound to an exact video artifact. Re-approve the video."
    if file_sha256(path) != video.publish_approval_artifact_sha256:
        return "Video artifact changed since approval. Re-approve the current file before publishing."
    return None


def normalize_publish_platforms(platforms) -> list[str]:
    """Return a canonical, validated publish-platform list.

    HTTP, Planner/Messaging and direct UploadAgent callers all pass through the
    same contract so an LLM-shaped string cannot be iterated character by
    character and unsupported destinations never reach provider code.
    """
    if isinstance(platforms, str):
        platforms = [platforms]
    if not isinstance(platforms, (list, tuple)) or not platforms:
        raise ValueError("At least one publish platform is required.")

    normalized = []
    for platform in platforms:
        if not isinstance(platform, str) or not platform.strip():
            raise ValueError("Publish platforms must be non-empty strings.")
        name = platform.strip().lower()
        if name not in ALLOWED_PUBLISH_PLATFORMS:
            raise ValueError(f"Unsupported publish platform(s): {name}")
        if name not in normalized:
            normalized.append(name)
    return normalized


def published_destinations(video: GeneratedVideo) -> set[str]:
    """Actual persisted publish destinations, not transaction IDs."""
    saved = safe_json_loads(video.uploaded_platforms_json, [], logger)
    return set(saved) if isinstance(saved, list) else set()


def disconnected_destinations(settings: UserSettings | None, platforms: list[str]) -> list[str]:
    """Cheap pre-queue check; every required field must be present."""
    fields = {
        "youtube": ("youtube_credentials_json_encrypted",),
        # Canonical Publish requires provider-verifiable OAuth Direct Post.
        # A legacy session cookie is deliberately not considered connected.
        "tiktok": ("tiktok_upload_token_encrypted",),
        "instagram": ("meta_page_access_token_encrypted", "instagram_user_id"),
        "facebook": ("meta_page_access_token_encrypted", "meta_page_id"),
    }
    return [
        platform
        for platform in platforms
        if not settings or not all(
            getattr(settings, field, None) for field in fields[platform]
        )
    ]


async def uncertain_publication_destinations(video_id: str) -> set[str]:
    """Destinations whose provider outcome may already have side effects.

    A stale PUBLISHING row can mean the process died after the provider accepted
    the upload. PERSISTENCE_ERROR explicitly means provider success was observed
    but canonical local state could not be committed. Neither is safe to retry
    automatically because that can duplicate a public post.
    """
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(PublicationRecord.platform).where(
            PublicationRecord.generated_video_id == video_id,
            PublicationRecord.status.in_(["PUBLISHING", "PERSISTENCE_ERROR", "OUTCOME_UNKNOWN"]),
        ))).scalars().all()
    return {str(platform) for platform in rows if platform}


def _publication_metadata(
    video: GeneratedVideo,
    platform: str,
    user_settings: UserSettings | None,
    platform_options: dict | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Return the exact title/caption/privacy semantics sent to a provider."""
    if platform == "youtube":
        return (
            video.youtube_title or video.title or "Untitled",
            video.youtube_description or "",
            None,
        )
    if platform == "tiktok":
        title = video.tiktok_title or video.title or "Untitled"
        platform_options = platform_options if isinstance(platform_options, dict) else {}
        privacy = (
            platform_options.get("privacy_level")
            or (
                user_settings.tiktok_default_privacy
                if user_settings and user_settings.tiktok_default_privacy
                else "PUBLIC_TO_EVERYONE"
            )
        )
        return title, title, privacy
    if platform in {"instagram", "facebook"}:
        caption = video.tiktok_description or video.youtube_description or video.tiktok_title or video.title or ""
        return video.title, caption, None
    raise ValueError(f"Unsupported publish platform(s): {platform}")


def _publication_result_fields(platform: str, result: dict | None) -> tuple[str | None, str | None, str | None, str | None]:
    result = result or {}
    provider_transaction_id = None
    public_content_id = None
    identifier_kind = None
    external_url = None
    if platform == "youtube":
        public_content_id = result.get("video_id")
        identifier_kind = "public_video_id" if public_content_id else None
        external_url = result.get("url")
    elif platform == "tiktok":
        provider_transaction_id = result.get("publish_id")
        public_content_id = result.get("post_id")
        identifier_kind = (
            "public_video_id" if public_content_id
            else "publish_transaction_id" if provider_transaction_id
            else None
        )
        if public_content_id:
            external_url = f"https://www.tiktok.com/video/{public_content_id}"
    elif platform == "instagram":
        provider_transaction_id = result.get("container_id")
        public_content_id = result.get("media_id")
        identifier_kind = "public_media_id" if public_content_id else None
        external_url = result.get("permalink")
    elif platform == "facebook":
        public_content_id = result.get("video_id")
        identifier_kind = "public_video_id" if public_content_id else None
        external_url = result.get("url")
    return provider_transaction_id, public_content_id, identifier_kind, external_url


class UploadAgent:
    async def _send_upload_complete(self, payload: dict, user_id: str) -> None:
        """Notification failure must never rewrite an already-persisted publish."""
        try:
            await ws_manager.send(payload, user_id)
        except Exception:
            logger.warning(
                "Upload completion notification failed for %s",
                payload.get("platform"),
                exc_info=True,
            )

    async def run(
        self,
        job_id: str,
        generated_video_id: str,
        platforms: list[str],
        user_id: str = "local",
        publish_options: dict | None = None,
    ):
        """Upload a generated video to specified platforms."""
        await update_job_status(job_id, "running", progress_pct=0, current_step="Preparing upload...")

        try:
            platforms = normalize_publish_platforms(platforms)
        except ValueError as exc:
            await update_job_status(job_id, "failed", error_message=str(exc))
            return

        # Load video
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(GeneratedVideo).where(GeneratedVideo.id == generated_video_id)
            )
            video = result.scalar_one_or_none()

        if not video:
            await update_job_status(job_id, "failed", error_message="Generated video not found")
            return

        if not video.video_path:
            await update_job_status(job_id, "failed", error_message="Video file path is missing")
            return

        # Canonical Phase-F gate. This lives inside the orchestrator as well as
        # the HTTP endpoint so Planner/Messaging/internal calls cannot bypass
        # the same explicit human approval simply by dispatching run_upload
        # directly.
        if not video.publish_approved_at:
            await update_job_status(
                job_id,
                "failed",
                error_message="Video must be explicitly approved for publishing first.",
            )
            return

        artifact_error = approved_artifact_error(video)
        if artifact_error:
            await update_job_status(job_id, "failed", error_message=artifact_error)
            return

        already_uploaded = published_destinations(video)
        duplicates = [platform for platform in platforms if platform in already_uploaded]
        if duplicates:
            await update_job_status(
                job_id,
                "failed",
                error_message=(
                    "Already published to: " + ", ".join(duplicates)
                    + ". Republish is not supported by the standard Publish flow."
                ),
            )
            return

        uncertain = sorted(
            set(platforms) & await uncertain_publication_destinations(generated_video_id)
        )
        if uncertain:
            await update_job_status(
                job_id,
                "failed",
                error_message=(
                    "Publish outcome requires review before retrying: "
                    + ", ".join(uncertain)
                    + ". The provider may already have accepted this upload."
                ),
            )
            return

        # Load user settings
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == user_id)
            )
            user_settings = result.scalar_one_or_none()

        publish_options = publish_options if isinstance(publish_options, dict) else {}
        uploaded = []
        errors = []
        total_platforms = len(platforms)

        for i, platform in enumerate(platforms):
            platform_options = publish_options.get(platform, {})
            if not isinstance(platform_options, dict):
                platform_options = {}
            pct = int((i / total_platforms) * 80) + 10
            await ws_manager.send_progress(job_id, pct, f"Uploading to {platform}...", user_id)

            publication_id = await self._create_publication_record(
                video, job_id, platform, user_settings, platform_options
            )
            provider_result = None
            try:
                if platform == "youtube":
                    result = await self._upload_youtube(video, user_settings)
                    provider_result = result
                    await self._finalize_successful_publication(
                        publication_id,
                        generated_video_id,
                        "youtube",
                        id_field="youtube_video_id",
                        id_value=result.get("video_id"),
                        result=result,
                    )
                    uploaded.append("youtube")
                    await self._send_upload_complete({
                        "type": "upload_complete",
                        "platform": "youtube",
                        "url": result.get("url", ""),
                        "video_id": result.get("video_id"),
                    }, user_id)

                elif platform == "tiktok":
                    result = await self._upload_tiktok(
                        video, user_settings, platform_options
                    )
                    provider_result = result
                    await self._finalize_successful_publication(
                        publication_id,
                        generated_video_id,
                        "tiktok",
                        id_field="tiktok_publish_id",
                        id_value=result.get("publish_id"),
                        result=result,
                    )
                    uploaded.append("tiktok")
                    await self._send_upload_complete({
                        "type": "upload_complete",
                        "platform": "tiktok",
                        "publish_id": result.get("publish_id"),
                        "post_id": result.get("post_id"),
                    }, user_id)

                elif platform == "instagram":
                    result = await self._upload_instagram(video, user_settings)
                    provider_result = result
                    await self._finalize_successful_publication(
                        publication_id,
                        generated_video_id,
                        "instagram",
                        id_field="instagram_media_id",
                        id_value=result.get("media_id"),
                        result=result,
                    )
                    uploaded.append("instagram")
                    await self._send_upload_complete({
                        "type": "upload_complete",
                        "platform": "instagram",
                        "url": result.get("permalink", ""),
                        "media_id": result.get("media_id"),
                    }, user_id)

                elif platform == "facebook":
                    result = await self._upload_facebook(video, user_settings)
                    provider_result = result
                    await self._finalize_successful_publication(
                        publication_id,
                        generated_video_id,
                        "facebook",
                        id_field="",
                        id_value=result.get("video_id"),
                        result=result,
                    )
                    uploaded.append("facebook")
                    await self._send_upload_complete({
                        "type": "upload_complete",
                        "platform": "facebook",
                        "url": result.get("url", ""),
                        "video_id": result.get("video_id"),
                    }, user_id)

                else:
                    logger.warning(f"Unknown upload platform: {platform}")
                    errors.append(f"Unknown platform: {platform}")

            except UploadAuthError as e:
                logger.error(f"Upload auth error for {platform}: {e}")
                errors.append(f"{platform}: {str(e)}")
                await self._finish_publication_record(
                    publication_id, platform, "FAILED", error_message=str(e)
                )
                await ws_manager.send_constraint_warning(
                    constraint=f"{platform}_upload_auth",
                    message=str(e),
                    severity="error",
                    wizard_id=f"{platform}_auth" if platform == "youtube" else f"{platform}_upload_auth",
                    user_id=user_id,
                )
            except UploadOutcomeUncertainError as e:
                message = (
                    f"{platform}: provider outcome is unknown: {e}. "
                    "Do not retry automatically; verify the provider first."
                )
                logger.warning(message)
                errors.append(message)
                await self._finish_publication_record(
                    publication_id,
                    platform,
                    "OUTCOME_UNKNOWN",
                    result=e.provider_result,
                    error_message=str(e),
                )
            except Exception as e:
                if provider_result is not None:
                    message = (
                        f"{platform}: provider upload succeeded but local persistence failed: {e}. "
                        "Do not retry automatically; verify the provider first."
                    )
                    logger.critical(message, exc_info=True)
                    errors.append(message)
                    try:
                        await self._finish_publication_record(
                            publication_id,
                            platform,
                            "PERSISTENCE_ERROR",
                            result=provider_result,
                            error_message=str(e),
                        )
                    except Exception:
                        logger.critical(
                            "Could not persist uncertain publication state for %s",
                            platform,
                            exc_info=True,
                        )
                else:
                    logger.error(f"Upload failed for {platform}: {e}", exc_info=True)
                    errors.append(f"{platform}: {str(e)}")
                    await self._finish_publication_record(
                        publication_id, platform, "FAILED", error_message=str(e)
                    )

        # Final status
        if uploaded:
            msg = f"Uploaded to {', '.join(uploaded)}"
            if errors:
                msg += f" (errors on: {'; '.join(errors)})"
            await update_job_status(
                job_id, "success",
                progress_pct=100,
                current_step=msg,
                output_data={"uploaded_platforms": uploaded, "errors": errors},
            )
        else:
            await update_job_status(
                job_id, "failed",
                error_message=f"Publish did not complete cleanly: {'; '.join(errors)}",
            )

    async def _mark_platform_uploaded(
        self, video_id: str, platform: str, id_field: str, id_value: str,
    ):
        """Update DB: set platform-specific ID, add to uploaded_platforms, mark as uploaded."""
        async with AsyncSessionLocal() as db:
            db_result = await db.execute(
                select(GeneratedVideo).where(GeneratedVideo.id == video_id)
            )
            v = db_result.scalar_one_or_none()
            if not v:
                logger.warning(f"Video {video_id} not found for platform update")
                return
            if id_field and id_value:
                setattr(v, id_field, id_value)
            existing = safe_json_loads(v.uploaded_platforms_json, [], logger)
            if platform not in existing:
                existing.append(platform)
            v.uploaded_platforms_json = json.dumps(existing)
            v.status = "uploaded"
            await db.commit()

    async def _finalize_successful_publication(
        self,
        record_id: str,
        video_id: str,
        platform: str,
        *,
        id_field: str,
        id_value: str | None,
        result: dict,
    ) -> None:
        """Atomically persist the local projection and publication history."""
        provider_transaction_id, public_content_id, identifier_kind, external_url = (
            _publication_result_fields(platform, result)
        )
        async with AsyncSessionLocal() as db:
            video = await db.get(GeneratedVideo, video_id)
            record = await db.get(PublicationRecord, record_id)
            if not video:
                raise RuntimeError(f"Generated video {video_id} vanished during upload")
            if not record:
                raise RuntimeError(f"Publication record {record_id} vanished during upload")

            if id_field and id_value:
                setattr(video, id_field, id_value)
            existing = safe_json_loads(video.uploaded_platforms_json, [], logger)
            if not isinstance(existing, list):
                existing = []
            if platform not in existing:
                existing.append(platform)
            video.uploaded_platforms_json = json.dumps(existing)
            video.status = "uploaded"

            record.status = "PUBLISHED"
            record.provider_transaction_id = provider_transaction_id
            record.public_content_id = public_content_id
            record.identifier_kind = identifier_kind
            record.external_url = external_url
            record.error_message = None
            record.updated_at = datetime.utcnow()
            record.published_at = datetime.utcnow()
            await db.commit()

    async def _create_publication_record(
        self,
        video: GeneratedVideo,
        job_id: str,
        platform: str,
        user_settings: UserSettings | None,
        platform_options: dict | None = None,
    ) -> str:
        title, caption, privacy = _publication_metadata(
            video, platform, user_settings, platform_options
        )
        record = PublicationRecord(
            generated_video_id=video.id,
            platform=platform,
            job_id=job_id,
            status="PUBLISHING",
            title=title or video.title,
            caption=caption,
            privacy=privacy,
            mapping_status="mapped",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        async with AsyncSessionLocal() as db:
            db.add(record)
            await db.commit()
            return record.id

    async def _finish_publication_record(
        self,
        record_id: str,
        platform: str,
        status: str,
        *,
        result: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        provider_transaction_id, public_content_id, identifier_kind, external_url = (
            _publication_result_fields(platform, result)
        )
        async with AsyncSessionLocal() as db:
            record = await db.get(PublicationRecord, record_id)
            if not record:
                logger.warning("Publication record %s vanished during upload", record_id)
                return
            record.status = status
            record.provider_transaction_id = provider_transaction_id
            record.public_content_id = public_content_id
            record.identifier_kind = identifier_kind
            record.external_url = external_url
            record.error_message = error_message
            record.updated_at = datetime.utcnow()
            record.published_at = (
                datetime.utcnow()
                if status in {"PUBLISHED", "PERSISTENCE_ERROR"}
                else None
            )
            await db.commit()

    async def _upload_youtube(self, video: GeneratedVideo, user_settings) -> dict:
        """Upload to YouTube via OAuth."""
        if not user_settings or not user_settings.youtube_credentials_json_encrypted:
            raise UploadAuthError("YouTube not connected. Set up YouTube OAuth in Settings.")

        try:
            credentials_json = decrypt(user_settings.youtube_credentials_json_encrypted)
        except DecryptionError:
            raise UploadAuthError("YouTube credentials corrupted — please reconnect YouTube in Settings.")

        from backend.services.youtube_uploader import upload_to_youtube

        tags = safe_json_loads(video.youtube_tags_json, [], logger)

        title, description, _ = _publication_metadata(video, "youtube", user_settings)
        return await upload_to_youtube(
            video_path=video.video_path,
            title=title,
            description=description,
            tags=tags,
            credentials_json=credentials_json,
        )

    @staticmethod
    def _meta_publish_credentials(user_settings) -> tuple[str, str]:
        if not user_settings or not getattr(user_settings, "meta_page_access_token_encrypted", None):
            raise UploadAuthError(
                "Meta publish is not connected. Add a verified Facebook Page access token in Settings."
            )
        try:
            access_token = decrypt(user_settings.meta_page_access_token_encrypted)
        except DecryptionError as exc:
            raise UploadAuthError(
                "Meta publish credentials are corrupted — reconnect in Settings."
            ) from exc
        page_id = str(getattr(user_settings, "meta_page_id", None) or "")
        return access_token, page_id

    async def _upload_instagram(self, video: GeneratedVideo, user_settings) -> dict:
        """Upload to a linked Instagram Professional account via Meta Graph API."""
        access_token, _ = self._meta_publish_credentials(user_settings)
        ig_user_id = str(getattr(user_settings, "instagram_user_id", None) or "")
        if not ig_user_id:
            raise UploadAuthError(
                "No linked Instagram Professional account was verified for this Facebook Page."
            )

        from backend.services.meta_uploader import upload_instagram_reel

        _, caption, _ = _publication_metadata(video, "instagram", user_settings)
        return await upload_instagram_reel(
            video_path=video.video_path,
            caption=caption or "",
            access_token=access_token,
            ig_user_id=ig_user_id,
        )

    async def _upload_facebook(self, video: GeneratedVideo, user_settings) -> dict:
        """Upload to the verified Facebook Page via the Reels Publishing API."""
        access_token, page_id = self._meta_publish_credentials(user_settings)
        if not page_id:
            raise UploadAuthError("Facebook Page ID is missing from Meta Publish settings.")

        from backend.services.meta_uploader import upload_facebook_reel

        _, description, _ = _publication_metadata(video, "facebook", user_settings)
        return await upload_facebook_reel(
            video_path=video.video_path,
            description=description or "",
            page_id=page_id,
            access_token=access_token,
        )

    async def _upload_tiktok(
        self,
        video: GeneratedVideo,
        user_settings,
        platform_options: dict | None = None,
    ) -> dict:
        """Direct-post to TikTok through the official OAuth Content Posting API."""
        access_token = ""
        legacy_cookie_present = False

        if user_settings:
            try:
                if user_settings.tiktok_upload_token_encrypted:
                    access_token = decrypt(user_settings.tiktok_upload_token_encrypted)
                legacy_cookie_present = bool(user_settings.tiktok_cookie_encrypted)
            except DecryptionError:
                raise UploadAuthError(
                    "TikTok credentials corrupted — please reconnect TikTok in Settings."
                )

        if not access_token:
            if legacy_cookie_present:
                raise UploadAuthError(
                    "TikTok only has a legacy cookie connection. Reconnect with "
                    "TikTok OAuth before using canonical Publish."
                )
            raise UploadAuthError(
                "TikTok OAuth is not connected. Reconnect TikTok in Settings before publishing."
            )

        from backend.services.tiktok_uploader import upload_to_tiktok

        options = platform_options if isinstance(platform_options, dict) else {}
        title, _, privacy = _publication_metadata(
            video, "tiktok", user_settings, options
        )

        return await upload_to_tiktok(
            video_path=video.video_path,
            title=title,
            access_token=access_token,
            privacy=privacy,
            disable_duet=bool(options.get("disable_duet", False)),
            disable_comment=bool(options.get("disable_comment", False)),
            disable_stitch=bool(options.get("disable_stitch", False)),
        )
