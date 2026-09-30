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
import random
import urllib.parse
from datetime import datetime
from typing import Any

from backend.services.douyin_browser_session import (
    DOUYIN_HOST,
    open_douyin_cdp_session,
    pick_authenticated_context_and_page,
    read_douyin_session_state,
    resolve_douyin_cdp_url,
)

from .base import ProviderStatus, SourceProviderResult

DOUYIN_SEARCH_PATH = "/aweme/v1/web/general/search/single/"
DOUYIN_PROFILE_PATH = "/aweme/v1/web/user/profile/other/"
DOUYIN_CREATOR_POSTS_PATH = "/aweme/v1/web/aweme/post/"

_ACCESS_HTTP = {401, 403, 412, 429}
_ACCESS_API_STATUS = {-1, 8, 2483, 10000}
DOUYIN_REQUEST_COUNT = 15
DOUYIN_PAGE_STEP = 10
DOUYIN_API_TIMEOUT_MS = 15_000


def _first_url(value: Any) -> str:
    if isinstance(value, dict):
        urls = value.get("url_list")
        if isinstance(urls, list) and urls:
            return str(urls[0] or "")
    return ""


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
        self.cdp_url = resolve_douyin_cdp_url(cdp_url)

    @staticmethod
    async def _pick_context_and_page(browser):
        # Compatibility seam for focused provider tests; ownership lives in
        # backend.services.douyin_browser_session.
        return await pick_authenticated_context_and_page(
            browser,
            state_reader=DouyinProvider._session_state,
        )

    @staticmethod
    async def _session_state(context, page) -> tuple[bool, dict[str, str], str | None]:
        return await read_douyin_session_state(context, page)

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

    async def _request_json(
        self,
        context,
        page,
        path: str,
        params: dict[str, str],
        *,
        referer: str,
        cookie_dict: dict[str, str] | None = None,
    ) -> dict:
        """Issue one authenticated Douyin web request through the CDP context."""
        query = urllib.parse.urlencode({
            **params,
            **self._common_params(params.get("msToken")),
        })
        url = f"{DOUYIN_HOST}{path}?{query}"
        user_agent = await page.evaluate("() => navigator.userAgent")
        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": user_agent,
            "Referer": referer,
            "Origin": DOUYIN_HOST,
        }
        cookies = cookie_dict or {}
        uifid = cookies.get("UIFID") or cookies.get("UIFID_TEMP")
        if uifid:
            headers["uifid"] = uifid
        response = await context.request.get(
            url, headers=headers, timeout=DOUYIN_API_TIMEOUT_MS
        )
        status = response.status
        text = await response.text()

        if status in _ACCESS_HTTP:
            raise PermissionError(
                f"Douyin HTTP {status}: access/session restriction"
            )
        if "Blocked by ArgusSecurityPlugin" in text or text.strip().lower() == "blocked":
            raise PermissionError(f"Douyin access blocked: {text[:180]}")
        if status < 200 or status >= 300:
            raise RuntimeError(f"Douyin HTTP {status}: {text[:180]}")

        try:
            payload = await response.json()
        except Exception as exc:
            raise RuntimeError(
                f"Douyin returned non-JSON response: {text[:180]}"
            ) from exc

        status_code = payload.get("status_code")
        if status_code not in (None, 0):
            message = (
                payload.get("status_msg")
                or payload.get("message")
                or "Douyin API error"
            )
            if status_code in _ACCESS_API_STATUS:
                raise PermissionError(
                    f"Douyin API status {status_code}: {message}"
                )
            raise RuntimeError(
                f"Douyin API status {status_code}: {message}"
            )
        return payload

    async def _fetch_creator_profile(
        self,
        context,
        page,
        cookie_dict: dict[str, str],
        ms_token: str | None,
        creator_id: str,
    ) -> dict:
        params = {
            "sec_user_id": creator_id,
            "publish_video_strategy_type": "2",
            "personal_center_strategy": "1",
        }
        if ms_token:
            params["msToken"] = str(ms_token)
        return await self._request_json(
            context,
            page,
            DOUYIN_PROFILE_PATH,
            params,
            referer=f"{DOUYIN_HOST}/user/{creator_id}",
            cookie_dict=cookie_dict,
        )

    async def _fetch_creator_posts(
        self,
        context,
        page,
        cookie_dict: dict[str, str],
        ms_token: str | None,
        creator_id: str,
        max_cursor: str,
        count: int,
    ) -> dict:
        params = {
            "sec_user_id": creator_id,
            "max_cursor": str(max_cursor or "0"),
            "count": str(count),
            "locate_query": "false",
            "publish_video_strategy_type": "2",
        }
        if ms_token:
            params["msToken"] = str(ms_token)
        return await self._request_json(
            context,
            page,
            DOUYIN_CREATOR_POSTS_PATH,
            params,
            referer=f"{DOUYIN_HOST}/user/{creator_id}",
            cookie_dict=cookie_dict,
        )

    @staticmethod
    def creator_id_from_url(profile_url: str) -> str:
        value = str(profile_url or "").strip()
        try:
            parsed = urllib.parse.urlparse(value)
        except Exception:
            return ""
        if not parsed.hostname or "douyin.com" not in parsed.hostname.lower():
            return ""
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "user":
            return urllib.parse.unquote(parts[1]).strip()
        return ""

    @staticmethod
    def _creator_info_from_payload(payload: dict, creator_id: str) -> dict:
        user = (
            payload.get("user")
            or (payload.get("user_info") or {}).get("user")
            or payload.get("user_info")
            or {}
        )
        stats = (
            payload.get("stats")
            or (payload.get("user_info") or {}).get("stats")
            or {}
        )
        if not isinstance(user, dict):
            user = {}
        if not isinstance(stats, dict):
            stats = {}

        return {
            "creator_id": creator_id,
            "display_name": str(
                user.get("nickname")
                or user.get("unique_id")
                or user.get("short_id")
                or creator_id
            ),
            "avatar_url": (
                _first_url(user.get("avatar_larger"))
                or _first_url(user.get("avatar_medium"))
                or _first_url(user.get("avatar_thumb"))
            ),
            "follower_count": _optional_metric(
                stats.get("follower_count")
                if "follower_count" in stats
                else stats.get("followerCount")
            ),
            "video_count": _optional_metric(
                stats.get("aweme_count")
                if "aweme_count" in stats
                else stats.get("videoCount")
            ),
        }

    async def creator_info(self, creator_id: str) -> dict:
        """Fetch one Douyin creator profile through the authenticated CDP session."""
        creator_id = str(creator_id or "").strip()
        if not creator_id:
            raise ValueError("Douyin creator_id is required")

        async with open_douyin_cdp_session(self.cdp_url) as session:
            payload = await self._fetch_creator_profile(
                session.context,
                session.page,
                session.cookie_dict,
                session.ms_token,
                creator_id,
            )
        return self._creator_info_from_payload(payload, creator_id)

    async def creator_history(
        self,
        creator_id: str,
        *,
        creator_name: str = "",
        limit: int = 30,
    ) -> SourceProviderResult:
        """Fetch recent videos for one Douyin creator, never TikTok aliases."""
        creator_id = str(creator_id or "").strip()
        query = f"creator:{creator_id}"
        if not creator_id or limit <= 0:
            return SourceProviderResult(
                platform=self.platform,
                query=query,
                status=ProviderStatus.EMPTY,
                provider=self.name,
            )

        raw_awemes: list[dict] = []
        try:
            async with open_douyin_cdp_session(self.cdp_url) as session:
                cursor_value = "0"
                seen_cursor: set[str] = set()

                while len(raw_awemes) < limit:
                    # Never over-fetch the last native page. Advancing the
                    # cursor after 18 rows while returning only the remaining
                    # 14 would permanently skip four videos on the next UI page.
                    request_count = max(1, min(18, limit - len(raw_awemes)))
                    payload = await self._fetch_creator_posts(
                        session.context,
                        session.page,
                        session.cookie_dict,
                        session.ms_token,
                        creator_id,
                        cursor_value,
                        request_count,
                    )
                    page_items = payload.get("aweme_list") or []
                    if not isinstance(page_items, list) or not page_items:
                        break
                    raw_awemes.extend(
                        item for item in page_items if isinstance(item, dict)
                    )
                    has_more = bool(payload.get("has_more"))
                    next_cursor = str(payload.get("max_cursor") or "") or None
                    if not has_more:
                        next_cursor = None
                        break
                    if not next_cursor or next_cursor in seen_cursor:
                        has_more = False
                        next_cursor = None
                        break
                    seen_cursor.add(next_cursor)
                    if len(raw_awemes) >= limit:
                        break
                    cursor_value = next_cursor
                    await asyncio.sleep(0.4)

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
        for aweme in raw_awemes:
            row = self._normalize_aweme(aweme, query)
            if not row or row["video_id"] in seen:
                continue
            seen.add(row["video_id"])
            row["creator_id"] = creator_id
            row["author_url"] = f"{DOUYIN_HOST}/user/{creator_id}"
            if creator_name and not row.get("author"):
                row["author"] = creator_name
            items.append(row)
            if len(items) >= limit:
                break

        return SourceProviderResult(
            platform=self.platform,
            query=query,
            status=ProviderStatus.SUCCESS if items else ProviderStatus.EMPTY,
            items=items,
            provider=self.name,
        )

    async def _fetch_creator_dom_items(
        self,
        page,
        creator_id: str,
        *,
        creator_name: str = "",
        limit: int = 18,
    ) -> list[dict]:
        """Read server-rendered creator cards when Douyin's posts API is 403."""
        creator_url = f"{DOUYIN_HOST}/user/{creator_id}"
        await page.goto(
            creator_url,
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        await page.wait_for_timeout(3_500)
        cards = await page.eval_on_selector_all(
            'a[href*="/video/"]',
            """els => els.map(el => {
                const href = el.href || '';
                const match = href.match(/\/video\/(\d+)/);
                const image = el.querySelector('img');
                const footer = el.closest('footer, [data-e2e="page-footer"]');
                return {
                    video_id: match ? match[1] : '',
                    video_url: href,
                    title: (el.getAttribute('title') || image?.alt || el.textContent || '').trim(),
                    cover_url: image?.currentSrc || image?.src || null,
                    associated: Boolean(image) && !footer,
                };
            })""",
        )

        items: list[dict] = []
        seen: set[str] = set()
        for card in cards or []:
            if not isinstance(card, dict):
                continue
            if card.get("associated") is not True:
                continue
            video_id = str(card.get("video_id") or "").strip()
            if not video_id.isdigit() or video_id in seen:
                continue
            seen.add(video_id)
            items.append({
                "platform": self.platform,
                "requested_platform": self.platform,
                "video_id": video_id,
                "video_url": f"{DOUYIN_HOST}/video/{video_id}",
                "title": str(card.get("title") or "").strip() or "Douyin video",
                "description": "",
                "thumbnail_url": card.get("cover_url"),
                "cover_url": card.get("cover_url"),
                "author": creator_name,
                "creator_id": creator_id,
                "author_url": creator_url,
                "views": 0,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "upload_date": None,
                "duration_seconds": None,
                "radar_metrics": {
                    "views": None,
                    "likes": None,
                    "comments": None,
                    "shares": None,
                },
                "metadata_source": "creator_page_dom",
            })
            if len(items) >= limit:
                break
        return items

    async def creator_snapshot(
        self,
        creator_id: str,
        *,
        limit: int = 18,
        cursor: str | None = None,
    ) -> tuple[dict, SourceProviderResult]:
        """Fetch profile and initial posts through one authenticated session."""
        creator_id = str(creator_id or "").strip()
        if not creator_id:
            raise ValueError("Douyin creator_id is required")
        if limit <= 0:
            raise ValueError("Douyin creator history limit must be positive")

        query = f"creator:{creator_id}"
        raw_awemes: list[dict] = []
        dom_items: list[dict] = []
        async with open_douyin_cdp_session(self.cdp_url) as session:
            profile_payload = await self._fetch_creator_profile(
                session.context,
                session.page,
                session.cookie_dict,
                session.ms_token,
                creator_id,
            )
            info = self._creator_info_from_payload(profile_payload, creator_id)

            cursor_value = str(cursor or "0")
            seen_cursor: set[str] = set()
            next_cursor: str | None = None
            has_more = False
            try:
                while len(raw_awemes) < limit:
                    request_count = max(1, min(18, limit - len(raw_awemes)))
                    payload = await self._fetch_creator_posts(
                        session.context,
                        session.page,
                        session.cookie_dict,
                        session.ms_token,
                        creator_id,
                        cursor_value,
                        request_count,
                    )
                    page_items = payload.get("aweme_list") or []
                    if not isinstance(page_items, list) or not page_items:
                        has_more = False
                        next_cursor = None
                        break
                    raw_awemes.extend(
                        item for item in page_items if isinstance(item, dict)
                    )
                    has_more = bool(payload.get("has_more"))
                    next_cursor = str(payload.get("max_cursor") or "") or None
                    if not has_more:
                        next_cursor = None
                        break
                    if not next_cursor or next_cursor in seen_cursor:
                        has_more = False
                        next_cursor = None
                        break
                    seen_cursor.add(next_cursor)
                    if len(raw_awemes) >= limit:
                        break
                    cursor_value = next_cursor
                    await asyncio.sleep(0.4)
            except PermissionError:
                # A cursor cannot be reconstructed from the DOM. If one or more
                # native pages already succeeded, keep that exact partial page
                # and stop cleanly instead of mixing first-page DOM cards into
                # cursor results (duplicates + a fake next cursor).
                has_more = False
                next_cursor = None
                if not raw_awemes:
                    # Never navigate the user's already-open authenticated
                    # Douyin tab just to perform DOM recovery. Borrow session
                    # cookies and create a temporary owned page.
                    dom_page = session.page
                    close_dom_page = False
                    if not getattr(session, "created_page", False):
                        new_page = getattr(session.context, "new_page", None)
                        if not callable(new_page):
                            raise RuntimeError(
                                "Douyin DOM fallback needs a temporary CDP page; "
                                "refusing to navigate the user's existing tab."
                            )
                        dom_page = await new_page()
                        close_dom_page = True
                    try:
                        dom_items = await self._fetch_creator_dom_items(
                            dom_page,
                            creator_id,
                            creator_name=info.get("display_name") or "",
                            limit=limit,
                        )
                    finally:
                        if close_dom_page:
                            try:
                                await dom_page.close()
                            except Exception:
                                pass
                    if not dom_items:
                        raise

        items: list[dict] = list(dom_items)
        seen: set[str] = set()
        for aweme in raw_awemes:
            row = self._normalize_aweme(aweme, query)
            if not row or row["video_id"] in seen:
                continue
            seen.add(row["video_id"])
            row["creator_id"] = creator_id
            row["author_url"] = f"{DOUYIN_HOST}/user/{creator_id}"
            if info.get("display_name") and not row.get("author"):
                row["author"] = info["display_name"]
            items.append(row)
            if len(items) >= limit:
                break

        history = SourceProviderResult(
            platform=self.platform,
            query=query,
            status=ProviderStatus.SUCCESS if items else ProviderStatus.EMPTY,
            items=items,
            provider=self.name,
            has_more=bool(has_more and next_cursor) if raw_awemes else False,
            next_cursor=(next_cursor if raw_awemes and has_more else None),
        )
        return info, history

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

        payload = await self._request_json(
            context,
            page,
            DOUYIN_SEARCH_PATH,
            params,
            referer=(
                f"{DOUYIN_HOST}/search/{urllib.parse.quote(query)}?type=general"
            ),
            cookie_dict=cookie_dict,
        )
        if "data" not in payload:
            raise RuntimeError("Douyin search response missing data")
        if payload["data"] is not None and not isinstance(payload["data"], list):
            raise RuntimeError("Douyin search data is not a list")
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

        try:
            async with open_douyin_cdp_session(self.cdp_url) as session:
                context = session.context
                page = session.page
                cookie_dict = session.cookie_dict
                ms_token = session.ms_token

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
