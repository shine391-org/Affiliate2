# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Agent 5: Upload Orchestrator
Uploads generated videos to YouTube and/or TikTok.
"""
import json
import logging
from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.generated_video import GeneratedVideo
from backend.models.user_settings import UserSettings
from backend.core.crypto import decrypt, DecryptionError
from backend.core.ws_manager import ws_manager
from backend.agents.job_helper import update_job_status
from backend.core.exceptions import UploadAuthError, safe_json_loads

logger = logging.getLogger(__name__)


ALLOWED_PUBLISH_PLATFORMS = {"youtube", "tiktok", "instagram"}


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
    """Cheap pre-queue check; UploadAgent still validates/decrypts at execution."""
    fields = {
        "youtube": ("youtube_credentials_json_encrypted",),
        "tiktok": ("tiktok_upload_token_encrypted", "tiktok_cookie_encrypted"),
        "instagram": ("instagram_access_token_encrypted",),
    }
    return [p for p in platforms if not settings or not any(
        getattr(settings, field, None) for field in fields[p]
    )]


class UploadAgent:
    async def run(
        self,
        job_id: str,
        generated_video_id: str,
        platforms: list[str],
        user_id: str = "local",
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

        # Load user settings
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == user_id)
            )
            user_settings = result.scalar_one_or_none()

        uploaded = []
        errors = []
        total_platforms = len(platforms)

        for i, platform in enumerate(platforms):
            pct = int((i / total_platforms) * 80) + 10
            await ws_manager.send_progress(job_id, pct, f"Uploading to {platform}...", user_id)

            try:
                if platform == "youtube":
                    result = await self._upload_youtube(video, user_settings)
                    uploaded.append("youtube")
                    await self._mark_platform_uploaded(
                        generated_video_id, "youtube",
                        id_field="youtube_video_id", id_value=result.get("video_id"),
                    )
                    await ws_manager.send({
                        "type": "upload_complete",
                        "platform": "youtube",
                        "url": result.get("url", ""),
                        "video_id": result.get("video_id"),
                    }, user_id)

                elif platform == "tiktok":
                    result = await self._upload_tiktok(video, user_settings)
                    uploaded.append("tiktok")
                    await self._mark_platform_uploaded(
                        generated_video_id, "tiktok",
                        id_field="tiktok_publish_id", id_value=result.get("publish_id"),
                    )
                    await ws_manager.send({
                        "type": "upload_complete",
                        "platform": "tiktok",
                        "publish_id": result.get("publish_id"),
                    }, user_id)

                elif platform == "instagram":
                    result = await self._upload_instagram(video, user_settings)
                    uploaded.append("instagram")
                    await self._mark_platform_uploaded(
                        generated_video_id, "instagram",
                        id_field="instagram_media_id", id_value=result.get("media_id"),
                    )
                    await ws_manager.send({
                        "type": "upload_complete",
                        "platform": "instagram",
                        "url": result.get("permalink", ""),
                        "media_id": result.get("media_id"),
                    }, user_id)

                else:
                    logger.warning(f"Unknown upload platform: {platform}")
                    errors.append(f"Unknown platform: {platform}")

            except UploadAuthError as e:
                logger.error(f"Upload auth error for {platform}: {e}")
                errors.append(f"{platform}: {str(e)}")
                await ws_manager.send_constraint_warning(
                    constraint=f"{platform}_upload_auth",
                    message=str(e),
                    severity="error",
                    wizard_id=f"{platform}_auth" if platform == "youtube" else f"{platform}_upload_auth",
                    user_id=user_id,
                )
            except Exception as e:
                logger.error(f"Upload failed for {platform}: {e}", exc_info=True)
                errors.append(f"{platform}: {str(e)}")

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
                error_message=f"Upload failed: {'; '.join(errors)}",
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

        return await upload_to_youtube(
            video_path=video.video_path,
            title=video.youtube_title or video.title or "Untitled",
            description=video.youtube_description or "",
            tags=tags,
            credentials_json=credentials_json,
        )

    async def _upload_instagram(self, video: GeneratedVideo, user_settings) -> dict:
        """Upload to Instagram Reels via Graph API."""
        if not user_settings or not user_settings.instagram_access_token_encrypted:
            raise UploadAuthError("Instagram not connected. Connect your Instagram account in Settings.")

        try:
            access_token = decrypt(user_settings.instagram_access_token_encrypted)
        except DecryptionError:
            raise UploadAuthError("Instagram credentials corrupted — please reconnect Instagram in Settings.")
        ig_user_id = user_settings.instagram_user_id or ""

        from backend.services.instagram_uploader import upload_to_instagram

        caption = video.tiktok_title or video.title or ""  # Reuse TikTok caption style

        return await upload_to_instagram(
            video_path=video.video_path,
            caption=caption,
            access_token=access_token,
            ig_user_id=ig_user_id,
        )

    async def _upload_tiktok(self, video: GeneratedVideo, user_settings) -> dict:
        """Upload to TikTok via OAuth or cookie fallback."""
        access_token = ""
        cookie_sessionid = ""

        if user_settings:
            try:
                if user_settings.tiktok_upload_token_encrypted:
                    access_token = decrypt(user_settings.tiktok_upload_token_encrypted)
                if not access_token and user_settings.tiktok_cookie_encrypted:
                    cookie_sessionid = decrypt(user_settings.tiktok_cookie_encrypted)
            except DecryptionError:
                raise UploadAuthError("TikTok credentials corrupted — please reconnect TikTok in Settings.")

        if not access_token and not cookie_sessionid:
            raise UploadAuthError("TikTok not connected. Set up TikTok upload in Settings.")

        from backend.services.tiktok_uploader import upload_to_tiktok

        privacy = "PUBLIC_TO_EVERYONE"
        if user_settings and user_settings.tiktok_default_privacy:
            privacy = user_settings.tiktok_default_privacy

        return await upload_to_tiktok(
            video_path=video.video_path,
            title=video.tiktok_title or video.title or "Untitled",
            access_token=access_token,
            privacy=privacy,
            cookie_sessionid=cookie_sessionid,
        )
