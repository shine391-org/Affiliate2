"""Real Bilibili keyword discovery for the canonical Affiliate2 app.

This is intentionally small: platform HTTP access + normalization only.
Scout owns orchestration/persistence; Radar owns repeated observations/ranking.
"""
from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime
from typing import Any

import httpx

from .base import ProviderStatus, SourceProviderResult

BILIBILI_SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"

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

_ACCESS_CODES = {-101, -352, -412}
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    return _TAG_RE.sub("", text).strip()


def _metric_int(value: Any) -> int:
    """Best-effort Bilibili metric coercion for Scout cards.

    Radar's canonical snapshot layer preserves unavailable metrics separately;
    Scout's existing schema expects integers, so unavailable values are 0 here.
    """
    if value is None or value == "" or value == "--":
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return max(int(value), 0)

    text = str(value).strip().lower().replace(",", "")
    multipliers = {"万": 10_000, "亿": 100_000_000, "k": 1_000, "m": 1_000_000}
    for suffix, multiplier in multipliers.items():
        if text.endswith(suffix):
            try:
                return max(int(float(text[:-len(suffix)]) * multiplier), 0)
            except ValueError:
                return 0
    try:
        return max(int(float(text)), 0)
    except ValueError:
        return 0


def _duration_seconds(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    parts = str(value).strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


def _image_url(value: Any) -> str:
    url = str(value or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return url


class BilibiliProvider:
    platform = "bilibili"
    name = "bilibili-public-search"

    def _normalize_item(self, raw: dict, query: str) -> dict | None:
        aid = raw.get("aid") or raw.get("id")
        bvid = str(raw.get("bvid") or "").strip()
        video_id = str(aid or bvid or "").strip()
        if not video_id:
            return None

        if bvid:
            video_url = f"https://www.bilibili.com/video/{bvid}"
        else:
            video_url = raw.get("arcurl") or raw.get("url") or f"https://www.bilibili.com/video/av{video_id}"

        mid = str(raw.get("mid") or raw.get("uid") or "").strip()
        publish_ts = raw.get("pubdate") or raw.get("senddate")
        upload_date = None
        try:
            if publish_ts not in (None, ""):
                upload_date = datetime.utcfromtimestamp(int(publish_ts))
        except (TypeError, ValueError, OSError):
            upload_date = None

        return {
            "platform": self.platform,
            "requested_platform": self.platform,
            "video_id": video_id,
            "video_url": str(video_url),
            "embed_url": None,
            "title": _clean_text(raw.get("title")),
            "description": _clean_text(raw.get("description") or raw.get("desc"))[:500],
            "author": _clean_text(raw.get("author") or raw.get("uname")),
            "author_url": f"https://space.bilibili.com/{mid}" if mid else "",
            "creator_id": mid or None,
            "thumbnail_url": _image_url(raw.get("pic") or raw.get("thumbnail")),
            "views": _metric_int(raw.get("play") or raw.get("video_review")),
            "likes": _metric_int(raw.get("like")),
            "comments": _metric_int(raw.get("danmaku") or raw.get("video_danmaku")),
            "shares": _metric_int(raw.get("share") or raw.get("share_count")),
            "favorites": _metric_int(raw.get("favorites") or raw.get("collect_count")),
            "duration_seconds": _duration_seconds(raw.get("duration")),
            "upload_date": upload_date,
            "source_keyword": query,
        }

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        query: str,
        page: int,
        page_size: int,
    ) -> list[dict]:
        params = {
            "search_type": "video",
            "keyword": query,
            "page": page,
            "page_size": page_size,
            "order": "",
            "pubtime_begin_s": 0,
            "pubtime_end_s": 0,
        }
        response = await client.get(BILIBILI_SEARCH_URL, params=params, timeout=30)

        if response.status_code in (401, 403, 412, 429):
            raise PermissionError(
                f"Bilibili HTTP {response.status_code}; session/access may be required"
            )

        response.raise_for_status()
        payload = response.json()
        code = payload.get("code")
        if code in _ACCESS_CODES:
            raise PermissionError(
                f"Bilibili API code {code}: {payload.get('message') or 'access/risk-control restriction'}"
            )
        if code != 0:
            raise RuntimeError(
                f"Bilibili API code {code}: {payload.get('message') or 'unknown error'}"
            )

        result = payload.get("data", {}).get("result", [])
        return result if isinstance(result, list) else []

    async def search(self, query: str, limit: int = 15) -> SourceProviderResult:
        query = str(query or "").strip()
        if not query or limit <= 0:
            return SourceProviderResult(
                platform=self.platform,
                query=query,
                status=ProviderStatus.EMPTY,
                provider=self.name,
            )

        page_size = min(50, max(1, min(limit, 20)))
        raw_items: list[dict] = []

        try:
            async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True) as client:
                page = 1
                while len(raw_items) < limit:
                    page_items = await self._fetch_page(client, query, page, page_size)
                    if not page_items:
                        break
                    raw_items.extend(page_items)
                    if len(page_items) < page_size:
                        break
                    page += 1
                    if len(raw_items) < limit:
                        await asyncio.sleep(0.35)
        except PermissionError as exc:
            return SourceProviderResult(
                platform=self.platform,
                query=query,
                status=ProviderStatus.AUTH_REQUIRED,
                error=str(exc),
                provider=self.name,
            )
        except Exception as exc:  # noqa: BLE001 — provider converts transport errors to explicit status
            return SourceProviderResult(
                platform=self.platform,
                query=query,
                status=ProviderStatus.ERROR,
                error=f"{type(exc).__name__}: {exc}",
                provider=self.name,
            )

        items: list[dict] = []
        seen: set[str] = set()
        for raw in raw_items:
            item = self._normalize_item(raw, query)
            if not item or item["video_id"] in seen:
                continue
            seen.add(item["video_id"])
            items.append(item)
            if len(items) >= limit:
                break

        return SourceProviderResult(
            platform=self.platform,
            query=query,
            status=ProviderStatus.SUCCESS if items else ProviderStatus.EMPTY,
            items=items,
            provider=self.name,
        )
