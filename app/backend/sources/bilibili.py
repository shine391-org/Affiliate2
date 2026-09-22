"""Real Bilibili keyword discovery for the canonical Affiliate2 app.

This is intentionally small: platform HTTP access + normalization only.
Scout owns orchestration/persistence; Radar owns repeated observations/ranking.
"""
from __future__ import annotations

import asyncio
import html
import re
import time
import urllib.parse
from datetime import datetime
from hashlib import md5
from typing import Any

import httpx

from .base import ProviderStatus, SourceProviderResult

BILIBILI_SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"
BILIBILI_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
BILIBILI_CREATOR_URL = "https://api.bilibili.com/x/space/wbi/arc/search"

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

_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    return _TAG_RE.sub("", text).strip()


def _optional_metric_int(value: Any) -> int | None:
    if value is None or value == "" or value == "--":
        return None
    try:
        if isinstance(value, str):
            text = value.strip().lower().replace(",", "")
            multipliers = {"万": 10_000, "亿": 100_000_000, "k": 1_000, "m": 1_000_000}
            for suffix, multiplier in multipliers.items():
                if text.endswith(suffix):
                    return max(int(float(text[:-len(suffix)]) * multiplier), 0)
            return max(int(float(text)), 0)
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


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


def _first_present(raw: dict, *keys: str) -> Any:
    """Return the first explicitly present, non-None field without treating 0 as missing."""
    for key in keys:
        if key in raw and raw[key] is not None:
            return raw[key]
    return None


def _extract_wbi_key(url: str) -> str:
    return str(url or "").rsplit("/", 1)[-1].split(".", 1)[0]


