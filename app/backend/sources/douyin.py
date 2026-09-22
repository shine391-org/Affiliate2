"""App-native Douyin discovery through the user's existing CDP browser session.

This replaces the legacy "launch MediaCrawler subprocess -> write JSONL -> reread
JSONL" integration. It connects to an already-running Chrome/Edge DevTools
endpoint, reuses the authenticated Douyin browser context, and calls Douyin's
web search endpoint directly.

No QR flow is implemented here: missing/expired login is returned explicitly as
AUTH_REQUIRED so the user can refresh the real browser session.
"""
from __future__ import annotations

import asyncio
import os
import random
import urllib.parse
from datetime import datetime
from typing import Any

from playwright.async_api import async_playwright

from .base import ProviderStatus, SourceProviderResult

DOUYIN_HOST = "https://www.douyin.com"
DOUYIN_SEARCH_PATH = "/aweme/v1/web/general/search/single/"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"

_ACCESS_HTTP = {401, 403, 412, 429}
_ACCESS_API_STATUS = {-1, 8, 2483, 10000}
DOUYIN_REQUEST_COUNT = 15
DOUYIN_PAGE_STEP = 10


def _first_url(value: Any) -> str:
    if isinstance(value, dict):
        urls = value.get("url_list")
        if isinstance(urls, list) and urls:
            return str(urls[0] or "")
    return ""


