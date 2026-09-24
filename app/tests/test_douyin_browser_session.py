"""Focused contracts for the shared Douyin CDP session layer."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.services.douyin_browser_session import open_douyin_cdp_session


@pytest.mark.asyncio
async def test_shared_session_closes_only_the_probe_page_not_real_browser():
    class Page:
        def __init__(self):
            self.url = ""
            self.closed = False

        async def goto(self, url, **kwargs):
            self.url = url

        async def evaluate(self, expression):
            if "localStorage" in expression:
                return {"HasUserLogin": "1", "xmst": "token"}
            if "navigator.userAgent" in expression:
                return "Mozilla/5.0 Edge"
            return None

        async def close(self):
            self.closed = True

    page = Page()

    class Context:
        pages = []

        async def new_page(self):
            return page

        async def cookies(self, urls):
            return [{"name": "LOGIN_STATUS", "value": "1", "domain": ".douyin.com"}]

    context = Context()

    class Browser:
        contexts = [context]
        closed = False

        async def close(self):
            self.closed = True

    browser = Browser()

    class Chromium:
        async def connect_over_cdp(self, *args, **kwargs):
            return browser

    class Playwright:
        chromium = Chromium()

    class PlaywrightCM:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *args):
            return False

    with patch(
        "backend.services.douyin_browser_session.async_playwright",
        return_value=PlaywrightCM(),
    ):
        async with open_douyin_cdp_session("http://127.0.0.1:9222") as session:
            assert session.context is context
            assert session.page is page
            assert session.created_page is True
            assert session.user_agent == "Mozilla/5.0 Edge"

    assert page.closed is True
    assert browser.closed is False
