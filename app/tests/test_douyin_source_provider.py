"""Focused tests for the canonical app-native Douyin provider."""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.scout import ScoutAgent
from backend.sources import ProviderStatus, SourceProviderError, SourceProviderResult, has_source_provider
from backend.sources.douyin import DouyinProvider, _duration_seconds


def test_douyin_is_registered_app_native():
    assert has_source_provider("douyin")
    assert has_source_provider("DOUYIN")


def test_douyin_normalization_preserves_platform_and_radar_nulls():
    provider = DouyinProvider()
    row = provider._normalize_aweme(
        {
            "aweme_id": "7558451761959226665",
            "desc": "test",
            "create_time": 1700000000,
            "author": {"sec_uid": "SEC123", "nickname": "Creator"},
            "statistics": {
                "digg_count": 100,
                "comment_count": 20,
                "share_count": 5,
                "collect_count": 9,
            },
            "video": {
                "duration": 321000,
                "cover": {"url_list": ["https://img.example/cover.jpg"]},
            },
        },
        "健身",
    )
    assert row is not None
    assert row["platform"] == "douyin"
    assert row["requested_platform"] == "douyin"
    assert row["video_url"].endswith("/7558451761959226665")
    assert row["creator_id"] == "SEC123"
    assert row["duration_seconds"] == 321
    assert row["radar_metrics"]["views"] is None
    assert row["views"] == 0
    assert row["radar_metrics"]["likes"] == 100


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(321000, 321), (1000, 1), (900, 1), (None, None), ("bad", None)],
)
def test_douyin_duration(raw, expected):
    assert _duration_seconds(raw) == expected


@pytest.mark.asyncio
async def test_scout_routes_douyin_to_native_provider_no_legacy_fallback():
    row = {
        "platform": "douyin",
        "requested_platform": "douyin",
        "video_id": "1",
        "video_url": "https://www.douyin.com/video/1",
        "views": 0,
        "likes": 1,
        "comments": 0,
        "shares": 0,
    }
    result = SourceProviderResult(
        platform="douyin",
        query="test",
        status=ProviderStatus.SUCCESS,
        items=[row],
        provider="test",
    )
    agent = ScoutAgent()

    with patch(
        "backend.agents.scout.search_source_provider",
        new=AsyncMock(return_value=result),
    ) as native, patch.object(
        agent,
        "_scout_via_ytdlp_search",
        new=AsyncMock(side_effect=AssertionError("must not fallback")),
    ):
        rows = await agent._scout_platform("douyin", "test", None)

    native.assert_awaited_once()
    assert rows == [row]


@pytest.mark.asyncio
async def test_scout_surfaces_douyin_auth_required():
    result = SourceProviderResult(
        platform="douyin",
        query="test",
        status=ProviderStatus.AUTH_REQUIRED,
        error="CDP session unavailable",
        provider="test",
    )
    agent = ScoutAgent()
    with patch(
        "backend.agents.scout.search_source_provider",
        new=AsyncMock(return_value=result),
    ):
        with pytest.raises(SourceProviderError) as exc:
            await agent._scout_platform("douyin", "test", None)
    assert exc.value.status == ProviderStatus.AUTH_REQUIRED


@pytest.mark.asyncio
async def test_douyin_picks_logged_in_context_by_existing_page_not_context_zero():
    provider = DouyinProvider()

    class Page:
        def __init__(self, url):
            self.url = url

    class Context:
        def __init__(self, pages):
            self.pages = pages
            self.new_page_called = False
        async def new_page(self):
            self.new_page_called = True
            raise AssertionError("should reuse existing Douyin page")

    first = Context([Page("https://example.com")])
    second_page = Page("https://www.douyin.com/search/test")
    second = Context([second_page])

    class Browser:
        contexts = [first, second]

    with patch.object(
        DouyinProvider,
        "_session_state",
        new=AsyncMock(return_value=(True, {"LOGIN_STATUS": "1"}, None)),
    ):
        context, page, created = await provider._pick_context_and_page(Browser())

    assert context is second
    assert page is second_page
    assert created is False
    assert first.new_page_called is False

@pytest.mark.asyncio
async def test_douyin_skips_logged_out_douyin_context_for_later_authenticated_one():
    provider = DouyinProvider()

    class Page:
        def __init__(self, url):
            self.url = url

    class Context:
        def __init__(self, pages):
            self.pages = pages

        async def new_page(self):
            raise AssertionError("existing Douyin pages should be reused")

    first_page = Page("https://www.douyin.com/search/first")
    second_page = Page("https://www.douyin.com/search/second")
    first = Context([first_page])
    second = Context([second_page])

    class Browser:
        contexts = [first, second]

    with patch.object(
        DouyinProvider,
        "_session_state",
        new=AsyncMock(
            side_effect=[
                (False, {}, None),
                (True, {"LOGIN_STATUS": "1"}, None),
            ]
        ),
    ) as session_state:
        context, page, created = await provider._pick_context_and_page(Browser())

    assert context is second
    assert page is second_page
    assert created is False
    assert session_state.await_count == 2

