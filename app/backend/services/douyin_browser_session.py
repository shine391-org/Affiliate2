"""Shared authenticated Douyin CDP session primitives.

Affiliate2 never launches a second browser here.  The service attaches to the
user's already-running Edge/Chrome DevTools endpoint and borrows one existing
authenticated context.  Callers may create a short-lived page, but they must
close only pages they created; closing the CDP browser would close the user's
real browser.

Keeping this layer small is deliberate: discovery and authenticated media
resolution share one definition of "Douyin session ready" without importing a
second crawler/runtime.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from playwright.async_api import async_playwright

DOUYIN_HOST = "https://www.douyin.com"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"

SessionStateReader = Callable[[Any, Any], Awaitable[tuple[bool, dict[str, str], str | None]]]


def resolve_douyin_cdp_url(cdp_url: str | None = None) -> str:
    return (
        cdp_url
        or os.getenv("AFFILIATE2_DOUYIN_CDP_URL")
        or os.getenv("DOUYIN_CDP_URL")
        or DEFAULT_CDP_URL
    )


async def read_douyin_session_state(
    context,
    page,
) -> tuple[bool, dict[str, str], str | None]:
    """Read only the in-browser state needed by Douyin.

    Cookie values stay inside Playwright memory for the lifetime of the CDP
    operation.  Nothing is exported to disk.
    """
    try:
        local_storage = await page.evaluate("() => ({...window.localStorage})")
    except Exception:
        local_storage = {}

    cookies = await context.cookies(
        [DOUYIN_HOST, "https://douyin.com", "https://creator.douyin.com"]
    )
    cookie_dict = {
        str(cookie.get("name")): str(cookie.get("value") or "")
        for cookie in cookies
        if cookie.get("name")
    }

    logged_in = (
        str(local_storage.get("HasUserLogin", "")) == "1"
        or cookie_dict.get("LOGIN_STATUS") == "1"
    )
    return logged_in, cookie_dict, local_storage.get("xmst")


async def pick_authenticated_context_and_page(
    browser,
    *,
    state_reader: SessionStateReader = read_douyin_session_state,
    navigation_timeout_ms: int = 30_000,
):
    """Return (context, page, created_page).

    Existing Douyin pages are checked first, so the common path does no
    navigation at all.  Contexts without a Douyin tab get one bounded probe
    page; failed probes are closed immediately.
    """
    contexts = browser.contexts
    if not contexts:
        raise PermissionError(
            "CDP browser has no reusable context. Start Edge/Chrome with "
            "--remote-debugging-port=9222 and log in to Douyin."
        )

    first_douyin_page = None
    contexts_without_douyin_page = []
    for context in contexts:
        douyin_pages = [
            page for page in context.pages
            if "douyin.com" in (page.url or "")
        ]
        if not douyin_pages:
            contexts_without_douyin_page.append(context)
            continue
        if first_douyin_page is None:
            first_douyin_page = (context, douyin_pages[0])
        for page in douyin_pages:
            try:
                logged_in, _, _ = await state_reader(context, page)
            except Exception:
                logged_in = False
            if logged_in:
                return context, page, False

    for context in contexts_without_douyin_page:
        page = None
        selected = False
        try:
            page = await context.new_page()
            await page.goto(
                DOUYIN_HOST,
                wait_until="domcontentloaded",
                timeout=navigation_timeout_ms,
            )
            logged_in, _, _ = await state_reader(context, page)
            if logged_in:
                selected = True
                return context, page, True
        except Exception:
            # One dead context must not make every other reusable context fail.
            pass
        finally:
            if page is not None and not selected:
                try:
                    await page.close()
                except Exception:
                    pass

    # Preserve a useful logged-out page so callers can report AUTH_REQUIRED
    # after checking the state themselves.
    if first_douyin_page is not None:
        context, page = first_douyin_page
        return context, page, False

    raise PermissionError(
        "No authenticated Douyin session found in any reusable CDP context."
    )


@dataclass
class DouyinCdpSession:
    context: Any
    page: Any
    cookie_dict: dict[str, str]
    ms_token: str | None
    user_agent: str
    created_page: bool
    cdp_url: str


@asynccontextmanager
async def open_douyin_cdp_session(
    cdp_url: str | None = None,
    *,
    connect_timeout_ms: int = 15_000,
    navigation_timeout_ms: int = 30_000,
) -> AsyncIterator[DouyinCdpSession]:
    """Attach to the existing browser and yield one authenticated session.

    Playwright's driver is scoped to this operation and is stopped afterwards,
    avoiding a resident helper process or stale event-loop state.  The real
    Edge/Chrome process is never closed.
    """
    selected_url = resolve_douyin_cdp_url(cdp_url)
    created_page = None

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.connect_over_cdp(
                selected_url,
                timeout=connect_timeout_ms,
            )
        except Exception as exc:
            raise PermissionError(
                f"Cannot connect to Douyin CDP at {selected_url}: {exc}. "
                "Start/login the configured Edge/Chrome profile first."
            ) from exc

        try:
            context, page, page_was_created = await pick_authenticated_context_and_page(
                browser,
                navigation_timeout_ms=navigation_timeout_ms,
            )
            created_page = page if page_was_created else None
            logged_in, cookie_dict, ms_token = await read_douyin_session_state(
                context, page
            )
            if not logged_in:
                raise PermissionError("Douyin browser session is not logged in.")

            try:
                user_agent = str(await page.evaluate("() => navigator.userAgent"))
            except Exception:
                user_agent = ""

            yield DouyinCdpSession(
                context=context,
                page=page,
                cookie_dict=cookie_dict,
                ms_token=ms_token,
                user_agent=user_agent,
                created_page=page_was_created,
                cdp_url=selected_url,
            )
        finally:
            if created_page is not None:
                try:
                    await created_page.close()
                except Exception:
                    pass