def _metric(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _optional_metric(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def _duration_seconds(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        duration = int(value)
    except (TypeError, ValueError):
        return None
    # Douyin aweme.video.duration is milliseconds, including exactly 1000ms.
    return max(round(duration / 1000), 0)


def _random_webid() -> str:
    # Matches the shape used by the donor integration without importing/running it.
    return "".join(str(random.randint(0, 9)) for _ in range(19))


class DouyinProvider:
    platform = "douyin"
    name = "douyin-cdp-web-search"

    def __init__(self, cdp_url: str | None = None):
        self.cdp_url = (
            cdp_url
            or os.getenv("AFFILIATE2_DOUYIN_CDP_URL")
            or os.getenv("DOUYIN_CDP_URL")
            or DEFAULT_CDP_URL
        )

    @staticmethod
    async def _pick_context_and_page(browser):
        contexts = browser.contexts
        if not contexts:
            raise PermissionError(
                "CDP browser has no reusable context. Start Edge/Chrome with "
                "--remote-debugging-port=9222 and log in to Douyin."
            )

        # A CDP browser can expose several reusable contexts. Do not stop at
        # the first Douyin tab: it may be logged out while a later context is
        # authenticated. Check every existing Douyin page first and prefer a
        # context whose session is actually valid.
        first_douyin_page = None
        contexts_without_douyin_page = []
        for context in contexts:
            douyin_pages = [
                page for page in context.pages
                if "douyin.com" in (page.url or "")
            ]
            if not douyin_pages:
                contexts_without_douyin_page.append(context)
                continue
            if first_douyin_page is None:
                first_douyin_page = (context, douyin_pages[0])
            for page in douyin_pages:
                try:
                    logged_in, _, _ = await DouyinProvider._session_state(context, page)
                except Exception:
                    logged_in = False
                if logged_in:
                    return context, page, False

        # A context can still carry valid Douyin cookies/local storage without
        # currently having a Douyin tab open. Probe those reusable contexts
        # before concluding AUTH_REQUIRED. This opens pages only inside the
        # already-running browser; it never launches a new browser/login flow.
        for context in contexts_without_douyin_page:
            page = None
            selected = False
            try:
                page = await context.new_page()
                await page.goto(DOUYIN_HOST, wait_until="domcontentloaded", timeout=60_000)
                logged_in, _, _ = await DouyinProvider._session_state(context, page)
                if logged_in:
                    selected = True
                    return context, page, True
            except Exception:
                # A closed/inaccessible reusable context must not abort scanning
                # the remaining CDP contexts.
                pass
            finally:
                if page is not None and not selected:
                    try:
                        await page.close()
                    except Exception:
                        pass

        # Preserve the existing caller contract for a visible logged-out
        # Douyin page. search() will perform the final session check and return
        # AUTH_REQUIRED, but only after all reusable contexts were examined.
        if first_douyin_page is not None:
            context, page = first_douyin_page
            return context, page, False

        raise PermissionError(
            "No authenticated Douyin session found in any reusable CDP context."
        )

    @staticmethod
    async def _session_state(context, page) -> tuple[bool, dict[str, str], str | None]:
        try:
            local_storage = await page.evaluate("() => ({...window.localStorage})")
        except Exception:
            local_storage = {}

        cookies = await context.cookies(
            [DOUYIN_HOST, "https://douyin.com", "https://creator.douyin.com"]
        )
        cookie_dict = {
            str(cookie.get("name")): str(cookie.get("value") or "")
            for cookie in cookies
            if cookie.get("name")
        }

        logged_in = (
            str(local_storage.get("HasUserLogin", "")) == "1"
            or cookie_dict.get("LOGIN_STATUS") == "1"
        )
        return logged_in, cookie_dict, local_storage.get("xmst")

    @staticmethod
    def _common_params(ms_token: str | None) -> dict[str, str]:
        params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "version_code": "190600",
            "version_name": "19.6.0",
            "update_version_code": "170400",
            "pc_client_type": "1",
            "cookie_enabled": "true",
            "browser_language": "zh-CN",
            "browser_platform": "MacIntel",
            "browser_name": "Chrome",
            "browser_version": "125.0.0.0",
            "browser_online": "true",
            "engine_name": "Blink",
            "os_name": "Mac OS",
            "os_version": "10.15.7",
            "cpu_core_num": "8",
            "device_memory": "8",
            "engine_version": "109.0",
            "platform": "PC",
            "screen_width": "2560",
            "screen_height": "1440",
            "effective_type": "4g",
            "round_trip_time": "50",
            "webid": _random_webid(),
        }
        if ms_token:
            params["msToken"] = str(ms_token)
        return params

    async def _fetch_search_page(
        self,
        context,
        page,
        cookie_dict: dict[str, str],
        ms_token: str | None,
        query: str,
        offset: int,
        search_id: str,
        count: int,
    ) -> dict:
        params = {
            "search_channel": "aweme_general",
            "enable_history": "1",
            "keyword": query,
            "search_source": "tab_search",
            "query_correct_type": "1",
            "is_filter_search": "0",
            "from_group_id": "7378810571505847586",
            "offset": str(offset),
            "count": str(count),
            "need_filter_settings": "1",
            "list_type": "multi",
            "search_id": search_id,
            **self._common_params(ms_token),
        }

        url = f"{DOUYIN_HOST}{DOUYIN_SEARCH_PATH}?{urllib.parse.urlencode(params)}"
        user_agent = await page.evaluate("() => navigator.userAgent")
        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": user_agent,
            "Referer": f"{DOUYIN_HOST}/search/{urllib.parse.quote(query)}?type=general",
            "Origin": DOUYIN_HOST,
            # Current Douyin edge gateway requires the header's presence on
            # this web path. If server-side validation changes, we return an
            # explicit access error instead of silently switching platforms.
            "x-tt-argus": "1",
        }
        uifid = cookie_dict.get("UIFID") or cookie_dict.get("UIFID_TEMP")
        if uifid:
            headers["uifid"] = uifid

        response = await context.request.get(url, headers=headers, timeout=45_000)
        status = response.status
        text = await response.text()

        if status in _ACCESS_HTTP:
            raise PermissionError(f"Douyin HTTP {status}: access/session restriction")
        if "Blocked by ArgusSecurityPlugin" in text or text.strip().lower() == "blocked":
            raise PermissionError(f"Douyin access blocked: {text[:180]}")
        if status < 200 or status >= 300:
            raise RuntimeError(f"Douyin HTTP {status}: {text[:180]}")

        try:
            payload = await response.json()
        except Exception as exc:
            raise RuntimeError(f"Douyin returned non-JSON response: {text[:180]}") from exc

        status_code = payload.get("status_code")
        if status_code not in (None, 0):
            message = payload.get("status_msg") or payload.get("message") or "Douyin API error"
            if status_code in _ACCESS_API_STATUS:
                raise PermissionError(f"Douyin API status {status_code}: {message}")
            raise RuntimeError(f"Douyin API status {status_code}: {message}")
        if "data" not in payload:
            raise RuntimeError("Douyin search response missing data")
        return payload

    def _normalize_aweme(self, aweme: dict, query: str) -> dict | None:
        aweme_id = str(aweme.get("aweme_id") or "").strip()
        if not aweme_id:
            return None

        author = aweme.get("author") or {}
        stats = aweme.get("statistics") or {}
        video = aweme.get("video") or {}
        creator_id = str(
            author.get("sec_uid") or author.get("uid") or author.get("short_id") or ""
        ).strip()
        nickname = str(author.get("nickname") or "").strip()
        create_time = aweme.get("create_time")

        upload_date = None
        try:
            if create_time not in (None, ""):
                upload_date = datetime.utcfromtimestamp(int(create_time))
        except (TypeError, ValueError, OSError):
            upload_date = None

        play_count = _optional_metric(stats.get("play_count"))
        likes = _optional_metric(stats.get("digg_count"))
        comments = _optional_metric(stats.get("comment_count"))
        shares = _optional_metric(stats.get("share_count"))
        favorites = _optional_metric(
            stats.get("collect_count")
            if "collect_count" in stats
            else stats.get("collect_count_v2")
        )

        return {
            "platform": self.platform,
            "requested_platform": self.platform,
            "video_id": aweme_id,
            "video_url": f"{DOUYIN_HOST}/video/{aweme_id}",
            "embed_url": None,
            "title": str(aweme.get("desc") or "")[:200],
            "description": str(aweme.get("desc") or "")[:500],
            "author": nickname,
            "author_url": f"{DOUYIN_HOST}/user/{creator_id}" if creator_id else "",
            "creator_id": creator_id or None,
            "thumbnail_url": (
                _first_url(video.get("cover"))
                or _first_url(video.get("origin_cover"))
                or _first_url(video.get("dynamic_cover"))
            ),
            "views": play_count or 0,
            "likes": likes or 0,
            "comments": comments or 0,
            "shares": shares or 0,
            "favorites": favorites or 0,
            "duration_seconds": _duration_seconds(video.get("duration")),
            "upload_date": upload_date,
            "source_keyword": query,
            # Preserve unknown-vs-zero semantics for Radar even though the
            # existing Scout DB/card schema uses integer defaults.
            "radar_metrics": {
                "views": play_count,
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "favorites": favorites,
            },
        }

    async def search(self, query: str, limit: int = 15) -> SourceProviderResult:
        query = str(query or "").strip()
        if not query or limit <= 0:
            return SourceProviderResult(
                platform=self.platform,
                query=query,
                status=ProviderStatus.EMPTY,
                provider=self.name,
            )

        created_page = None
        try:
            async with async_playwright() as playwright:
                try:
                    browser = await playwright.chromium.connect_over_cdp(
                        self.cdp_url,
                        timeout=15_000,
                    )
                except Exception as exc:
                    return SourceProviderResult(
                        platform=self.platform,
                        query=query,
                        status=ProviderStatus.AUTH_REQUIRED,
                        error=(
                            f"Cannot connect to Douyin CDP at {self.cdp_url}: {exc}. "
                            "Start/login the configured Edge/Chrome profile first."
                        ),
                        provider=self.name,
                    )

                context, page, page_was_created = await self._pick_context_and_page(browser)
                created_page = page if page_was_created else None
                logged_in, cookie_dict, ms_token = await self._session_state(context, page)
                if not logged_in:
                    if created_page:
                        await created_page.close()
                    return SourceProviderResult(
                        platform=self.platform,
                        query=query,
                        status=ProviderStatus.AUTH_REQUIRED,
                        error="Douyin browser session is not logged in.",
                        provider=self.name,
                    )

                raw_awemes: list[dict] = []
                seen: set[str] = set()
                search_id = ""
                offset = 0
                count = DOUYIN_REQUEST_COUNT

                while len(raw_awemes) < limit:
                    payload = await self._fetch_search_page(
                        context,
                        page,
                        cookie_dict,
                        ms_token,
                        query,
                        offset,
                        search_id,
                        count,
                    )
                    data = payload.get("data")
                    if not isinstance(data, list) or not data:
                        break

                    added_this_page = 0
                    for entry in data:
                        if not isinstance(entry, dict):
                            continue
                        aweme = entry.get("aweme_info")
                        if not aweme:
                            mix_items = (entry.get("aweme_mix_info") or {}).get("mix_items") or []
                            aweme = mix_items[0] if mix_items else None
                        if not isinstance(aweme, dict):
                            continue
                        aweme_id = str(aweme.get("aweme_id") or "")
                        if not aweme_id or aweme_id in seen:
                            continue
                        seen.add(aweme_id)
                        raw_awemes.append(aweme)
                        added_this_page += 1
                        if len(raw_awemes) >= limit:
                            break

                    search_id = str((payload.get("extra") or {}).get("logid") or search_id)
                    offset += DOUYIN_PAGE_STEP
                    if added_this_page == 0 or len(raw_awemes) >= limit:
                        break
                    await asyncio.sleep(0.4)

                if created_page:
                    await created_page.close()

        except PermissionError as exc:
            try:
                if created_page:
                    await created_page.close()
            except Exception:
                pass
            return SourceProviderResult(
                platform=self.platform,
                query=query,
                status=ProviderStatus.AUTH_REQUIRED,
                error=str(exc),
                provider=self.name,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                if created_page:
                    await created_page.close()
            except Exception:
                pass
            return SourceProviderResult(
                platform=self.platform,
                query=query,
                status=ProviderStatus.ERROR,
                error=f"{type(exc).__name__}: {exc}",
                provider=self.name,
            )

        items: list[dict] = []
        for aweme in raw_awemes:
            row = self._normalize_aweme(aweme, query)
            if row:
                items.append(row)

        return SourceProviderResult(
            platform=self.platform,
            query=query,
            status=ProviderStatus.SUCCESS if items else ProviderStatus.EMPTY,
            items=items,
            provider=self.name,
        )