@pytest.mark.asyncio
async def test_douyin_unusable_context_does_not_abort_remaining_contexts():
    provider = DouyinProvider()

    class Page:
        def __init__(self, url=""):
            self.url = url
            self.closed = False

        async def goto(self, *args, **kwargs):
            self.url = "https://www.douyin.com/"

        async def close(self):
            self.closed = True

    class BrokenContext:
        pages = []

        async def new_page(self):
            raise RuntimeError("context closed")

    good_page = Page()

    class GoodContext:
        pages = []

        async def new_page(self):
            return good_page

    class Browser:
        contexts = [BrokenContext(), GoodContext()]

    with patch.object(
        DouyinProvider,
        "_session_state",
        new=AsyncMock(return_value=(True, {"LOGIN_STATUS": "1"}, None)),
    ):
        context, page, created = await provider._pick_context_and_page(Browser())

    assert isinstance(context, GoodContext)
    assert page is good_page
    assert created is True
    assert good_page.closed is False

@pytest.mark.asyncio
async def test_douyin_search_uses_ten_item_page_step_even_when_request_count_is_fifteen():
    provider = DouyinProvider()
    offsets = []
    context = object()
    page = object()

    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        yield SimpleNamespace(
            context=context,
            page=page,
            cookie_dict={},
            ms_token=None,
        )

    async def fetch_page(
        _context, _page, _cookies, _token, _query, offset, _search_id, count
    ):
        offsets.append((offset, count))
        return {
            "status_code": 0,
            "data": [
                {
                    "aweme_info": {
                        "aweme_id": str(i),
                        "statistics": {},
                        "video": {},
                    }
                }
                for i in range(offset + 1, offset + 11)
            ],
            "extra": {"logid": f"log-{offset}"},
        }

    with patch("backend.sources.douyin.open_douyin_cdp_session", fake_session), \
         patch.object(provider, "_fetch_search_page", side_effect=fetch_page):
        result = await provider.search("健身", limit=15)

    assert result.status == ProviderStatus.SUCCESS
    assert len(result.items) == 15
    assert offsets == [(0, 15), (10, 15)]


@pytest.mark.asyncio
async def test_douyin_waf_status_2483_is_access_restriction():
    provider = DouyinProvider()

    class Response:
        status = 200

        async def text(self):
            return '{"status_code":2483,"status_msg":"risk control"}'

        async def json(self):
            return {"status_code": 2483, "status_msg": "risk control", "data": []}

    class Request:
        async def get(self, *args, **kwargs):
            return Response()

    class Context:
        request = Request()

    class Page:
        async def evaluate(self, *args, **kwargs):
            return "Mozilla/5.0"

    with pytest.raises(PermissionError, match="2483"):
        await provider._fetch_search_page(
            Context(), Page(), {}, None, "健身", 0, "", 15
        )


@pytest.mark.asyncio
async def test_douyin_search_surfaces_access_failure_from_shared_session():
    provider = DouyinProvider()

    @asynccontextmanager
    async def blocked_session(*args, **kwargs):
        raise PermissionError("risk control")
        yield  # pragma: no cover

    with patch("backend.sources.douyin.open_douyin_cdp_session", blocked_session):
        result = await provider.search("健身", limit=1)

    assert result.status == ProviderStatus.AUTH_REQUIRED
    assert result.error == "risk control"


