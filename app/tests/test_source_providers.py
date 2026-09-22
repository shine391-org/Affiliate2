"""Focused Phase B tests for the app-native source-provider boundary."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.scout import ScoutAgent
from backend.sources import (
    ProviderStatus,
    SourceProviderError,
    SourceProviderResult,
    has_source_provider,
)
from backend.sources.bilibili import BilibiliProvider, _duration_seconds, _metric_int


def test_bilibili_is_app_native_provider():
    assert has_source_provider("bilibili") is True
    assert has_source_provider("BILIBILI") is True
    assert has_source_provider("made-up-platform") is False


def test_provider_result_rejects_cross_platform_rows():
    with pytest.raises(ValueError):
        SourceProviderResult(
            platform="bilibili",
            query="x",
            status=ProviderStatus.SUCCESS,
            items=[{"platform": "youtube", "video_id": "wrong"}],
        )


def test_bilibili_normalizes_real_platform_identity():
    provider = BilibiliProvider()
    item = provider._normalize_item(
        {
            "aid": 12345,
            "bvid": "BV1abc123",
            "mid": 999,
            "author": "Creator",
            "title": '<em class="keyword">AI</em> test',
            "description": "desc",
            "play": "1.2万",
            "like": "500",
            "danmaku": "42",
            "favorites": "88",
            "duration": "03:21",
            "pubdate": 1700000000,
            "pic": "//i0.hdslb.com/test.jpg",
        },
        "AI",
    )
    assert item is not None
    assert item["platform"] == "bilibili"
    assert item["requested_platform"] == "bilibili"
    assert item["video_id"] == "12345"
    assert item["video_url"] == "https://www.bilibili.com/video/BV1abc123"
    assert item["author_url"] == "https://space.bilibili.com/999"
    assert item["title"] == "AI test"
    assert item["views"] == 12000
    assert item["duration_seconds"] == 201
    assert item["thumbnail_url"].startswith("https://")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 0),
        ("--", 0),
        ("1,234", 1234),
        ("1.5万", 15000),
        ("2k", 2000),
    ],
)
def test_bilibili_metric_coercion(raw, expected):
    assert _metric_int(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("03:21", 201), ("1:02:03", 3723), ("bad", None), (None, None)],
)
def test_bilibili_duration(raw, expected):
    assert _duration_seconds(raw) == expected


@pytest.mark.asyncio
async def test_bilibili_search_dedupes_and_returns_success(monkeypatch):
    provider = BilibiliProvider()
    pages = [
        [
            {"aid": 1, "title": "A", "play": 10},
            {"aid": 1, "title": "duplicate", "play": 11},
            {"aid": 2, "title": "B", "play": 20},
        ],
        [],
    ]

    async def fake_fetch(client, query, page, page_size):
        return pages[page - 1]

    monkeypatch.setattr(provider, "_fetch_page", fake_fetch)
    result = await provider.search("test", limit=3)
    assert result.status == ProviderStatus.SUCCESS
    assert [row["video_id"] for row in result.items] == ["1", "2"]
    assert all(row["platform"] == "bilibili" for row in result.items)


@pytest.mark.asyncio
async def test_bilibili_access_failure_is_explicit(monkeypatch):
    provider = BilibiliProvider()

    async def blocked(*args, **kwargs):
        raise PermissionError("HTTP 412")

    monkeypatch.setattr(provider, "_fetch_page", blocked)
    result = await provider.search("test", limit=3)
    assert result.status == ProviderStatus.AUTH_REQUIRED
    assert result.items == []
    assert "412" in (result.error or "")


@pytest.mark.asyncio
async def test_scout_uses_real_bilibili_provider_and_never_youtube_fallback():
    row = {
        "platform": "bilibili",
        "requested_platform": "bilibili",
        "video_id": "123",
        "video_url": "https://www.bilibili.com/video/av123",
        "title": "real",
        "views": 10,
        "likes": 1,
        "comments": 0,
        "shares": 0,
    }
    provider_result = SourceProviderResult(
        platform="bilibili",
        query="AI",
        status=ProviderStatus.SUCCESS,
        items=[row],
        provider="test",
    )

    agent = ScoutAgent()
    with patch(
        "backend.agents.scout.search_source_provider",
        new=AsyncMock(return_value=provider_result),
    ) as native, patch.object(
        agent,
        "_scout_via_ytdlp_search",
        new=AsyncMock(side_effect=AssertionError("must not fallback")),
    ):
        result = await agent._scout_platform("bilibili", "AI", None)

    native.assert_awaited_once()
    assert result == [row]
    assert result[0]["platform"] == "bilibili"


@pytest.mark.asyncio
async def test_scout_surfaces_bilibili_auth_required_without_fallback():
    provider_result = SourceProviderResult(
        platform="bilibili",
        query="AI",
        status=ProviderStatus.AUTH_REQUIRED,
        error="session required",
        provider="test",
    )
    agent = ScoutAgent()

    with patch(
        "backend.agents.scout.search_source_provider",
        new=AsyncMock(return_value=provider_result),
    ), patch.object(
        agent,
        "_scout_via_ytdlp_search",
        new=AsyncMock(side_effect=AssertionError("must not fallback")),
    ):
        with pytest.raises(SourceProviderError) as exc:
            await agent._scout_platform("bilibili", "AI", None)

    assert exc.value.status == ProviderStatus.AUTH_REQUIRED
