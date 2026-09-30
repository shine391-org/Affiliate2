from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from backend.api import channels
from backend.sources.base import ProviderStatus, SourceProviderResult


class _DbResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _DbSession:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, _query):
        return _DbResult(self.value)


def _channel():
    return SimpleNamespace(
        id="db-channel",
        platform="douyin",
        channel_id="creator-sec-id",
        channel_url="https://www.douyin.com/user/creator-sec-id",
        channel_name="Creator",
        thumbnail_url="https://img.example/avatar.jpg",
        subscriber_count=12,
        video_count=34,
    )


def _provider_with_snapshot(side_effect=None):
    provider = SimpleNamespace()
    if side_effect is not None:
        provider.creator_snapshot = AsyncMock(side_effect=side_effect)
    else:
        provider.creator_snapshot = AsyncMock(return_value=(
            {
                "display_name": "Creator",
                "avatar_url": "https://img.example/avatar.jpg",
                "follower_count": 12,
                "video_count": 34,
            },
            SourceProviderResult(
                platform="douyin",
                query="creator:creator-sec-id",
                status=ProviderStatus.SUCCESS,
                provider="test",
                items=[{
                    "platform": "douyin",
                    "creator_id": "creator-sec-id",
                    "video_id": "123",
                    "video_url": "https://www.douyin.com/video/123",
                    "title": "Exact video",
                    "thumbnail_url": "https://img.example/cover.jpg",
                    "views": 10,
                    "likes": 2,
                    "comments": 1,
                    "shares": 0,
                    "duration_seconds": 8,
                    "upload_date": None,
                }],
            ),
        ))
    return provider


@pytest.mark.asyncio
async def test_douyin_channel_video_response_preserves_exact_identity(monkeypatch):
    provider = _provider_with_snapshot()
    monkeypatch.setattr(channels, "AsyncSessionLocal", lambda: _DbSession(_channel()))
    monkeypatch.setattr(channels, "_get_douyin_provider", AsyncMock(return_value=provider))

    # page_token is declared with a FastAPI Query(None) default. Calling the
    # handler directly (as this unit test does) bypasses FastAPI's dependency
    # resolution, so the sentinel object itself would arrive here. Pass the
    # resolved default explicitly, exactly as the framework would.
    result = await channels.get_channel_videos("db-channel", page_token=None, limit=18)

    video = result["videos"][0]
    assert video["platform"] == "douyin"
    assert video["creator_id"] == "creator-sec-id"
    assert video["aweme_id"] == "123"
    assert video["video_id"] == "123"
    assert video["source_url"] == "https://www.douyin.com/video/123"
    assert video["url"] == video["source_url"]
    provider.creator_snapshot.assert_awaited_once_with(
        "creator-sec-id", limit=18, cursor="0"
    )


@pytest.mark.asyncio
async def test_douyin_auth_required_is_409_not_generic_502(monkeypatch):
    provider = _provider_with_snapshot(PermissionError("login required"))
    monkeypatch.setattr(channels, "AsyncSessionLocal", lambda: _DbSession(_channel()))
    monkeypatch.setattr(channels, "_get_douyin_provider", AsyncMock(return_value=provider))

    with pytest.raises(HTTPException) as exc:
        await channels.get_channel_videos("db-channel", limit=18)

    assert exc.value.status_code == 409
    assert "login" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_douyin_provider_timeout_is_explicit_504(monkeypatch):
    provider = _provider_with_snapshot(asyncio.TimeoutError())
    monkeypatch.setattr(channels, "AsyncSessionLocal", lambda: _DbSession(_channel()))
    monkeypatch.setattr(channels, "_get_douyin_provider", AsyncMock(return_value=provider))

    with pytest.raises(HTTPException) as exc:
        await channels.get_channel_videos("db-channel", limit=18)

    assert exc.value.status_code == 504
    assert "timed out" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_douyin_provider_failure_is_explicit_502(monkeypatch):
    provider = _provider_with_snapshot(RuntimeError("provider rejected request"))
    monkeypatch.setattr(channels, "AsyncSessionLocal", lambda: _DbSession(_channel()))
    monkeypatch.setattr(channels, "_get_douyin_provider", AsyncMock(return_value=provider))

    with pytest.raises(HTTPException) as exc:
        await channels.get_channel_videos("db-channel", limit=18)

    assert exc.value.status_code == 502
    assert "provider rejected request" in str(exc.value.detail).lower()



@pytest.mark.asyncio
async def test_douyin_channel_cursor_is_forwarded_and_returned(monkeypatch):
    provider = _provider_with_snapshot()
    info, history = provider.creator_snapshot.return_value
    history.has_more = True
    history.next_cursor = "cursor-2"

    monkeypatch.setattr(channels, "AsyncSessionLocal", lambda: _DbSession(_channel()))
    monkeypatch.setattr(channels, "_get_douyin_provider", AsyncMock(return_value=provider))

    result = await channels.get_channel_videos(
        "db-channel",
        page_token="cursor-1",
        limit=36,
    )

    provider.creator_snapshot.assert_awaited_once_with(
        "creator-sec-id",
        limit=36,
        cursor="cursor-1",
    )
    assert result["has_more"] is True
    assert result["next_page_token"] == "cursor-2"


@pytest.mark.asyncio
async def test_douyin_channel_last_page_has_no_next_token(monkeypatch):
    provider = _provider_with_snapshot()
    monkeypatch.setattr(channels, "AsyncSessionLocal", lambda: _DbSession(_channel()))
    monkeypatch.setattr(channels, "_get_douyin_provider", AsyncMock(return_value=provider))

    result = await channels.get_channel_videos(
        "db-channel",
        page_token="last",
        limit=50,
    )

    assert result["has_more"] is False
    assert result["next_page_token"] is None
