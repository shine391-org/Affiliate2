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

