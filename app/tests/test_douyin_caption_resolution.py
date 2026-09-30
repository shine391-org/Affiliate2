"""Caption-search resolution: Douyin /video/<id> can serve a different aweme.

These tests pin the exact-identity guarantee of the CDP media resolver: when the
video page's own player/network traffic belongs to another aweme, the resolver
must reach for the canonical DouyinProvider search surface and only accept a
candidate whose aweme_id equals the requested one.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.services.douyin_media_resolver import resolve_douyin_media


def _silent_page():
    class Page:
        def __init__(self):
            self.response_handler = None

        def on(self, event, handler):
            if event == "response":
                self.response_handler = handler

        async def route(self, *args, **kwargs):
            return None

        async def goto(self, *args, **kwargs):
            return None

        async def evaluate(self, script):
            return []

        async def wait_for_function(self, *args, **kwargs):
            return None

        async def close(self):
            return None

    return Page()


class _FakeSearchProvider:
    """Minimal stand-in for the canonical DouyinProvider search surface."""

    calls: list = []

    def __init__(self, cdp_url=None):
        self.cdp_url = cdp_url

    @property
    def _common_params(self, ms_token):
        return {"aid": "6383"}

    async def _fetch_search_page(
        self, context, page, cookies, ms_token, keyword, offset, search_id, count
    ):
        type(self).calls.append(keyword)
        if offset:
            return {"data": []}
        return {"data": [
            {"aweme_info": {
                "aweme_id": "999",
                "desc": "decoy",
                "video": {"play_addr": {"url_list": ["https://v3-web.douyinvod.com/decoy.mp4"]}},
            }},
            {"aweme_info": {
                "aweme_id": "123",
                "desc": keyword,
                "video": {"play_addr": {"url_list": [
                    "https://example.com/redirect",
                    "https://v3-web.douyinvod.com/exact.mp4?mime_type=video_mp4",
                ]}},
            }},
        ]}


class _DecoyOnlySearchProvider(_FakeSearchProvider):
    async def _fetch_search_page(self, *args, **kwargs):
        return {"data": [{"aweme_info": {
            "aweme_id": "999",
            "video": {"play_addr": {"url_list": ["https://v3-web.douyinvod.com/decoy.mp4"]}},
        }}]}


def _session(page):
    class Context:
        async def new_page(self):
            return page

    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        yield SimpleNamespace(
            context=Context(),
            page=None,
            created_page=False,
            user_agent="UA",
            cookie_dict={"UIFID": "u"},
            ms_token="ms",
        )

    return fake_session


@pytest.mark.asyncio
async def test_caption_search_resolves_the_requested_aweme_when_the_page_plays_another():
    wrong_media = "https://v3-web.douyinvod.com/other.mp4?__vid=999"
    page = _silent_page()

    class Response:
        url = wrong_media
        headers = {"content-type": "video/mp4", "content-length": "999999"}

    async def goto(*args, **kwargs):
        page.response_handler(Response())

    page.goto = goto

    _FakeSearchProvider.calls = []

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        _session(page),
    ), patch(
        "backend.sources.douyin.DouyinProvider",
        _FakeSearchProvider,
    ):
        resolved = await resolve_douyin_media(
            "https://www.douyin.com/video/123",
            title_hint="酸辣柠檬虾 tutorial",
        )

    assert resolved.url == "https://v3-web.douyinvod.com/exact.mp4?mime_type=video_mp4"
    assert resolved.candidate_aweme_id == "123"
    assert resolved.provenance == "search:play_addr"
    # Only the full caption is probed once it already yields the exact aweme.
    assert _FakeSearchProvider.calls == ["酸辣柠檬虾 tutorial"]


@pytest.mark.asyncio
async def test_caption_search_never_accepts_another_aweme():
    page = _silent_page()

    _DecoyOnlySearchProvider.calls = []

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        _session(page),
    ), patch(
        "backend.sources.douyin.DouyinProvider",
        _DecoyOnlySearchProvider,
    ):
        with pytest.raises(RuntimeError, match=r"requested_aweme_id=123"):
            await resolve_douyin_media(
                "https://www.douyin.com/video/123",
                title_hint="some caption",
            )
