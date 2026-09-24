"""The Douyin CDP resolver stays a resolver, not a second downloader."""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

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
