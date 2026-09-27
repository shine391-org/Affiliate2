"""The Douyin CDP resolver stays a resolver, not a second downloader."""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from backend.services.douyin_media_resolver import (
    _candidate_score,
    resolve_douyin_media,
)


@pytest.mark.asyncio
async def test_resolver_returns_dom_media_and_closes_temp_page():
    class Page:
        def __init__(self):
            self.closed = False
            self.response_handler = None

        def on(self, event, handler):
            if event == "response":
                self.response_handler = handler

        async def route(self, *args, **kwargs):
            return None

        async def goto(self, *args, **kwargs):
            return None

        async def wait_for_function(self, *args, **kwargs):
            return None

        async def evaluate(self, script):
            return ["https://v3-web.douyinvod.com/video/main.mp4?sig=abc"]

        async def close(self):
            self.closed = True

    page = Page()

    class Context:
        async def new_page(self):
            return page

    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        yield SimpleNamespace(
            context=Context(),
            page=None,
            created_page=False,
            user_agent="Mozilla/5.0 Edge",
        )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        fake_session,
    ):
        resolved = await resolve_douyin_media(
            "https://www.douyin.com/video/123"
        )

    assert resolved.url.startswith("https://v3-web.douyinvod.com/")
    assert resolved.headers["Referer"] == "https://www.douyin.com/video/123"
    assert resolved.headers["User-Agent"] == "Mozilla/5.0 Edge"
    assert page.closed is True


def test_candidate_score_prefers_douyin_cdn_before_generic_size():
    assert _candidate_score("https://v3-web.douyinvod.com/a", 10) > _candidate_score(
        "https://example.com/video.mp4", 10_000_000
    )
    assert _candidate_score(
        "https://v3-web.douyinvod.com/a?__vid=123", 10, "123"
    ) > _candidate_score(
        "https://v3-web.douyinvod.com/b?__vid=999", 10_000_000, "123"
    )


@pytest.mark.asyncio
async def test_resolver_waits_for_source_dom_media_before_unrelated_network_video():
    unrelated = "https://v3-web.douyinvod.com/other.mp4?__vid=999"
    expected = "https://v5-dy.zjcdn.com/source.mp4?__vid=123"

    class Response:
        url = unrelated
        headers = {"content-type": "video/mp4", "content-length": "999999"}

    class Page:
        def __init__(self):
            self.response_handler = None
            self.evaluations = 0
            self.waited = False

        def on(self, event, handler):
            if event == "response":
                self.response_handler = handler

        async def route(self, *args, **kwargs):
            return None

        async def goto(self, *args, **kwargs):
            self.response_handler(Response())

        async def evaluate(self, script):
            self.evaluations += 1
            return [] if self.evaluations == 1 else [expected]

        async def wait_for_function(self, *args, **kwargs):
            self.waited = True

        async def close(self):
            return None

    page = Page()

    class Context:
        async def new_page(self):
            return page

    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        yield SimpleNamespace(
            context=Context(),
            page=None,
            created_page=False,
            user_agent="Mozilla/5.0 Edge",
        )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        fake_session,
    ):
        resolved = await resolve_douyin_media("https://www.douyin.com/video/123")

    assert page.waited is True
    assert resolved.url == expected


@pytest.mark.asyncio
async def test_resolver_uses_partially_loaded_page_after_navigation_timeout():
    expected = "https://v5-dy.zjcdn.com/source.mp4?__vid=123"

    class Page:
        def on(self, *args, **kwargs):
            return None

        async def route(self, *args, **kwargs):
            return None

        async def goto(self, *args, **kwargs):
            raise PlaywrightTimeoutError("slow Douyin navigation")

        async def evaluate(self, script):
            return [expected]

        async def wait_for_function(self, *args, **kwargs):
            return None

        async def close(self):
            return None

    page = Page()

    class Context:
        async def new_page(self):
            return page

    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        yield SimpleNamespace(
            context=Context(),
            page=None,
            created_page=False,
            user_agent="Mozilla/5.0 Edge",
        )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        fake_session,
    ):
        resolved = await resolve_douyin_media("https://www.douyin.com/video/123")

    assert resolved.url == expected