def _wbi_sign(params: dict, img_key: str, sub_key: str) -> dict:
    mixin = img_key + sub_key
    salt = "".join(mixin[i] for i in _MIXIN_KEY_ENC_TAB)[:32]
    signed = dict(params)
    signed["wts"] = int(time.time())
    signed = dict(sorted(signed.items()))
    signed = {
        key: "".join(ch for ch in str(value) if ch not in "!'()*")
        for key, value in signed.items()
    }
    query = urllib.parse.urlencode(signed)
    signed["w_rid"] = md5((query + salt).encode()).hexdigest()
    return signed


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

        radar_views = _optional_metric_int(_first_present(raw, "play", "video_review"))
        radar_likes = _optional_metric_int(raw.get("like"))
        radar_comments = _optional_metric_int(_first_present(raw, "danmaku", "video_danmaku"))
        radar_shares = _optional_metric_int(_first_present(raw, "share", "share_count"))
        radar_favorites = _optional_metric_int(_first_present(raw, "favorites", "collect_count"))

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
            "views": radar_views or 0,
            "likes": radar_likes or 0,
            "comments": radar_comments or 0,
            "shares": radar_shares or 0,
            "favorites": radar_favorites or 0,
            "radar_metrics": {
                "views": radar_views,
                "likes": radar_likes,
                "comments": radar_comments,
                "shares": radar_shares,
                "favorites": radar_favorites,
            },
            "duration_seconds": _duration_seconds(raw.get("duration")),
            "upload_date": upload_date,
            "source_keyword": query,
        }

    def _normalize_creator_item(
        self,
        raw: dict,
        creator_id: str,
        creator_name: str,
    ) -> dict | None:
        aid = raw.get("aid")
        bvid = str(raw.get("bvid") or "").strip()
        video_id = str(aid or bvid or "").strip()
        if not video_id:
            return None

        mid = str(raw.get("mid") or creator_id or "").strip()
        publish_ts = raw.get("created")
        upload_date = None
        try:
            if publish_ts not in (None, ""):
                upload_date = datetime.utcfromtimestamp(int(publish_ts))
        except (TypeError, ValueError, OSError):
            upload_date = None

        video_url = (
            f"https://www.bilibili.com/video/{bvid}"
            if bvid
            else f"https://www.bilibili.com/video/av{video_id}"
        )
        radar_views = _optional_metric_int(raw.get("play"))
        radar_comments = _optional_metric_int(raw.get("video_review"))

        return {
            "platform": self.platform,
            "requested_platform": self.platform,
            "video_id": video_id,
            "video_url": video_url,
            "embed_url": None,
            "title": _clean_text(raw.get("title")),
            "description": _clean_text(raw.get("description"))[:500],
            "author": _clean_text(raw.get("author") or creator_name),
            "author_url": f"https://space.bilibili.com/{mid}" if mid else "",
            "creator_id": mid or None,
            "thumbnail_url": _image_url(raw.get("pic")),
            "views": radar_views or 0,
            "likes": 0,
            "comments": radar_comments or 0,
            "shares": 0,
            "favorites": 0,
            "radar_metrics": {
                "views": radar_views,
                "likes": None,
                "comments": radar_comments,
                "shares": None,
                "favorites": None,
            },
            "duration_seconds": _duration_seconds(raw.get("length")),
            "upload_date": upload_date,
            "source_keyword": f"creator:{creator_id}",
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

        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Bilibili search returned an unexpected data envelope")

        voucher = data.get("v_voucher") or payload.get("v_voucher")
        if voucher:
            raise PermissionError("Bilibili search returned a risk-control voucher/challenge")

        if "result" not in data:
            raise RuntimeError("Bilibili search response missing data.result")
        result = data.get("result")
        if not isinstance(result, list):
            raise RuntimeError("Bilibili search data.result is not a list")
        return result

    async def _get_wbi_keys(self, client: httpx.AsyncClient) -> tuple[str, str]:
        response = await client.get(BILIBILI_NAV_URL, timeout=15)
        if response.status_code in (401, 403, 412, 429):
            raise PermissionError(
                f"Bilibili nav HTTP {response.status_code}; session/access may be required"
            )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        wbi = data.get("wbi_img") or {}
        img_key = _extract_wbi_key(wbi.get("img_url"))
        sub_key = _extract_wbi_key(wbi.get("sub_url"))
        if not img_key or not sub_key:
            code = payload.get("code")
            if code in _ACCESS_CODES:
                raise PermissionError(
                    f"Bilibili nav API code {code}: {payload.get('message') or 'access restriction'}"
                )
            raise RuntimeError("Bilibili nav response did not contain WBI keys")
        return img_key, sub_key

    async def _fetch_creator_page(
        self,
        client: httpx.AsyncClient,
        creator_id: str,
        page: int,
        page_size: int,
        wbi_keys: tuple[str, str],
    ) -> list[dict]:
        params = _wbi_sign(
            {
                "mid": creator_id,
                "pn": page,
                "ps": page_size,
                "order": "pubdate",
            },
            wbi_keys[0],
            wbi_keys[1],
        )
        response = await client.get(BILIBILI_CREATOR_URL, params=params, timeout=30)
        if response.status_code in (401, 403, 412, 429):
            raise PermissionError(
                f"Bilibili creator HTTP {response.status_code}; session/access may be required"
            )
        response.raise_for_status()
        payload = response.json()
        code = payload.get("code")
        if code in _ACCESS_CODES:
            raise PermissionError(
                f"Bilibili creator API code {code}: {payload.get('message') or 'access/risk-control restriction'}"
            )
        if code != 0:
            raise RuntimeError(
                f"Bilibili creator API code {code}: {payload.get('message') or 'unknown error'}"
            )
        videos = payload.get("data", {}).get("list", {}).get("vlist", [])
        return videos if isinstance(videos, list) else []

    async def creator_history(
        self,
        creator_id: str,
        *,
        creator_name: str = "",
        limit: int = 30,
    ) -> SourceProviderResult:
        creator_id = str(creator_id or "").strip()
        query = f"creator:{creator_id}"
        if not creator_id or limit <= 0:
            return SourceProviderResult(
                platform=self.platform,
                query=query,
                status=ProviderStatus.EMPTY,
                provider=self.name,
            )

        page_size = min(30, max(1, min(limit, 30)))
        raw_items: list[dict] = []

        try:
            async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True) as client:
                keys = await self._get_wbi_keys(client)
                page = 1
                while len(raw_items) < limit:
                    page_items = await self._fetch_creator_page(
                        client,
                        creator_id,
                        page,
                        page_size,
                        keys,
                    )
                    if not page_items:
                        break
                    raw_items.extend(page_items)
                    if len(page_items) < page_size:
                        break
                    page += 1
                    if len(raw_items) < limit:
                        await asyncio.sleep(0.5)
        except PermissionError as exc:
            return SourceProviderResult(
                platform=self.platform,
                query=query,
                status=ProviderStatus.AUTH_REQUIRED,
                error=str(exc),
                provider=self.name,
            )
        except Exception as exc:  # noqa: BLE001
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
            item = self._normalize_creator_item(raw, creator_id, creator_name)
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
