"""Focused tests for the canonical app-native Douyin provider."""
from __future__ import annotations

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

    context, page, created = await provider._pick_context_and_page(Browser())
    assert context is second
    assert page is second_page
    assert created is False
    assert first.new_page_called is False
