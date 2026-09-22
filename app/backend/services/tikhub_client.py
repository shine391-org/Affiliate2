# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""TikHub API client — paid fallback for TikTok/Douyin search.

TikHub API v1 endpoints (updated April 2026):
  TikTok: /api/v1/tiktok/app/v3/fetch_video_search_result
  Douyin: /api/v1/douyin/app/v3/fetch_video_search_result
"""
import logging
import httpx

logger = logging.getLogger(__name__)

TIKHUB_BASE = "https://api.tikhub.io"


async def search_tiktok(
    niche: str,
    api_key: str,
    max_results: int = 30,
) -> list[dict]:
    """Search TikTok videos via TikHub API."""
    if not api_key:
        logger.warning("TikHub API key not configured — skipping TikTok scout")
        return []

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{TIKHUB_BASE}/api/v1/tiktok/app/v3/fetch_video_search_result",
                params={"keyword": niche, "count": min(max_results, 30)},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        results = _parse_tiktok_app_v3_as(data, "tiktok")

        # AI fallback: if rule-based parsing found nothing but API returned data
        if not results and data.get("data"):
            results = await _ai_parse_fallback(data, "tiktok", niche)

        return results

    except httpx.HTTPStatusError as e:
        logger.error(f"TikHub API error: {e.response.status_code} — {e.response.text[:200]}")
        return []
    except Exception as e:
        logger.error(f"TikHub client error: {e}")
        return []


def _parse_tiktok_app_v3_as(data: dict, platform: str = "tiktok") -> list[dict]:
    """
    Parse TikHub app v3 response format.

    The actual TikTok API nests videos in various keys depending on the endpoint:
      - aweme_list[]           (most common — direct aweme objects)
      - search_item_list[]     (search results — each has .aweme_info)
      - business_data[]        (business API — each has .data.aweme_info)
      - aweme_detail[]         (web endpoint format)
      - videos[]               (legacy format)
    """
    inner = data.get("data", {})
    if not isinstance(inner, dict):
        logger.warning(f"TikHub: unexpected data type: {type(inner)}")
        return []

    results = []

    # Log available data keys for debugging
    list_sizes = {k: len(v) for k, v in inner.items() if isinstance(v, list) and v}
    if list_sizes:
        logger.info(f"TikHub response lists: {list_sizes}")

    # Format 1: aweme_list[] — direct aweme objects (most common)
    aweme_list = inner.get("aweme_list") or []
    if aweme_list:
        for aweme in aweme_list:
            parsed = _aweme_to_result(aweme, platform)
            if parsed:
                results.append(parsed)
        return results

    # Format 2: search_item_list[] — search results with nested aweme_info
    search_items = inner.get("search_item_list", [])
    if search_items:
        for item in search_items:
            aweme = item.get("aweme_info") or item.get("aweme_mix_info", {}).get("mix_items", [{}])[0]
            if aweme:
                parsed = _aweme_to_result(aweme, platform)
                if parsed:
                    results.append(parsed)
        return results

    # Format 3: business_data[].data.aweme_info
    business_data = inner.get("business_data", [])
    if business_data:
        for item in business_data:
            aweme = item.get("data", {}).get("aweme_info")
            if not aweme:
                continue
            parsed = _aweme_to_result(aweme, platform)
            if parsed:
                results.append(parsed)
        return results

    # Format 4: aweme_detail[] (web endpoint)
    aweme_detail = inner.get("aweme_detail", [])
    if aweme_detail:
        for aweme in aweme_detail:
            parsed = _aweme_to_result(aweme, platform)
            if parsed:
                results.append(parsed)
        return results

    logger.warning(f"TikHub: no recognized data format. Keys: {list(inner.keys())}")
    return []


def _aweme_to_result(aweme: dict, platform: str) -> dict | None:
    """Convert an aweme_info object to our standard result format."""
    video_id = aweme.get("aweme_id", "")
    if not video_id:
        return None

    author = aweme.get("author", {})
    stats = aweme.get("statistics", {})
    video_info = aweme.get("video", {})

    # Build username — try multiple fields
    unique_id = author.get("unique_id") or author.get("uniqueId") or ""
    nickname = author.get("nickname", "")
    uid = author.get("uid", "")
    username = unique_id or nickname

    # Build thumbnail URL from cover
    cover = video_info.get("cover", {})
    if isinstance(cover, dict):
        url_list = cover.get("url_list", [])
        thumbnail_url = url_list[0] if url_list else ""
    elif isinstance(cover, str):
        thumbnail_url = cover
    else:
        thumbnail_url = ""

    # Duration — may be in video_info or top-level
    duration = video_info.get("duration", 0) or aweme.get("duration", 0)
    # Some endpoints return duration in milliseconds
    if duration > 10000:
        duration = duration // 1000

    # Build video URL
    if platform == "tiktok":
        if unique_id:
            video_url = f"https://www.tiktok.com/@{unique_id}/video/{video_id}"
        else:
            video_url = f"https://www.tiktok.com/video/{video_id}"
        author_url = f"https://www.tiktok.com/@{unique_id}" if unique_id else ""
    else:  # douyin
        video_url = aweme.get("share_url") or f"https://www.douyin.com/video/{video_id}"
        author_url = f"https://www.douyin.com/user/{uid}" if uid else ""

    return {
        "platform": platform,
        "video_id": video_id,
        "video_url": video_url,
        "embed_url": None,
        "title": (aweme.get("desc") or "")[:200],
        "description": aweme.get("desc") or "",
        "author": f"@{username}" if username else "Unknown",
        "author_url": author_url,
        "thumbnail_url": thumbnail_url,
        "views": stats.get("play_count", 0) or stats.get("playCount", 0),
        "likes": stats.get("digg_count", 0) or stats.get("diggCount", 0),
        "comments": stats.get("comment_count", 0) or stats.get("commentCount", 0),
        "shares": stats.get("share_count", 0) or stats.get("shareCount", 0),
        "duration_seconds": duration,
        "upload_date": _ts_to_datetime(aweme.get("create_time") or aweme.get("createTime")),
    }


async def search_douyin(
    niche: str,
    api_key: str,
    max_results: int = 30,
) -> list[dict]:
    """Search Douyin videos via TikHub API."""
    if not api_key:
        logger.warning("TikHub API key not configured — skipping Douyin scout")
        return []

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{TIKHUB_BASE}/api/v1/douyin/app/v3/fetch_video_search_result",
                params={"keyword": niche, "count": min(max_results, 30)},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        # Reuse the same parser — Douyin uses identical aweme format
        results = _parse_tiktok_app_v3_as(data, "douyin")

        # AI fallback: if rule-based parsing found nothing but API returned data
        if not results and data.get("data"):
            results = await _ai_parse_fallback(data, "douyin", niche)

        return results

    except httpx.HTTPStatusError as e:
        logger.error(f"TikHub Douyin API error: {e.response.status_code} — {e.response.text[:200]}")
        return []
    except Exception as e:
        logger.error(f"TikHub Douyin error: {e}")
        return []


async def _ai_parse_fallback(data: dict, platform: str, keyword: str) -> list[dict]:
    """
    AI fallback: when rule-based parsing returns 0 results but the API returned data,
    ask AI to extract video info from the raw response. This handles future API format
    changes without requiring code updates.
    """
    import json as _json

    inner = data.get("data", {})
    if not isinstance(inner, dict):
        return []

    # Only attempt if there's meaningful data (not just metadata)
    list_keys = [k for k, v in inner.items() if isinstance(v, list) and v]
    if not list_keys:
        return []

    logger.warning(f"TikHub rule-based parser found 0 results. Trying AI fallback. Data keys with lists: {list_keys}")

    try:
        from backend.core.ai_retry import ai_parse_api_response

        # Send a truncated version of the raw response
        raw_sample = _json.dumps(inner, ensure_ascii=False, default=str)
        ai_results = await ai_parse_api_response(raw_sample, platform, keyword)

        if not ai_results:
            return []

        # Convert AI output to our standard format using _aweme_to_result
        results = []
        for aweme in ai_results:
            parsed = _aweme_to_result(aweme, platform)
            if parsed:
                results.append(parsed)

        if results:
            logger.info(f"AI fallback extracted {len(results)} {platform} videos")
        return results

    except Exception as e:
        logger.debug(f"AI parse fallback failed (non-critical): {e}")
        return []


def _ts_to_datetime(ts):
    """Convert Unix timestamp to datetime."""
    from datetime import datetime
    if not ts:
        return None
    try:
        return datetime.utcfromtimestamp(int(ts))
    except Exception:
        return None
