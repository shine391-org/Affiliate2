# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
REST API for reading YouTube/TikTok/Douyin channel data.
Supports multiple connected channels per user.
YouTube/TikTok use their existing readers; Douyin is a separate authenticated
CDP connector and never aliases to TikTok.
"""
import logging

from fastapi import APIRouter, Query, HTTPException
from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.connected_channel import ConnectedChannel
from backend.models.user_settings import UserSettings
from backend.core.api_keys import get_youtube_api_key as _resolve_youtube_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/channels", tags=["channels"])


async def _get_douyin_provider():
    """Build the Douyin connector from the persisted local CDP endpoint."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        user_settings = result.scalar_one_or_none()

    from backend.services.download_browser_session import normalize_douyin_cdp_url
    from backend.sources.douyin import DouyinProvider

    cdp_url = normalize_douyin_cdp_url(
        getattr(user_settings, "douyin_cdp_url", None)
    )
    return DouyinProvider(cdp_url=cdp_url)


async def _get_youtube_api_key() -> str:
    """YouTube API key — per-user (BYOK) overrides .env fallback."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == "local")
        )
        user_settings = result.scalar_one_or_none()
    return _resolve_youtube_key(user_settings)


# ── List connected channels ──────────────────────────────────────────────────

@router.get("/list")
async def list_channels(platform: str = Query(None), user_id: str = "local"):
    """List all connected channels, optionally filtered by platform."""
    async with AsyncSessionLocal() as db:
        q = select(ConnectedChannel).where(ConnectedChannel.user_id == user_id)
        if platform:
            q = q.where(ConnectedChannel.platform == platform)
        q = q.order_by(ConnectedChannel.created_at.desc())
        result = await db.execute(q)
        channels = result.scalars().all()

    return {
        "channels": [
            {
                "id": ch.id,
                "platform": ch.platform,
                "channel_id": ch.channel_id,
                "channel_url": ch.channel_url,
                "channel_name": ch.channel_name,
                "thumbnail_url": ch.thumbnail_url,
                "subscriber_count": ch.subscriber_count,
                "video_count": ch.video_count,
            }
            for ch in channels
        ]
    }


# ── Connect / Disconnect ────────────────────────────────────────────────────

@router.post("/connect")
async def connect_channel(body: dict = None):
    """Connect a YouTube, TikTok or Douyin channel by URL."""
    body = body or {}
    platform = body.get("platform", "").strip().lower()
    url = body.get("url", "").strip()

    if platform not in ("youtube", "tiktok", "douyin"):
        raise HTTPException(
            status_code=400,
            detail="platform must be 'youtube', 'tiktok' or 'douyin'",
        )
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    channel_id = None
    channel_name = None
    thumbnail_url = None
    subscriber_count = 0
    video_count = 0

    if platform == "youtube":
        api_key = await _get_youtube_api_key()
        if not api_key:
            raise HTTPException(status_code=400, detail="YouTube API key not configured")

        from backend.services.channel_reader import resolve_youtube_channel_id
        channel_id = await resolve_youtube_channel_id(url, api_key)
        if not channel_id:
            raise HTTPException(status_code=400, detail="Could not find a YouTube channel at that URL")

        # Fetch channel info for display
        try:
            from backend.services.channel_reader import get_youtube_channel_info
            info = await get_youtube_channel_info(channel_id, api_key)
            if info:
                channel_name = info.get("title", "")
                thumbnail_url = info.get("thumbnail_url", "")
                subscriber_count = info.get("subscriber_count", 0)
                video_count = info.get("video_count", 0)
        except Exception:
            pass

    elif platform == "tiktok":
        if "tiktok.com/@" not in url:
            if url.startswith("@"):
                url = f"https://www.tiktok.com/{url}"
            elif not url.startswith("http"):
                url = f"https://www.tiktok.com/@{url}"

        # Fetch TikTok profile metadata (name, avatar, follower count)
        try:
            from backend.services.channel_reader import get_tiktok_channel_info
            info = await get_tiktok_channel_info(url)
            if info:
                channel_name = info.get("display_name", "")
                thumbnail_url = info.get("avatar_url", "")
                subscriber_count = info.get("follower_count", 0)
                video_count = info.get("video_count", 0)
        except Exception:
            pass  # metadata is optional — connect still succeeds

    elif platform == "douyin":
        provider = await _get_douyin_provider()
        if not url.startswith("http"):
            creator_id = url.strip().lstrip("@")
            if not creator_id:
                raise HTTPException(status_code=400, detail="Douyin profile URL is required")
            url = f"https://www.douyin.com/user/{creator_id}"

        creator_id = provider.creator_id_from_url(url)
        if not creator_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Douyin connector needs a full creator profile URL such as "
                    "https://www.douyin.com/user/<sec_user_id>."
                ),
            )
        channel_id = creator_id
        url = f"https://www.douyin.com/user/{creator_id}"
        try:
            info = await provider.creator_info(creator_id)
        except PermissionError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Douyin session is not ready. Open Settings → Source Providers, "
                    "verify CDP login, then retry. " + str(exc)[:250]
                ),
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Douyin profile lookup failed: {str(exc)[:250]}",
            ) from exc

        channel_name = info.get("display_name", "")
        thumbnail_url = info.get("avatar_url", "")
        subscriber_count = info.get("follower_count", 0)
        video_count = info.get("video_count", 0)

    async with AsyncSessionLocal() as db:
        # Check if this channel is already connected
        existing_q = select(ConnectedChannel).where(
            ConnectedChannel.user_id == "local",
            ConnectedChannel.platform == platform,
        )
        if channel_id:
            existing_q = existing_q.where(ConnectedChannel.channel_id == channel_id)
        else:
            existing_q = existing_q.where(ConnectedChannel.channel_url == url)
        result = await db.execute(existing_q)
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="This channel is already connected")

        ch = ConnectedChannel(
            user_id="local",
            platform=platform,
            channel_id=channel_id,
            channel_url=url,
            channel_name=channel_name,
            thumbnail_url=thumbnail_url,
            subscriber_count=subscriber_count,
            video_count=video_count,
        )
        db.add(ch)
        await db.commit()
        await db.refresh(ch)

        return {
            "ok": True,
            "channel": {
                "id": ch.id,
                "platform": platform,
                "channel_id": channel_id,
                "channel_url": url,
                "channel_name": channel_name,
                "thumbnail_url": thumbnail_url,
                "subscriber_count": subscriber_count,
                "video_count": video_count,
            },
        }


@router.post("/disconnect")
async def disconnect_channel(body: dict = None):
    """Disconnect a channel by its ID."""
    body = body or {}
    channel_db_id = body.get("id", "").strip()

    if not channel_db_id:
        raise HTTPException(status_code=400, detail="id is required")

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ConnectedChannel).where(ConnectedChannel.id == channel_db_id)
        )
        ch = result.scalar_one_or_none()
        if not ch:
            return {"ok": True}

        platform = ch.platform
        await db.delete(ch)
        await db.commit()

    from backend.services.channel_reader import cache_clear
    if platform == "youtube":
        cache_clear("yt_")
    elif platform == "tiktok":
        cache_clear("tt_")
    return {"ok": True}


# ── Search ───────────────────────────────────────────────────────────────────

@router.get("/search")
async def search_channels(
    q: str = Query(""),
    platform: str = Query("youtube"),
):
    """Search channels. Douyin intentionally accepts a profile URL, not fake name search."""
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Search query is required")

    if platform == "youtube":
        api_key = await _get_youtube_api_key()
        if not api_key:
            raise HTTPException(status_code=400, detail="YouTube API key not configured")
        try:
            from backend.services.channel_reader import search_youtube_channels
            results = await search_youtube_channels(q, api_key)
            return {"results": results}
        except Exception as e:
            logger.error(f"YouTube search failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail=f"YouTube search error: {str(e)[:200]}")

    elif platform == "tiktok":
        try:
            from backend.services.channel_reader import search_tiktok_profiles
            results = await search_tiktok_profiles(q)
            return {"results": results}
        except Exception as e:
            logger.error(f"TikTok search failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail=f"TikTok search error: {str(e)[:200]}")

    elif platform == "douyin":
        provider = await _get_douyin_provider()
        creator_id = provider.creator_id_from_url(q)
        if not creator_id:
            raise HTTPException(
                status_code=400,
                detail="Douyin name search is not exposed here. Paste a Douyin creator profile URL.",
            )
        try:
            info = await provider.creator_info(creator_id)
            return {"results": [{
                "channel_id": creator_id,
                "username": creator_id,
                "display_name": info.get("display_name", creator_id),
                "thumbnail_url": info.get("avatar_url", ""),
                "follower_count": info.get("follower_count", 0),
                "video_count": info.get("video_count", 0),
                "url": f"https://www.douyin.com/user/{creator_id}",
            }]}
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)[:300]) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Douyin profile lookup failed: {str(exc)[:200]}",
            ) from exc

    else:
        raise HTTPException(
            status_code=400,
            detail="platform must be 'youtube', 'tiktok' or 'douyin'",
        )


# ── Fetch channel videos ────────────────────────────────────────────────────

@router.get("/videos/{channel_db_id}")
async def get_channel_videos(
    channel_db_id: str,
    page_token: str | None = Query(None),
    refresh: bool = Query(False),
):
    """Fetch videos for a specific connected channel."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ConnectedChannel).where(ConnectedChannel.id == channel_db_id)
        )
        ch = result.scalar_one_or_none()

    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")

    if ch.platform == "youtube":
        api_key = await _get_youtube_api_key()
        if not api_key:
            raise HTTPException(status_code=400, detail="YouTube API key not configured")

        if refresh:
            from backend.services.channel_reader import cache_clear
            cache_clear(f"yt_{ch.channel_id}")

        try:
            from backend.services.channel_reader import get_youtube_channel
            result = await get_youtube_channel(ch.channel_id, api_key, page_token=page_token)
            return result
        except Exception as e:
            logger.error(f"YouTube channel fetch failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail=f"YouTube API error: {str(e)[:200]}")

    elif ch.platform == "tiktok":
        if refresh:
            from backend.services.channel_reader import cache_clear
            cache_clear(f"tt_{ch.channel_url}")

        try:
            from backend.services.channel_reader import get_tiktok_channel
            result = await get_tiktok_channel(ch.channel_url)
            return result
        except Exception as e:
            logger.error(f"TikTok channel fetch failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail=f"TikTok error: {str(e)[:200]}")


    elif ch.platform == "douyin":
        from backend.sources.base import ProviderStatus

        provider = await _get_douyin_provider()
        creator_id = ch.channel_id or provider.creator_id_from_url(ch.channel_url)
        if not creator_id:
            raise HTTPException(status_code=400, detail="Stored Douyin channel has no creator ID")

        try:
            info = await provider.creator_info(creator_id)
            history = await provider.creator_history(
                creator_id,
                creator_name=ch.channel_name or "",
                limit=200,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)[:300]) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Douyin connector failed: {str(exc)[:250]}",
            ) from exc

        if history.status == ProviderStatus.AUTH_REQUIRED:
            raise HTTPException(status_code=409, detail=history.error or "Douyin login required")
        if history.status == ProviderStatus.ERROR:
            raise HTTPException(status_code=502, detail=history.error or "Douyin creator fetch failed")

        videos = [{
            "video_id": item.get("video_id", ""),
            "title": item.get("title", ""),
            "cover_url": item.get("thumbnail_url", ""),
            "created_at": (
                item.get("upload_date").timestamp()
                if getattr(item.get("upload_date"), "timestamp", None)
                else 0
            ),
            "view_count": item.get("views", 0) or 0,
            "like_count": item.get("likes", 0) or 0,
            "comment_count": item.get("comments", 0) or 0,
            "share_count": item.get("shares", 0) or 0,
            "duration": item.get("duration_seconds", 0) or 0,
            "url": item.get("video_url", ""),
        } for item in history.items]

        return {
            "connected": True,
            "user": {
                "display_name": info.get("display_name", ch.channel_name or ""),
                "avatar_url": info.get("avatar_url", ch.thumbnail_url or ""),
                "follower_count": info.get("follower_count", ch.subscriber_count or 0),
                "video_count": info.get("video_count", ch.video_count or len(videos)),
            },
            "videos": videos,
            "has_more": False,
        }


# ── Analyze (reuses existing download + analyze pipeline) ───────────────────

@router.post("/analyze")
async def analyze_channel_video(body: dict = None):
    """Download + transcribe + extract insights from one of the user's own videos."""
    body = body or {}
    video_url = body.get("video_url", "").strip()
    if not video_url:
        raise HTTPException(status_code=400, detail="video_url is required")

    from backend.core.task_runner import run_download_url, dispatch
    from backend.agents.job_helper import create_job

    title = body.get("title", "")
    job = await create_job("download", "local", {"url": video_url, "title": title, "source": "my_channel"})
    dispatch(run_download_url(job_id=job.id, url=video_url, title=title, user_id="local"))

    return {"job_id": job.id, "message": f"Analyzing video: {title or video_url}"}
