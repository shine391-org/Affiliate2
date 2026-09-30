"""Exact-ID lookup must work without a caption and never bless a decoy."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.services import douyin_media_resolver as resolver
from backend.sources.douyin import DouyinProvider


def detail(ident="123", urls=None):
    return {"status_code": 0, "aweme_detail": {"aweme_id": ident, "video": {
        "play_addr": {"url_list": urls or ["https://v3.douyinvod.com/exact.mp4"]}
    }}}


@pytest.fixture
def browser(monkeypatch):
    class Page:
        closed = False
        handlers = {}
        url = "https://www.douyin.com/video/123"

        def on(self, event, callback):
            self.handlers[event] = callback

        async def route(self, *args):
            pass

        async def goto(self, *args, **kwargs):
            self.handlers["response"](SimpleNamespace(
                url="https://v3.douyinvod.com/decoy.mp4?__vid=999",
                headers={"content-type": "video/mp4"},
            ))

        async def evaluate(self, *args):
            return []

        async def wait_for_function(self, *args, **kwargs):
            pass

        async def close(self):
            self.closed = True

    page = Page()
    session = SimpleNamespace(page=page, context=SimpleNamespace(new_page=AsyncMock(return_value=page)),
                              created_page=False, user_agent="UA", cookie_dict={}, ms_token=None)

    @asynccontextmanager
    async def opened(*args):
        yield session

    monkeypatch.setattr(resolver, "open_douyin_cdp_session", opened)
    return page


@pytest.mark.asyncio
async def test_manual_url_exact_lookup_without_caption(browser, monkeypatch):
    lookup = AsyncMock(return_value=detail(urls=[
        "https://v3.douyinvod.com/exact.mp4",
        "https://v3.douyinvod.com/alternate.mp4",
        "https://v3.douyinvod.com/wrong.mp4?__vid=999",
    ]))
    monkeypatch.setattr(DouyinProvider, "_fetch_aweme_detail", lookup, raising=False)
    search = AsyncMock()
    monkeypatch.setattr(resolver, "_search_caption_candidates", search)
    result = await resolver.resolve_douyin_media(browser.url)
    lookup.assert_awaited_once_with(browser, "123")
    search.assert_not_awaited()
    assert browser.closed
    assert result.requested_aweme_id == result.candidate_aweme_id == "123"
    assert result.resolution_path == "exact_aweme"
    assert result.provenance == "exact_aweme:play_addr"
    assert len(result.alternates) == 1
    assert all(item.candidate_aweme_id == "123" for item in result.alternates)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [detail("456"), detail(urls=["https://v3.douyinvod.com/a?__vid=999"]),
                                     {"aweme_detail": None, "filter_detail": {"filter_reason": "unavailable"}}])
async def test_exact_mismatch_or_missing_fails_closed_with_actual_stages(browser, monkeypatch, payload):
    lookup = AsyncMock(return_value=payload)
    monkeypatch.setattr(DouyinProvider, "_fetch_aweme_detail", lookup, raising=False)
    with pytest.raises(RuntimeError) as exc:
        await resolver.resolve_douyin_media(browser.url)
    text = str(exc.value)
    assert "requested_aweme_id=123" in text and "999" in text
    assert "resolution_path=dom_then_network_then_exact_aweme" in text
    assert "then_search" not in text
    assert "exact_aweme=" in text
    if (payload.get("aweme_detail") or {}).get("aweme_id") == "456":
        assert "456" in text
    assert browser.closed


@pytest.mark.asyncio
async def test_exact_failure_continues_caption_fallback(browser, monkeypatch):
    events = []

    async def lookup(*args):
        events.append("exact")
        raise PermissionError("access restricted")

    async def search(*args, **kwargs):
        events.append("search")
        return [resolver._MediaCandidate("https://v3.douyinvod.com/a", url_aweme_ids=("123",),
                                         provenance="search:play_addr", resolution_path="search")]

    monkeypatch.setattr(DouyinProvider, "_fetch_aweme_detail", lookup, raising=False)
    monkeypatch.setattr(resolver, "_search_caption_candidates", search)
    result = await resolver.resolve_douyin_media(browser.url, title_hint="caption")
    assert events == ["exact", "search"]
    assert result.candidate_aweme_id == "123"


@pytest.mark.asyncio
async def test_exact_timeout_reports_failure_not_unattempted_search(browser, monkeypatch):
    monkeypatch.setattr(DouyinProvider, "_fetch_aweme_detail", AsyncMock(side_effect=TimeoutError()), raising=False)
    with pytest.raises(RuntimeError, match="exact_aweme=TimeoutError"):
        await resolver.resolve_douyin_media(browser.url, title_hint="   ")


@pytest.mark.asyncio
async def test_provider_uses_page_fetch_with_exact_id():
    page = SimpleNamespace(evaluate=AsyncMock(return_value={"status": 200, "payload": detail()}))
    result = await DouyinProvider()._fetch_aweme_detail(page, "123")
    assert result == detail()
    script, params = page.evaluate.call_args.args
    assert "fetch(" in script and "AbortController" in script
    assert "aweme_id=123" in params["url"]
    assert "credentials" in script


@pytest.mark.asyncio
@pytest.mark.parametrize("response, error", [
    ({"status": 403, "payload": {}}, PermissionError),
    ({"status": 200, "payload": None}, RuntimeError),
    ({"status": 200, "payload": {"status_code": 8}}, PermissionError),
    ({"status": 200, "payload": {"status_code": 1234}}, RuntimeError),
])
async def test_provider_detail_errors(response, error):
    page = SimpleNamespace(evaluate=AsyncMock(return_value=response))
    with pytest.raises(error):
        await DouyinProvider()._fetch_aweme_detail(page, "123")


@pytest.mark.asyncio
async def test_search_decoy_ids_are_reported_and_do_not_rescue_payload_conflict(browser, monkeypatch):
    monkeypatch.setattr(DouyinProvider, "_fetch_aweme_detail", AsyncMock(return_value={"aweme_detail": None}))
    monkeypatch.setattr(DouyinProvider, "_fetch_search_page", AsyncMock(return_value={
        "data": [{"aweme_info": detail("456", ["https://v3.douyinvod.com/a?__vid=123"])["aweme_detail"]}]
    }))
    with pytest.raises(RuntimeError) as exc:
        await resolver.resolve_douyin_media(browser.url, title_hint="caption")
    text = str(exc.value)
    assert "456" in text
    assert "resolution_path=dom_then_network_then_exact_aweme_then_search" in text


@pytest.mark.asyncio
async def test_search_access_error_is_reported(browser, monkeypatch):
    monkeypatch.setattr(DouyinProvider, "_fetch_aweme_detail", AsyncMock(return_value={"aweme_detail": None}))
    monkeypatch.setattr(DouyinProvider, "_fetch_search_page", AsyncMock(side_effect=PermissionError()))
    with pytest.raises(RuntimeError, match="search=PermissionError"):
        await resolver.resolve_douyin_media(browser.url, title_hint="caption")


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["dom", "network"])
async def test_exact_lookup_not_run_after_earlier_exact_match(browser, monkeypatch, stage):
    if stage == "dom":
        browser.evaluate = AsyncMock(return_value=["https://v3.douyinvod.com/a?__vid=123"])
    else:
        async def goto(*args, **kwargs):
            browser.handlers["response"](SimpleNamespace(
                url="https://v3.douyinvod.com/a?__vid=123", headers={"content-type": "video/mp4"},
            ))
        browser.goto = goto
    lookup = AsyncMock()
    monkeypatch.setattr(DouyinProvider, "_fetch_aweme_detail", lookup)
    result = await resolver.resolve_douyin_media(browser.url)
    assert result.resolution_path == stage
    lookup.assert_not_awaited()


@pytest.mark.parametrize("url", [
    "https://v3.douyinvod.com/a?__vid=123&__vid=999",
    "https://v3.douyinvod.com/video/999?__vid=123",
])
def test_conflicting_url_ids_cannot_be_rescued_by_exact_payload(url):
    candidates = resolver._aweme_play_candidates(detail(urls=[url])["aweme_detail"])
    assert resolver._verified_ordered(candidates, "123") == []
