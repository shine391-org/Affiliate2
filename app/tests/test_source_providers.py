"""Focused Phase B tests for the app-native source-provider boundary."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.agents.scout import ScoutAgent
from backend.sources import (
    ProviderStatus,
    SourceProviderError,
    SourceProviderResult,
    fetch_creator_history,
    has_source_provider,
)
from backend.sources.bilibili import BilibiliProvider, _duration_seconds, _optional_metric_int


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


def test_bilibili_zero_views_do_not_fall_through_to_another_metric():
    provider = BilibiliProvider()
    item = provider._normalize_item(
        {"aid": 9, "play": 0, "video_review": 999, "danmaku": 0},
        "zero",
    )
    assert item["views"] == 0
    assert item["comments"] == 0


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
        (None, None),
        ("--", None),
        ("1,234", 1234),
        ("1.5万", 15000),
        ("2k", 2000),
    ],
)
def test_bilibili_metric_coercion(raw, expected):
    assert _optional_metric_int(raw) == expected


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


@pytest.mark.asyncio
async def test_scout_normalizes_platform_names_to_lowercase(monkeypatch):
    agent = ScoutAgent()
    seen = []

    async def fake_platform(platform, niche, user_settings=None, user_id="local"):
        seen.append(platform)
        return []

    monkeypatch.setattr(agent, "_scout_platform", fake_platform)
    monkeypatch.setattr(agent, "_enrich_with_outlier_scores", AsyncMock())
    monkeypatch.setattr(agent, "_save_results", AsyncMock(return_value=([], 0)))

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeDB:
        async def execute(self, *args, **kwargs):
            return _FakeResult()

    class _FakeCM:
        async def __aenter__(self):
            return _FakeDB()
        async def __aexit__(self, *args):
            return False

    with patch("backend.agents.scout.AsyncSessionLocal", return_value=_FakeCM()), \
         patch("backend.agents.scout.update_job_status", new=AsyncMock()), \
         patch("backend.agents.scout.ws_manager") as ws, \
         patch("backend.core.ai_retry.ai_refine_search", new=AsyncMock(return_value=None)):
        ws.send_progress = AsyncMock()
        ws.send_constraint_warning = AsyncMock()
        ws.send = AsyncMock()
        await agent.run("job-lowercase", "AI", ["BILIBILI"])

    assert seen == ["bilibili"]


@pytest.mark.asyncio
async def test_bilibili_creator_history_normalizes_real_records(monkeypatch):
    provider = BilibiliProvider()

    async def fake_keys(client):
        return ("a" * 32, "b" * 32)

    async def fake_page(client, creator_id, page, page_size, wbi_keys):
        if page > 1:
            return []
        return [
            {
                "aid": 77,
                "bvid": "BVcreator77",
                "mid": creator_id,
                "author": "Creator",
                "title": "Older video",
                "description": "desc",
                "created": 1700000000,
                "play": 1234,
                "video_review": 25,
                "length": "05:00",
                "pic": "//i0.hdslb.com/creator.jpg",
            }
        ]

    monkeypatch.setattr(provider, "_get_wbi_keys", fake_keys)
    monkeypatch.setattr(provider, "_fetch_creator_page", fake_page)

    result = await provider.creator_history("999", creator_name="Creator", limit=10)
    assert result.status == ProviderStatus.SUCCESS
    assert len(result.items) == 1
    row = result.items[0]
    assert row["platform"] == "bilibili"
    assert row["creator_id"] == "999"
    assert row["video_id"] == "77"
    assert row["views"] == 1234
    assert row["comments"] == 25
    assert row["duration_seconds"] == 300
    assert row["source_keyword"] == "creator:999"


@pytest.mark.asyncio
async def test_bilibili_creator_access_failure_is_explicit(monkeypatch):
    provider = BilibiliProvider()

    async def blocked(client):
        raise PermissionError("risk control -352")

    monkeypatch.setattr(provider, "_get_wbi_keys", blocked)
    result = await provider.creator_history("999", limit=10)
    assert result.status == ProviderStatus.AUTH_REQUIRED
    assert "-352" in (result.error or "")


@pytest.mark.asyncio
async def test_creator_history_registry_dispatches(monkeypatch):
    fake = SourceProviderResult(
        platform="bilibili",
        query="creator:123",
        status=ProviderStatus.SUCCESS,
        items=[],
        provider="test",
    )
    with patch(
        "backend.sources.bilibili.BilibiliProvider.creator_history",
        new=AsyncMock(return_value=fake),
    ) as history:
        result = await fetch_creator_history(
            "bilibili",
            "123",
            creator_name="name",
            limit=5,
        )
    history.assert_awaited_once_with("123", creator_name="name", limit=5)
    assert result.platform == "bilibili"


@pytest.mark.asyncio
async def test_bilibili_code_zero_risk_voucher_is_not_false_empty():
    provider = BilibiliProvider()

    class Response:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"code": 0, "data": {"v_voucher": "challenge-token"}}

    class Client:
        async def get(self, *args, **kwargs):
            return Response()

    with pytest.raises(PermissionError, match="risk-control"):
        await provider._fetch_page(Client(), "AI", 1, 10)


@pytest.mark.asyncio
async def test_bilibili_code_zero_unexpected_envelope_is_error_not_empty():
    provider = BilibiliProvider()

    class Response:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"code": 0, "data": {"unexpected": True}}

    class Client:
        async def get(self, *args, **kwargs):
            return Response()

    with pytest.raises(RuntimeError, match="missing data.result"):
        await provider._fetch_page(Client(), "AI", 1, 10)


@pytest.mark.asyncio
async def test_bilibili_normal_empty_result_remains_empty():
    provider = BilibiliProvider()

    class Response:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"code": 0, "data": {"result": []}}

    class Client:
        async def get(self, *args, **kwargs):
            return Response()

    assert await provider._fetch_page(Client(), "AI", 1, 10) == []
