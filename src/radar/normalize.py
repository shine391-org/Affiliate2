"""
normalize.py — Per-platform normalization functions (Phase 2.1)

Each normalizer takes a legacy/raw source record and returns
(NormalizedContent, MetricSnapshot). Canonical app-native providers may already
emit normalized Scout rows and bypass these legacy mapping helpers.

NULL policy: missing / unavailable metrics → None, NOT 0.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .models import MetricSnapshot, NormalizedContent


def _opt_int(value) -> Optional[int]:
    """Coerce to int; return None if falsy or non-numeric."""
    if value is None or value == "" or value == "None":
        return None
    try:
        v = int(value)
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Douyin
# ---------------------------------------------------------------------------

def normalize_douyin(raw: dict, captured_at: str) -> tuple[NormalizedContent, MetricSnapshot]:
    """
    Map a legacy Douyin source record → normalized schema.

    Key field mappings:
        aweme_id        → content_id
        aweme_url       → canonical_url
        creator_hash    → creator_id   (legacy source identifier)
        nickname        → creator_name
        create_time     → publish_time (Unix ts)
        title / desc    → title / description
        liked_count     → likes
        comment_count   → comments
        share_count     → shares
        collected_count → favorites
        views           → NULL  (Douyin search API does not return play count)
    """
    content_id = str(raw.get("aweme_id", ""))
    content = NormalizedContent(
        platform="douyin",
        content_id=content_id,
        canonical_url=raw.get("aweme_url") or f"https://www.douyin.com/video/{content_id}",
        creator_id=raw.get("creator_hash") or raw.get("uid"),
        creator_name=raw.get("nickname"),
        title=raw.get("title") or raw.get("desc"),
        description=raw.get("desc"),
        publish_time=_opt_int(raw.get("create_time")),
        source_keyword=raw.get("source_keyword"),
    )

    snapshot = MetricSnapshot(
        platform="douyin",
        content_id=content_id,
        captured_at=captured_at,
        views=None,                          # search API never returns play count
        likes=_opt_int(raw.get("liked_count")),
        comments=_opt_int(raw.get("comment_count")),
        shares=_opt_int(raw.get("share_count")),
        favorites=_opt_int(raw.get("collected_count")),
    )
    return content, snapshot


# ---------------------------------------------------------------------------
# Bilibili
# ---------------------------------------------------------------------------

def normalize_bilibili(raw: dict, captured_at: str) -> tuple[NormalizedContent, MetricSnapshot]:
    """
    Map MediaCrawler Bilibili JSONL record → normalized schema.

    Key field mappings:
        video_id        → content_id
        video_url       → canonical_url
        user_id / uid   → creator_id
        nickname        → creator_name
        create_time     → publish_time (Unix ts)
        title / desc    → title / description
        video_play_count → views
        liked_count     → likes
        video_danmaku   → comments (Bilibili uses danmaku as primary comment metric)
        share_count     → shares   (may be absent)
        collect_count   → favorites
    """
    video_id = str(raw.get("video_id", raw.get("aid", "")))
    url = raw.get("video_url") or f"https://www.bilibili.com/video/av{video_id}"

    content = NormalizedContent(
        platform="bilibili",
        content_id=video_id,
        canonical_url=url,
        creator_id=str(raw.get("user_id") or raw.get("uid") or raw.get("creator_hash") or ""),
        creator_name=raw.get("nickname"),
        title=raw.get("title"),
        description=raw.get("desc") or raw.get("description"),
        publish_time=_opt_int(raw.get("create_time")),
        source_keyword=raw.get("source_keyword"),
    )

    snapshot = MetricSnapshot(
        platform="bilibili",
        content_id=video_id,
        captured_at=captured_at,
        views=_opt_int(raw.get("video_play_count")),
        likes=_opt_int(raw.get("liked_count")),
        comments=_opt_int(raw.get("video_danmaku")),   # danmaku = primary engagement
        shares=_opt_int(raw.get("share_count")),
        favorites=_opt_int(raw.get("collect_count")),
    )
    return content, snapshot
