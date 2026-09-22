# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
REST API for reading YouTube/TikTok channel data.
Supports multiple connected channels per user.
Connect by URL — no OAuth needed. Uses YouTube Data API key + yt-dlp.
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
    """Connect a YouTube or TikTok channel by URL. Supports multiple channels."""
    body = body or {}
    platform = body.get("platform", "").strip().lower()
    url = body.get("url", "").strip()

    if platform not in ("youtube", "tiktok"):
        raise HTTPException(status_code=400, detail="platform must be 'youtube' or 'tiktok'")
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
    cache_clear("yt_" if platform == "youtube" else "tt_")
    return {"ok": True}


# ── Search ───────────────────────────────────────────────────────────────────

@router.get("/search")
async def search_channels(
    q: str = Query(""),
    platform: str = Query("youtube"),
):
    """Search for YouTube channels or TikTok profiles by name."""
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

    else:
        raise HTTPException(status_code=400, detail="platform must be 'youtube' or 'tiktok'")


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
