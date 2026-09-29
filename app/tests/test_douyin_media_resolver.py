"""The Douyin CDP resolver stays a resolver, not a second downloader."""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from backend.services.douyin_media_resolver import (
    _candidate_score,
    _normalize_dom_candidate,
    _verified_candidate,
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


def test_canonical_aweme_id_reads_short_link_object_id():
    """v.douyin.com redirects to a page whose object_id carries the real id."""
    url = (
        "https://www.douyin.com/jingxuan?app=aweme&iid=&is_qrcode=1"
        "&object_id=0_3638929914889127993_3638929914889127993_0"
        "&redirect_from=iesdouyin&schema_type=20"
    )
    assert canonical_aweme_id(url) == "3638929914889127993"
    # the bare short link itself still names nothing
    assert canonical_aweme_id("https://v.douyin.com/iRNBho6G/") is None


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


class _Frame:
    """Minimal stand-in for a Playwright main frame."""

    parent_frame = None

    def __init__(self, url):
        self.url = url


class _RedirectPage:
    """Resolver tab that replays a full redirect chain on framenavigated."""

    def __init__(self, chain, dom_payloads, network=None):
        self.chain = list(chain)
        self.url = chain[-1] if chain else ""
        self._dom = list(dom_payloads)
        self._network = list(network or [])
        self._handlers = {}

    def on(self, event, handler):
        self._handlers.setdefault(event, []).append(handler)

    async def route(self, *args, **kwargs):
        return None

    async def goto(self, *args, **kwargs):
        for hop in self.chain:
            for handler in self._handlers.get("framenavigated", []):
                handler(_Frame(hop))
        for response in self._network:
            for handler in self._handlers.get("response", []):
                handler(response)
        return None

    async def evaluate(self, script):
        if "og:url" in script:
            return ""
        if self._dom:
            return self._dom.pop(0)
        return []

    async def wait_for_function(self, *args, **kwargs):
        return None

    async def close(self):
        return None


def _page_session(page):
    class Context:
        async def new_page(self):
            return page

    @asynccontextmanager
    async def fake_session(*args, **kwargs):
        yield SimpleNamespace(
            context=Context(), page=None, created_page=False, user_agent="UA"
        )

    return fake_session


class _VideoResponse:
    def __init__(self, url, size="999999"):
        self.url = url
        self.headers = {"content-type": "video/mp4", "content-length": size}


@pytest.mark.asyncio
async def test_short_url_keeps_first_canonical_aweme_across_later_redirect():
    """BLOCKER 1: /video/123 -> /video/999 must not re-point the request at 999."""
    wrong = "https://v3-web.douyinvod.com/other.mp4?__vid=999"
    right = "https://v5-dy.zjcdn.com/main.mp4?__vid=123"
    page = _RedirectPage(
        chain=[
            "https://v.douyin.com/iAbCdEf/",
            "https://www.douyin.com/video/123",
            "https://www.douyin.com/video/999",
        ],
        # The landed /video/999 page only ever plays 999.
        dom_payloads=[[wrong], [wrong]],
        network=[_VideoResponse(wrong)],
    )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        _page_session(page),
    ):
        with pytest.raises(RuntimeError, match=r"requested_aweme_id=123.*999"):
            await resolve_douyin_media("https://v.douyin.com/iAbCdEf/")


@pytest.mark.asyncio
async def test_short_url_redirect_chain_accepts_media_of_the_first_canonical_aweme():
    right = "https://v5-dy.zjcdn.com/main.mp4?__vid=123"
    page = _RedirectPage(
        chain=[
            "https://v.douyin.com/iAbCdEf/",
            "https://www.douyin.com/video/123",
            "https://www.douyin.com/video/999",
        ],
        dom_payloads=[[right]],
    )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        _page_session(page),
    ):
        resolved = await resolve_douyin_media("https://v.douyin.com/iAbCdEf/")

    assert resolved.requested_aweme_id == "123"
    assert resolved.candidate_aweme_id == "123"
    assert resolved.url == right


@pytest.mark.asyncio
async def test_dom_association_cannot_override_a_conflicting_media_url_identity():
    """BLOCKER 2: URL __vid=999 + DOM ['123'] must be rejected."""
    page = _RedirectPage(
        chain=["https://www.douyin.com/video/123"],
        dom_payloads=[[
            {"url": "https://v3-web.douyinvod.com/other.mp4?__vid=999",
             "awemeIds": ["123"], "provenance": "dom:video[0]"},
        ]],
    )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        _page_session(page),
    ):
        with pytest.raises(RuntimeError, match=r"requested_aweme_id=123.*999"):
            await resolve_douyin_media("https://www.douyin.com/video/123")


@pytest.mark.asyncio
async def test_dom_association_establishes_identity_when_url_has_none():
    """Positive: the URL declares no aweme, so the DOM association may prove it."""
    anonymous = "https://v3-web.douyinvod.com/main.mp4?sig=abc"
    page = _RedirectPage(
        chain=["https://www.douyin.com/video/123"],
        dom_payloads=[[
            {"url": anonymous, "awemeIds": ["123"], "provenance": "dom:video[0]"},
        ]],
    )

    with patch(
        "backend.services.douyin_media_resolver.open_douyin_cdp_session",
        _page_session(page),
    ):
        resolved = await resolve_douyin_media("https://www.douyin.com/video/123")

    assert resolved.url == anonymous
    assert resolved.requested_aweme_id == "123"
    assert resolved.candidate_aweme_id == "123"


def test_candidate_never_claims_an_id_that_contradicts_its_media_url():
    candidate = _normalize_dom_candidate({
        "url": "https://v3-web.douyinvod.com/other.mp4?__vid=999",
        "awemeIds": ["123"],
    })
    assert candidate is not None

    assert candidate.url_aweme_ids == ("999",)
    assert candidate.dom_aweme_ids == ("123",)
    # URL evidence is authoritative and conflicts, so the candidate cannot pass.
    assert candidate.matches("123") is False
    assert candidate.matches("999") is True
    # identity_ids must expose the authoritative value, never the DOM rescue.
    assert "123" not in candidate.identity_ids


def test_candidate_prefers_url_identity_over_conflicting_dom_association():
    candidate = _normalize_dom_candidate({
        "url": "https://v5-dy.zjcdn.com/main.mp4?__vid=123",
        "awemeIds": ["999"],
    })
    assert candidate is not None

    assert candidate.matches("123") is True
    assert candidate.matches("999") is False
    assert candidate.identity_ids == ("123",)


def test_resolved_candidate_aweme_id_is_derived_from_the_media_url_evidence():
    """A resolved media URL declaring __vid=999 can never be reported as 123."""
    candidate = _normalize_dom_candidate({
        "url": "https://v3-web.douyinvod.com/other.mp4?__vid=999",
        "awemeIds": ["123"],
    })
    assert candidate is not None

    # The gate rejects it before _resolved() is ever reachable, and the value
    # _resolved would derive for such a candidate is 999, never 123.
    assert _verified_candidate([candidate], "123") is None
    assert candidate.identity_ids[0] == "999"



def test_ambiguous_dom_identity_proves_nothing():
    candidate = _normalize_dom_candidate({
        "url": "https://v3-web.douyinvod.com/main.mp4?sig=abc",
        "awemeIds": ["123", "999"],
        "provenance": "dom:shared-feed-container",
    })
    assert candidate is not None
    assert candidate.url_aweme_ids == ()
    assert candidate.dom_aweme_ids == ()
    assert candidate.matches("123") is False
    assert candidate.matches("999") is False