@pytest.mark.asyncio
async def test_creator_request_uses_session_uifid_and_bounded_timeout():
    provider = DouyinProvider()
    captured = {}

    class Response:
        status = 200

        async def text(self):
            return '{"status_code":0,"user":{}}'

        async def json(self):
            return {"status_code": 0, "user": {}}

    class Request:
        async def get(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return Response()

    class Context:
        request = Request()

    class Page:
        async def evaluate(self, *args, **kwargs):
            return "Mozilla/5.0 Edge"

    await provider._request_json(
        Context(),
        Page(),
        "/aweme/v1/web/user/profile/other/",
        {"sec_user_id": "creator"},
        referer="https://www.douyin.com/user/creator",
        cookie_dict={"UIFID_TEMP": "session-derived"},
    )

    assert captured["headers"]["uifid"] == "session-derived"
    assert captured["timeout"] <= 20_000
    assert captured["headers"].get("x-tt-argus") != "1"


@pytest.mark.asyncio
async def test_creator_snapshot_reuses_one_authenticated_cdp_session():
    provider = DouyinProvider()
    opened = 0
    session = SimpleNamespace(
        context=object(),
        page=object(),
        cookie_dict={"UIFID": "session-derived"},
        ms_token="token",
    )

    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        nonlocal opened
        opened += 1
        yield session

    profile_payload = {
        "user": {"nickname": "Creator"},
        "stats": {"follower_count": 12, "aweme_count": 34},
    }
    posts_payload = {
        "aweme_list": [{
            "aweme_id": "123",
            "desc": "Exact video",
            "author": {"sec_uid": "creator", "nickname": "Creator"},
            "statistics": {},
            "video": {},
        }],
        "has_more": False,
    }

    with patch("backend.sources.douyin.open_douyin_cdp_session", fake_session), \
         patch.object(provider, "_fetch_creator_profile", new=AsyncMock(return_value=profile_payload)), \
         patch.object(provider, "_fetch_creator_posts", new=AsyncMock(return_value=posts_payload)):
        info, history = await provider.creator_snapshot("creator", limit=18)

    assert opened == 1
    assert info["display_name"] == "Creator"
    assert history.status == ProviderStatus.SUCCESS
    assert history.items[0]["video_url"] == "https://www.douyin.com/video/123"


@pytest.mark.asyncio
async def test_creator_snapshot_falls_back_to_creator_page_when_posts_api_is_403():
    provider = DouyinProvider()

    class Page:
        def __init__(self):
            self.goto = AsyncMock()
            self.wait_for_timeout = AsyncMock()

        async def eval_on_selector_all(self, selector, script):
            assert selector == 'a[href*="/video/"]'
            return [
                {
                    "video_id": "123",
                    "video_url": "https://www.douyin.com/video/123?source=Baiduspider",
                    "title": "First video",
                    "cover_url": "https://img.example/123.jpg",
                    "associated": True,
                },
                {
                    "video_id": "456",
                    "video_url": "https://www.douyin.com/video/456",
                    "title": "Second video",
                    "cover_url": None,
                    "associated": True,
                },
            ]

    user_page = Page()
    dom_page = Page()
    dom_page.close = AsyncMock()

    class Context:
        async def new_page(self):
            return dom_page

    session = SimpleNamespace(
        context=Context(),
        page=user_page,
        created_page=False,
        cookie_dict={"UIFID": "session-derived"},
        ms_token="token",
    )

    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        yield session

    with patch("backend.sources.douyin.open_douyin_cdp_session", fake_session), \
         patch.object(provider, "_fetch_creator_profile", new=AsyncMock(return_value={
             "user": {"nickname": "Creator"},
             "stats": {},
         })), \
         patch.object(
             provider,
             "_fetch_creator_posts",
             new=AsyncMock(side_effect=PermissionError("Douyin HTTP 403")),
         ):
        info, history = await provider.creator_snapshot("creator", limit=18)

    assert info["display_name"] == "Creator"
    assert history.status == ProviderStatus.SUCCESS
    assert [item["video_id"] for item in history.items] == ["123", "456"]
    assert history.items[0]["video_url"] == "https://www.douyin.com/video/123"
    user_page.goto.assert_not_awaited()
    dom_page.goto.assert_awaited_once_with(
        "https://www.douyin.com/user/creator",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    dom_page.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_creator_dom_fallback_rejects_unassociated_footer_links():
    provider = DouyinProvider()

    class Page:
        async def goto(self, *args, **kwargs):
            return None

        async def wait_for_timeout(self, milliseconds):
            return None

        async def eval_on_selector_all(self, selector, script):
            return [{
                "video_id": "999",
                "video_url": "https://www.douyin.com/video/999",
                "title": "SEO footer link",
                "cover_url": None,
                "associated": False,
            }]

    items = await provider._fetch_creator_dom_items(Page(), "creator", limit=18)

    assert items == []




@pytest.mark.asyncio
async def test_creator_snapshot_partial_last_native_page_does_not_skip_cursor_rows(monkeypatch):
    provider = DouyinProvider()
    counts = []
    cursors = []

    session = SimpleNamespace(
        context=object(),
        page=object(),
        created_page=True,
        cookie_dict={"UIFID": "session-derived"},
        ms_token="token",
    )

    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        yield session

    async def fake_posts(
        _context, _page, _cookies, _token, _creator_id, cursor, count,
    ):
        cursors.append(cursor)
        counts.append(count)
        page_no = len(counts)
        start = (page_no - 1) * 18 + 1
        return {
            "aweme_list": [
                {
                    "aweme_id": str(start + i),
                    "desc": f"Video {start + i}",
                    "author": {"sec_uid": "creator", "nickname": "Creator"},
                    "statistics": {},
                    "video": {},
                }
                for i in range(count)
            ],
            "has_more": True,
            "max_cursor": f"cursor-{page_no}",
        }

    monkeypatch.setattr("backend.sources.douyin.asyncio.sleep", AsyncMock())
    with patch("backend.sources.douyin.open_douyin_cdp_session", fake_session), \
         patch.object(provider, "_fetch_creator_profile", new=AsyncMock(return_value={
             "user": {"nickname": "Creator"},
             "stats": {"aweme_count": 100},
         })), \
         patch.object(provider, "_fetch_creator_posts", new=fake_posts):
        _info, history = await provider.creator_snapshot(
            "creator",
            limit=50,
            cursor="cursor-start",
        )

    assert cursors == ["cursor-start", "cursor-1", "cursor-2"]
    assert counts == [18, 18, 14]
    assert len(history.items) == 50
    assert history.has_more is True
    assert history.next_cursor == "cursor-3"



@pytest.mark.asyncio
async def test_creator_snapshot_partial_native_page_does_not_mix_dom_fallback(monkeypatch):
    provider = DouyinProvider()
    dom_called = False

    session = SimpleNamespace(
        context=object(),
        page=object(),
        created_page=True,
        cookie_dict={"UIFID": "session-derived"},
        ms_token="token",
    )

    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        yield session

    first_page = {
        "aweme_list": [{
            "aweme_id": str(i),
            "desc": f"Video {i}",
            "author": {"sec_uid": "creator", "nickname": "Creator"},
            "statistics": {},
            "video": {},
        } for i in range(1, 19)],
        "has_more": True,
        "max_cursor": "cursor-1",
    }

    calls = 0

    async def fake_posts(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return first_page
        raise PermissionError("risk control on next cursor")

    async def fake_dom(*args, **kwargs):
        nonlocal dom_called
        dom_called = True
        return [{
            "platform": "douyin",
            "requested_platform": "douyin",
            "video_id": "999",
            "video_url": "https://www.douyin.com/video/999",
            "title": "DOM duplicate/fallback",
            "description": "",
            "thumbnail_url": None,
            "cover_url": None,
            "author": "Creator",
            "creator_id": "creator",
            "author_url": "https://www.douyin.com/user/creator",
            "views": 0,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "upload_date": None,
            "duration_seconds": None,
            "radar_metrics": {},
            "metadata_source": "creator_page_dom",
        }]

    monkeypatch.setattr("backend.sources.douyin.asyncio.sleep", AsyncMock())
    with patch("backend.sources.douyin.open_douyin_cdp_session", fake_session), \
         patch.object(provider, "_fetch_creator_profile", new=AsyncMock(return_value={
             "user": {"nickname": "Creator"}, "stats": {"aweme_count": 100},
         })), \
         patch.object(provider, "_fetch_creator_posts", new=fake_posts), \
         patch.object(provider, "_fetch_creator_dom_items", new=fake_dom):
        _info, history = await provider.creator_snapshot("creator", limit=36)

    assert len(history.items) == 18
    assert history.has_more is False
    assert history.next_cursor is None
    assert dom_called is False
    assert "999" not in [row["video_id"] for row in history.items]


@pytest.mark.asyncio
async def test_creator_history_starts_from_zero_cursor_and_bounds_final_request(monkeypatch):
    provider = DouyinProvider()
    calls = []
    session = SimpleNamespace(
        context=object(),
        page=object(),
        cookie_dict={"UIFID": "session-derived"},
        ms_token="token",
    )

    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        yield session

    async def fake_posts(
        _context, _page, _cookies, _token, _creator_id, cursor, count,
    ):
        calls.append((cursor, count))
        page_no = len(calls)
        start = 1 if page_no == 1 else 19
        return {
            "aweme_list": [
                {
                    "aweme_id": str(start + i),
                    "desc": f"Video {start + i}",
                    "author": {"sec_uid": "creator", "nickname": "Creator"},
                    "statistics": {},
                    "video": {},
                }
                for i in range(count)
            ],
            "has_more": True,
            "max_cursor": f"cursor-{page_no}",
        }

    monkeypatch.setattr("backend.sources.douyin.asyncio.sleep", AsyncMock())
    with patch("backend.sources.douyin.open_douyin_cdp_session", fake_session), \
         patch.object(provider, "_fetch_creator_posts", new=fake_posts):
        history = await provider.creator_history("creator", limit=20)

    assert history.status == ProviderStatus.SUCCESS
    assert calls == [("0", 18), ("cursor-1", 2)]
    assert len(history.items) == 20
