"""Resolve an authenticated Douyin page to one short-lived media URL.

This is intentionally NOT a downloader.  It borrows the existing authenticated
CDP browser, resolves the playable media URL, closes the temporary page, and
hands the URL back to the canonical yt-dlp pipeline.

No cookie database access, cookie export, second browser, or MediaCrawler
subprocess is involved.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from backend.services.douyin_browser_session import open_douyin_cdp_session


@dataclass(frozen=True)
class ResolvedDouyinMedia:
    url: str
    headers: dict[str, str]
    source_url: str


def _is_http_media_url(value: str | None) -> bool:
    if not value:
        return False
    url = str(value).strip()
    if not url.startswith(("http://", "https://")):
        return False
    lowered = url.lower()
    return "uuu_265" not in lowered


def _candidate_score(url: str, content_length: int = 0) -> tuple[int, int]:
    """Prefer known Douyin video CDNs, then the largest observed response."""
    host = (urlsplit(url).hostname or "").lower()
    strong = any(
        marker in host
        for marker in ("douyinvod", "zjcdn", "bytecdn", "byteoversea", "aweme")
    )
    return (1 if strong else 0, max(int(content_length or 0), 0))


async def resolve_douyin_media(
    source_url: str,
    *,
    cdp_url: str | None = None,
    navigation_timeout_ms: int = 20_000,
    media_wait_timeout_ms: int = 8_000,
) -> ResolvedDouyinMedia:
    """Resolve one Douyin video page using the already-running CDP browser.

    The temporary page blocks images/fonts to keep CPU, memory and bandwidth
    low while preserving layout. Scripts/XHR/media stay enabled because Douyin
    needs them to produce the signed stream URL.
    """
    source_url = str(source_url or "").strip()
    if "douyin.com/" not in source_url.lower() and "iesdouyin.com/" not in source_url.lower():
        raise ValueError("Douyin media resolver only accepts Douyin URLs")

    async with open_douyin_cdp_session(cdp_url) as session:
        page = await session.context.new_page()
        network_candidates: list[tuple[str, int]] = []

        def _capture(response):
            try:
                headers = response.headers or {}
                content_type = str(headers.get("content-type") or "").lower()
                candidate = str(response.url or "")
                if content_type.startswith("video/") and _is_http_media_url(candidate):
                    try:
                        size = int(headers.get("content-length") or 0)
                    except (TypeError, ValueError):
                        size = 0
                    network_candidates.append((candidate, size))
            except Exception:
                pass

        async def _lightweight_route(route):
            request = route.request
            if request.resource_type in {"image", "font"}:
                await route.abort()
            else:
                await route.continue_()

        page.on("response", _capture)
        await page.route("**/*", _lightweight_route)

        try:
            await page.goto(
                source_url,
                wait_until="domcontentloaded",
                timeout=navigation_timeout_ms,
            )
            try:
                await page.wait_for_function(
                    """() => Array.from(document.querySelectorAll('video')).some(
                        v => (v.currentSrc || v.src || '').startsWith('http')
                    )""",
                    timeout=media_wait_timeout_ms,
                )
            except Exception:
                # Network capture below is an equally valid resolution path.
                pass

            dom_urls = await page.evaluate(
                """() => Array.from(document.querySelectorAll('video')).flatMap(v => {
                    const urls = [v.currentSrc, v.src];
                    for (const s of v.querySelectorAll('source')) urls.push(s.src);
                    return urls.filter(Boolean);
                })"""
            )
            for candidate in dom_urls or []:
                if _is_http_media_url(candidate):
                    return ResolvedDouyinMedia(
                        url=str(candidate),
                        headers={
                            **({"User-Agent": session.user_agent} if session.user_agent else {}),
                            "Referer": source_url,
                        },
                        source_url=source_url,
                    )

            if network_candidates:
                candidate, _ = max(
                    network_candidates,
                    key=lambda item: _candidate_score(item[0], item[1]),
                )
                return ResolvedDouyinMedia(
                    url=candidate,
                    headers={
                        **({"User-Agent": session.user_agent} if session.user_agent else {}),
                        "Referer": source_url,
                    },
                    source_url=source_url,
                )

            raise RuntimeError("Authenticated Douyin page exposed no playable media URL")
        finally:
            try:
                await page.close()
            except Exception:
                pass
