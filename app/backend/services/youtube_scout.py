# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""YouTube Data API v3 search for trending videos."""
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


async def search_youtube(
    niche: str,
    api_key: str,
    max_results: int = 50,
) -> list[dict]:
    """
    Search YouTube for trending videos in a niche.
    Returns list of raw video dicts with metadata.
    """
    import asyncio
    from googleapiclient.discovery import build

    def _search():
        youtube = build("youtube", "v3", developerKey=api_key)

        # Detect language from the query to set relevanceLanguage
        lang = _detect_language(niche)

        # Search for relevant videos
        search_response = youtube.search().list(
            q=niche,
            part="id,snippet",
            type="video",
            order="viewCount",
            maxResults=min(max_results, 50),
            publishedAfter=_get_recent_date(),
            relevanceLanguage=lang,
        ).execute()

        video_ids = [
            item["id"]["videoId"]
            for item in search_response.get("items", [])
            if item["id"].get("videoId")
        ]

        if not video_ids:
            return []

        # Get detailed stats for each video
        videos_response = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(video_ids),
        ).execute()

        results = []
        for item in videos_response.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            video_id = item["id"]

            results.append({
                "platform": "youtube",
                "video_id": video_id,
                "video_url": f"https://youtube.com/watch?v={video_id}",
                "embed_url": f"https://www.youtube-nocookie.com/embed/{video_id}",
                "title": snippet.get("title", ""),
                "description": snippet.get("description", "")[:500],
                "author": snippet.get("channelTitle", ""),
                "author_url": f"https://youtube.com/channel/{snippet.get('channelId', '')}",
                "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "shares": 0,
                "duration_seconds": _parse_duration(item.get("contentDetails", {}).get("duration", "")),
                "upload_date": _parse_date(snippet.get("publishedAt")),
            })

        return results

    return await asyncio.to_thread(_search)


def _detect_language(text: str) -> str:
    """Detect the primary language of the query text. Returns ISO 639-1 code."""
    import unicodedata
    # Count characters by script
    cjk = 0
    latin = 0
    for ch in text:
        name = unicodedata.name(ch, "")
        if "CJK" in name or "HIRAGANA" in name or "KATAKANA" in name:
            cjk += 1
        elif "LATIN" in name:
            latin += 1
    if cjk > 0:
        # Distinguish Chinese vs Japanese (rough heuristic)
        has_hiragana = any("HIRAGANA" in unicodedata.name(c, "") or "KATAKANA" in unicodedata.name(c, "") for c in text)
        return "ja" if has_hiragana else "zh"
    # Check for Korean (Hangul)
    if any("\uAC00" <= ch <= "\uD7A3" or "\u1100" <= ch <= "\u11FF" for ch in text):
        return "ko"
    return "en"


def _get_recent_date() -> str:
    """Return ISO date string for 30 days ago."""
    from datetime import timedelta
    dt = datetime.utcnow() - timedelta(days=30)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_duration(duration_str: str) -> Optional[int]:
    """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
    import re
    if not duration_str:
        return None
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str)
    if not match:
        return None
    h, m, s = match.groups()
    return int(h or 0) * 3600 + int(m or 0) * 60 + int(s or 0)


def _parse_date(date_str: str) -> Optional[datetime]:
    """Parse YouTube date string to datetime."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None
