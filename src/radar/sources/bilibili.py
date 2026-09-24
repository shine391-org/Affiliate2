"""
sources/bilibili.py — Bilibili adapter for Radar Ingestion (Phase 2.1)

Strategy: REUSE WITH ADAPTER
  - Call Bilibili search API directly via httpx (no browser, no auth needed).
  - Phase 1 confirmed: Bilibili public search returns full metadata without login.
  - The old browser/subprocess donor path is retired; this source is direct HTTP only
    and blocks on QR code even headless.

API endpoint: https://api.bilibili.com/x/web-interface/wbi/search/type
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from ..models import MetricSnapshot, NormalizedContent, SourceRunResult
from ..normalize import normalize_bilibili

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

BILIBILI_SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"

# Public headers matching a normal browser request
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


async def _fetch_search_page(
    client: httpx.AsyncClient,
    keyword: str,
    page: int,
    page_size: int = 20,
) -> list[dict]:
    """Fetch one page of Bilibili search results. Returns list of video dicts."""
    params = {
        "search_type": "video",
        "keyword": keyword,
        "page": page,
        "page_size": page_size,
        "order": "",
        "pubtime_begin_s": 0,
        "pubtime_end_s": 0,
    }
    try:
        resp = await client.get(BILIBILI_SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return []
        result = data.get("data", {}).get("result", [])
        return result if isinstance(result, list) else []
    except Exception:
        return []


async def _scan_async(keyword: str, max_count: int) -> SourceRunResult:
    """Async implementation of Bilibili search scan."""
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_items: list[dict] = []

    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True) as client:
        page = 1
        page_size = 20
        while len(all_items) < max_count:
            items = await _fetch_search_page(client, keyword, page, page_size)
            if not items:
                break
            all_items.extend(items)
            page += 1
            if len(items) < page_size:
                break
            await asyncio.sleep(0.5)   # polite rate limit

    raw_count = len(all_items)
    if raw_count == 0:
        return SourceRunResult(
            platform="bilibili",
            keyword=keyword,
            status="EMPTY",
            records_raw=0,
            records_normalized=0,
        )

    contents: list[NormalizedContent] = []
    snapshots: list[MetricSnapshot] = []
    seen: set[str] = set()

    for raw in all_items[:max_count]:
        # Remap Bilibili search API fields to the legacy Radar normalization shape
        normalized_raw = {
            "video_id": str(raw.get("aid", raw.get("id", ""))),
            "video_url": raw.get("arcurl") or raw.get("url") or f"https://www.bilibili.com/video/av{raw.get('aid', '')}",
            "user_id": str(raw.get("mid", raw.get("uid", ""))),
            "nickname": raw.get("author") or raw.get("uname"),
            "create_time": raw.get("pubdate") or raw.get("senddate"),
            "title": raw.get("title", "").replace("<em class=\"keyword\">", "").replace("</em>", ""),
            "desc": raw.get("description") or raw.get("desc", ""),
            "video_play_count": str(raw.get("play", raw.get("video_review", ""))),
            "liked_count": str(raw.get("like", "")),
            "video_danmaku": str(raw.get("danmaku", raw.get("video_danmaku", ""))),
            "collect_count": str(raw.get("favorites", "")),
            "share_count": None,
            "source_keyword": keyword,
        }

        c, s = normalize_bilibili(normalized_raw, captured_at)
        if not c.content_id or c.content_id in seen:
            continue
        seen.add(c.content_id)
        contents.append(c)
        snapshots.append(s)

    return SourceRunResult(
        platform="bilibili",
        keyword=keyword,
        status="SUCCESS",
        records_raw=raw_count,
        records_normalized=len(contents),
        contents=contents,
        snapshots=snapshots,
    )


def scan(keyword: str, max_count: int = 15, timeout: int = 60) -> SourceRunResult:
    """
    Scan Bilibili for a keyword using public search API.
    No browser, no login required.

    Args:
        keyword:   Search keyword
        max_count: Maximum videos to return
        timeout:   Total request timeout in seconds (unused, kept for API compat)

    Returns:
        SourceRunResult with normalized content and metric snapshots
    """
    try:
        return asyncio.run(_scan_async(keyword, max_count))
    except Exception as exc:
        return SourceRunResult(
            platform="bilibili",
            keyword=keyword,
            status="ERROR",
            error=str(exc),
        )
