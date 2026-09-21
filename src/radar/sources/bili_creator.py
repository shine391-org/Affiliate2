"""
sources/bili_creator.py — Bilibili creator (space) feed adapter (Phase 2.3)

Reuses the wbi signing algorithm from MediaCrawler
(tools/MediaCrawler/media_platform/bilibili/help.py) — reimplemented inline
with stdlib only (md5 + urllib), no MediaCrawler import.

Endpoint: https://api.bilibili.com/x/space/wbi/arc/search
Wbi keys come from https://api.bilibili.com/x/web-interface/nav (public, no login).

Purpose: fetch a creator's video history so creators reach >=5 content
and the creator-baseline signal can activate.
"""
from __future__ import annotations

import asyncio
import re
import time
import urllib.parse
from hashlib import md5
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import httpx

from ..models import MetricSnapshot, NormalizedContent, SourceRunResult
from ..normalize import normalize_bilibili
from .bilibili import _scan_async

PROJECT_ROOT = Path(__file__).parent.parent.parent

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
ARC_SEARCH_URL = "https://api.bilibili.com/x/space/wbi/arc/search"

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

# ---------------------------------------------------------------------------
# wbi signing — ported from MediaCrawler bilibili/help.py (stdlib only)
# ---------------------------------------------------------------------------

_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def _get_mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in _MIXIN_KEY_ENC_TAB)[:32]


def _enc_wbi(params: dict) -> dict:
    params = params.copy()
    params["wts"] = int(time.time())
    params = dict(sorted(params.items()))
    params = {
        k: "".join(filter(lambda chr: chr not in "!'()*", str(v)))
        for k, v in params.items()
    }
    query = urllib.parse.urlencode(params)
    return params


def wbi_sign(params: dict, img_key: str, sub_key: str) -> dict:
    """Sign request params with wbi keys. Returns params + w_rid."""
    mixin_key = _get_mixin_key(img_key + sub_key)
    params = params.copy()
    params["wts"] = int(time.time())
    params = dict(sorted(params.items()))
    params = {
        k: "".join(filter(lambda c: c not in "!'()*", str(v)))
        for k, v in params.items()
    }
    query = urllib.parse.urlencode(params)
    params["w_rid"] = md5((query + mixin_key).encode()).hexdigest()
    return params


def _extract_key(url: str) -> str:
    """Extract key part from a wbi png url, e.g. '7cd084...png' -> '7cd084...'"""
    return url.split("/")[-1].split(".")[0]


# ---------------------------------------------------------------------------
# Async scan
# ---------------------------------------------------------------------------

