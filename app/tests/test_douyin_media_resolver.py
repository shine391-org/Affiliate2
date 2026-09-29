"""The Douyin CDP resolver stays a resolver, not a second downloader."""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from backend.services.douyin_media_resolver import (
    _candidate_score,
    canonical_aweme_id,
    resolve_douyin_media,
)


def test_canonical_aweme_id_reads_canonical_share_and_modal_urls():
    assert canonical_aweme_id("https://www.douyin.com/video/123") == "123"
    assert canonical_aweme_id("https://www.douyin.com/video/123/") == "123"
    assert canonical_aweme_id("https://www.iesdouyin.com/share/video/123/?region=VN") == "123"
    assert canonical_aweme_id("https://www.douyin.com/discover?modal_id=123") == "123"
    assert canonical_aweme_id("https://v.douyin.com/iAbCdEf/") is None
    assert canonical_aweme_id("https://www.douyin.com/user/SEC_abc") is None


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
            return ["https://v3-web.douyinvod.com/video/main.mp4?sig=abc&__vid=123"]

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


@pytest.mark.asyncio
async def test_resolver_rejects_unrelated_dom_video_for_requested_aweme():
    unrelated = "https://v3-web.douyinvod.com/other.mp4?__vid=999"

    class Page:
        def on(self, *args, **kwargs):
            return None

        async def route(self, *args, **kwargs):
            return None

        async def goto(self, *args, **kwargs):
            return None

        async def evaluate(self, script):
            return [unrelated]

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
            context=Context(), page=None, created_page=False, user_agent="UA"
        )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        fake_session,
    ):
        with pytest.raises(RuntimeError, match=r"requested_aweme_id=123.*999"):
            await resolve_douyin_media("https://www.douyin.com/video/123")


@pytest.mark.asyncio
async def test_resolver_accepts_dom_video_verified_for_requested_aweme():
    expected = "https://v3-web.douyinvod.com/main.mp4?__vid=123"

    class Page:
        def on(self, *args, **kwargs):
            return None

        async def route(self, *args, **kwargs):
            return None

        async def goto(self, *args, **kwargs):
            return None

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
            context=Context(), page=None, created_page=False, user_agent="UA"
        )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        fake_session,
    ):
        resolved = await resolve_douyin_media("https://www.douyin.com/video/123")

    assert resolved.url == expected


@pytest.mark.asyncio
async def test_resolver_rejects_unrelated_network_candidate_when_aweme_is_known():
    unrelated = "https://v3-web.douyinvod.com/other.mp4?__vid=999"

    class Response:
        url = unrelated
        headers = {"content-type": "video/mp4", "content-length": "999999"}

    class Page:
        def __init__(self):
            self.response_handler = None

        def on(self, event, handler):
            if event == "response":
                self.response_handler = handler

        async def route(self, *args, **kwargs):
            return None

        async def goto(self, *args, **kwargs):
            self.response_handler(Response())

        async def evaluate(self, script):
            return []

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
            context=Context(), page=None, created_page=False, user_agent="UA"
        )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        fake_session,
    ):
        with pytest.raises(RuntimeError, match=r"requested_aweme_id=123.*999"):
            await resolve_douyin_media("https://www.douyin.com/video/123")


def _short_url_session(page_cls, landed_url):
    """Session whose resolver tab lands on `landed_url` after a short link."""
    page = page_cls(landed_url)

    class Context:
        async def new_page(self):
            return page

    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        yield SimpleNamespace(
            context=Context(), page=None, created_page=False, user_agent="UA"
        )

    return fake_session, page


@pytest.mark.asyncio
async def test_short_url_redirect_proves_aweme_and_accepts_its_media():
    expected = "https://v3-web.douyinvod.com/main.mp4?__vid=123"

    class Page:
        def __init__(self, landed_url):
            self.url = landed_url

        def on(self, *args, **kwargs):
            return None

        async def route(self, *args, **kwargs):
            return None

        async def goto(self, *args, **kwargs):
            return None

        async def evaluate(self, script):
            if "og:url" in script:
                return ""
            return [expected]

        async def wait_for_function(self, *args, **kwargs):
            return None

        async def close(self):
            return None

    fake_session, _page = _short_url_session(
        Page, "https://www.iesdouyin.com/share/video/123/?region=VN"
    )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        fake_session,
    ):
        resolved = await resolve_douyin_media("https://v.douyin.com/iAbCdEf/")

    assert resolved.url == expected
    assert resolved.requested_aweme_id == "123"
    assert resolved.candidate_aweme_id == "123"


@pytest.mark.asyncio
async def test_short_url_never_accepts_unrelated_aweme_exposed_by_the_page():
    unrelated = "https://v3-web.douyinvod.com/other.mp4?__vid=999"

    class Response:
        url = unrelated
        headers = {"content-type": "video/mp4", "content-length": "999999"}

    class Page:
        def __init__(self, landed_url):
            self.url = landed_url
            self.response_handler = None

        def on(self, event, handler):
            if event == "response":
                self.response_handler = handler

        async def route(self, *args, **kwargs):
            return None

        async def goto(self, *args, **kwargs):
            self.response_handler(Response())

        async def evaluate(self, script):
            if "og:url" in script:
                return ""
            return [unrelated]

        async def wait_for_function(self, *args, **kwargs):
            return None

        async def close(self):
            return None

    fake_session, _page = _short_url_session(
        Page, "https://www.douyin.com/video/123"
    )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        fake_session,
    ):
        with pytest.raises(RuntimeError, match=r"requested_aweme_id=123.*999"):
            await resolve_douyin_media("https://v.douyin.com/iAbCdEf/")


@pytest.mark.asyncio
async def test_unresolvable_short_url_fails_closed_even_with_playable_media():
    playable = "https://v3-web.douyinvod.com/main.mp4?__vid=999"

    class Page:
        def __init__(self, landed_url):
            self.url = landed_url

        def on(self, *args, **kwargs):
            return None

        async def route(self, *args, **kwargs):
            return None

        async def goto(self, *args, **kwargs):
            return None

        async def evaluate(self, script):
            if "og:url" in script:
                return "https://www.douyin.com/user/SEC_abc"
            return [playable]

        async def wait_for_function(self, *args, **kwargs):
            return None

        async def close(self):
            return None

    fake_session, _page = _short_url_session(
        Page, "https://www.douyin.com/user/SEC_abc"
    )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        fake_session,
    ):
        with pytest.raises(RuntimeError, match="share/short URL"):
            await resolve_douyin_media("https://v.douyin.com/iAbCdEf/")