async def _get_wbi_keys(client: httpx.AsyncClient) -> tuple[str, str] | None:
    """Fetch wbi img/sub keys from public nav endpoint.
    Note: nav returns code=-101 when not logged in, but wbi_img is still present.
    """
    try:
        resp = await client.get(NAV_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # wbi_img is present even when code=-101 (not logged in)
        wbi = data.get("data", {}).get("wbi_img", {})
        img_url = wbi.get("img_url", "")
        sub_url = wbi.get("sub_url", "")
        if not img_url or not sub_url:
            return None
        return _extract_key(img_url), _extract_key(sub_url)
    except Exception:
        return None


async def _fetch_creator_page(
    client: httpx.AsyncClient,
    wbi_keys: tuple[str, str],
    mid: str,
    pn: int,
    ps: int = 30,
) -> list[dict]:
    """Fetch one page of a creator's video list. Returns list of video dicts."""
    params = {"mid": mid, "pn": pn, "ps": ps, "order": "pubdate"}
    signed = wbi_sign(params, wbi_keys[0], wbi_keys[1])
    try:
        resp = await client.get(ARC_SEARCH_URL, params=signed, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return []
        vlist = data.get("data", {}).get("list", {}).get("vlist", [])
        return vlist if isinstance(vlist, list) else []
    except Exception:
        return []


async def _scan_creator_async(mid: str, creator_name: str, max_count: int) -> SourceRunResult:
    """Fetch up to max_count videos from a creator's space feed."""
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_items: list[dict] = []

    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True) as client:
        wbi_keys = await _get_wbi_keys(client)
        if not wbi_keys:
            return SourceRunResult(
                platform="bilibili",
                keyword=f"creator:{mid}",
                status="ERROR",
                error="Failed to fetch wbi keys from nav endpoint",
            )

        pn = 1
        while len(all_items) < max_count:
            items = await _fetch_creator_page(client, wbi_keys, mid, pn)
            if not items:
                break
            all_items.extend(items)
            if len(items) < 30:
                break
            pn += 1
            await asyncio.sleep(1.0)   # polite rate limit

    if not all_items:
        return SourceRunResult(
            platform="bilibili",
            keyword=f"creator:{mid}",
            status="EMPTY",
            records_raw=0,
            records_normalized=0,
        )

    contents: list[NormalizedContent] = []
    snapshots: list[MetricSnapshot] = []
    seen: set[str] = set()

    for raw in all_items[:max_count]:
        # vlist item fields: bvid, aid, title, description, created, play, pic,
        #                    length, author, mid, comment, is_upower_exclusive ...
        # NOTE: vlist does not include `like` count — only play + comment.
        normalized_raw = {
            "video_id": str(raw.get("aid", "")),
            "video_url": f"https://www.bilibili.com/video/av{raw.get('aid', '')}",
            "user_id": str(raw.get("mid", mid)),
            "nickname": raw.get("author") or creator_name,
            "create_time": raw.get("created"),
            "title": raw.get("title", ""),
            "desc": raw.get("description", ""),
            "video_play_count": str(raw.get("play", "")),
            "liked_count": None,          # vlist has no like field
            "video_danmaku": raw.get("video_review", ""),   # video_review = danmaku count in vlist
            "collect_count": None,        # vlist has no favorites field
            "share_count": None,
            "source_keyword": f"creator:{mid}",
        }
        c, s = normalize_bilibili(normalized_raw, captured_at)
        if not c.content_id or c.content_id in seen:
            continue
        seen.add(c.content_id)
        contents.append(c)
        snapshots.append(s)

    return SourceRunResult(
        platform="bilibili",
        keyword=f"creator:{mid}",
        status="SUCCESS",
        records_raw=len(all_items),
        records_normalized=len(contents),
        contents=contents,
        snapshots=snapshots,
    )


def scan_creator(mid: str, creator_name: str = "", max_count: int = 30) -> SourceRunResult:
    """
    Scan a Bilibili creator's video history via public space API.

    Args:
        mid:           Bilibili creator id (numeric string)
        creator_name:  Display name (fallback if API returns none)
        max_count:     Max videos to fetch

    Returns:
        SourceRunResult with normalized content and snapshots.
    """
    try:
        return asyncio.run(_scan_creator_async(mid, creator_name, max_count))
    except Exception as exc:
        return SourceRunResult(
            platform="bilibili",
            keyword=f"creator:{mid}",
            status="ERROR",
            error=str(exc),
        )


def scan_creator_via_search(mid: str, creator_name: str, max_count: int = 30) -> SourceRunResult:
    """
    Fallback: find a creator's videos by searching their display name with the
    proven public search API, then filter to only that creator_id.

    Used when the space API (arc/search) is blocked by risk control (-352).
    Reuses radar.sources.bilibili._scan_async — no new crawler logic.

    Args:
        mid:           Target creator id (only records with this id are kept)
        creator_name:  Creator display name to search for
        max_count:     Max videos to fetch per search

    Returns:
        SourceRunResult with only the target creator's content.
    """
    if not creator_name:
        return SourceRunResult(
            platform="bilibili",
            keyword=f"creator:{mid}",
            status="EMPTY",
            records_raw=0,
            records_normalized=0,
        )

    async def _run():
        result = await _scan_async(creator_name, max_count)
        if result.status != "SUCCESS":
            # Re-tag as creator scan
            result.keyword = f"creator:{mid}"
            return result

        contents = [c for c in result.contents if c.creator_id == str(mid)]
        snap_ids = {c.content_id for c in contents}
        snapshots = [s for s in result.snapshots if s.content_id in snap_ids]
        return SourceRunResult(
            platform="bilibili",
            keyword=f"creator:{mid}",
            status="SUCCESS" if contents else "EMPTY",
            records_raw=result.records_raw,
            records_normalized=len(contents),
            contents=contents,
            snapshots=snapshots,
        )

    try:
        return asyncio.run(_run())
    except Exception as exc:
        return SourceRunResult(
            platform="bilibili",
            keyword=f"creator:{mid}",
            status="ERROR",
            error=str(exc),
        )
