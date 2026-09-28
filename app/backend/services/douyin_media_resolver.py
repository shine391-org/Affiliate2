"""Resolve an authenticated Douyin page to one short-lived media URL.

This is intentionally NOT a downloader.  It borrows the existing authenticated
CDP browser, resolves the playable media URL, closes the temporary page, and
hands the URL back to the canonical yt-dlp pipeline.

No cookie database access, cookie export, second browser, or MediaCrawler
subprocess is involved.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from backend.services.douyin_browser_session import open_douyin_cdp_session

DOUYIN_SEARCH_PAGE_SIZE = 18


@dataclass(frozen=True)
class ResolvedDouyinMedia:
    url: str
    headers: dict[str, str]
    source_url: str
    requested_aweme_id: str | None = None
    candidate_aweme_id: str | None = None
    provenance: str = ""
    resolution_path: str = ""


@dataclass(frozen=True)
class _MediaCandidate:
    url: str
    content_length: int = 0
    aweme_ids: tuple[str, ...] = ()
    provenance: str = ""
    resolution_path: str = ""


def _is_http_media_url(value: str | None) -> bool:
    if not value:
        return False
    url = str(value).strip()
    if not url.startswith(("http://", "https://")):
        return False
    lowered = url.lower()
    return "uuu_265" not in lowered


def _candidate_score(
    url: str,
    content_length: int = 0,
    source_video_id: str | None = None,
) -> tuple[int, int, int]:
    """Prefer the requested video, then known Douyin CDNs, then response size."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    candidate_video_ids = parse_qs(parsed.query).get("__vid", [])
    source_match = bool(source_video_id and source_video_id in candidate_video_ids)
    strong = any(
        marker in host
        for marker in ("douyinvod", "zjcdn", "bytecdn", "byteoversea", "aweme")
    )
    return (
        1 if source_match else 0,
        1 if strong else 0,
        max(int(content_length or 0), 0),
    )


def _aweme_ids_from_url(url: str) -> tuple[str, ...]:
    parsed = urlsplit(str(url or ""))
    values = [
        str(value).strip()
        for value in parse_qs(parsed.query).get("__vid", [])
        if str(value).strip().isdigit()
    ]
    path_match = re.search(r"/video/(\d+)(?:/|$)", parsed.path)
    if path_match:
        values.append(path_match.group(1))
    return tuple(dict.fromkeys(values))


def _normalize_dom_candidate(raw) -> _MediaCandidate | None:
    if isinstance(raw, str):
        url = raw
        dom_ids: list[str] = []
        provenance = "dom:video-source"
    elif isinstance(raw, dict):
        url = str(raw.get("url") or "")
        dom_ids = [
            str(value).strip()
            for value in (raw.get("awemeIds") or [])
            if str(value).strip().isdigit()
        ]
        provenance = str(raw.get("provenance") or "dom:video-source")
    else:
        return None
    if not _is_http_media_url(url):
        return None
    return _MediaCandidate(
        url=url,
        aweme_ids=tuple(dict.fromkeys([*_aweme_ids_from_url(url), *dom_ids])),
        provenance=provenance,
        resolution_path="dom",
    )


def _verified_candidate(
    candidates: list[_MediaCandidate],
    expected_aweme_id: str | None,
) -> _MediaCandidate | None:
    if not candidates:
        return None
    if expected_aweme_id:
        candidates = [
            candidate
            for candidate in candidates
            if expected_aweme_id in candidate.aweme_ids
        ]
        if not candidates:
            return None
    return max(
        candidates,
        key=lambda candidate: _candidate_score(
            candidate.url, candidate.content_length, expected_aweme_id
        ),
    )


def _identity_observation(
    expected_aweme_id: str | None,
    observed: list[_MediaCandidate],
) -> str:
    """One forensic string describing why identity could not be proven."""
    if not expected_aweme_id:
        return "Authenticated Douyin page exposed no playable media URL"
    observed_ids = sorted({
        aweme_id
        for item in observed
        for aweme_id in item.aweme_ids
    })
    provenance = sorted({item.provenance for item in observed if item.provenance})
    return (
        "Could not verify requested Douyin video identity: "
        f"requested_aweme_id={expected_aweme_id}; "
        f"observed_candidate_ids={observed_ids or ['unknown']}; "
        f"provenance={provenance or ['none']}; "
        "resolution_path=dom_then_network_then_search"
    )


def _caption_keywords(title_hint: str) -> tuple[str, ...]:
    """Longest-first caption probes for the exact-identity search fallback."""
    caption = " ".join(str(title_hint or "").split())
    if not caption:
        return ()
    keywords = [caption]
    if len(caption) > 16:
        keywords.append(caption[:16])
    return tuple(dict.fromkeys(keywords))


def _aweme_play_candidates(aweme: dict) -> list[_MediaCandidate]:
    """Signed CDN play URLs declared by one search-API aweme object."""
    video = aweme.get("video") or {}
    candidates: list[_MediaCandidate] = []
    for field in ("play_addr_h264", "play_addr", "play_addr_265", "download_addr"):
        block = video.get(field)
        if not isinstance(block, dict):
            continue
        height = block.get("height") or 0
        for url in block.get("url_list") or []:
            if not _is_http_media_url(url):
                continue
            candidates.append(_MediaCandidate(
                url=url,
                content_length=int(height or 0),
                aweme_ids=_aweme_ids_from_url(url),
                provenance="search:play_addr",
                resolution_path="search",
            ))
    return candidates


async def _search_caption_candidates(
    title_hint: str,
    expected_aweme_id: str,
    *,
    cdp_url: str | None,
) -> list[_MediaCandidate]:
    """Ask the canonical provider for this exact aweme by caption.

    Douyin's /video/<id> page regularly serves a *different* aweme to an
    authenticated session, so that page's own player is not trustworthy. The
    search API returns the aweme object keyed by its own id, which is the only
    source here able to prove exact identity.
    """
    from backend.sources.douyin import DouyinProvider

    provider = DouyinProvider(cdp_url=cdp_url)
    found: list[_MediaCandidate] = []
    async with open_douyin_cdp_session(cdp_url) as session:
        for keyword in _caption_keywords(title_hint):
            try:
                payload = await provider._fetch_search_page(
                    session.context,
                    session.page,
                    session.cookie_dict,
                    session.ms_token,
                    keyword,
                    0,
                    "",
                    DOUYIN_SEARCH_PAGE_SIZE,
                )
            except Exception:
                continue
            for entry in payload.get("data") or []:
                aweme = entry.get("aweme_info") if isinstance(entry, dict) else None
                if not isinstance(aweme, dict):
                    continue
                if str(aweme.get("aweme_id") or "") != expected_aweme_id:
                    continue
                for candidate in _aweme_play_candidates(aweme):
                    found.append(_MediaCandidate(
                        url=candidate.url,
                        content_length=candidate.content_length,
                        aweme_ids=(expected_aweme_id,),
                        provenance=candidate.provenance,
                        resolution_path=candidate.resolution_path,
                    ))
            if found:
                break
    return found


async def resolve_douyin_media(
    source_url: str,
    *,
    cdp_url: str | None = None,
    navigation_timeout_ms: int = 20_000,
    media_wait_timeout_ms: int = 15_000,
    title_hint: str = "",
) -> ResolvedDouyinMedia:
    """Resolve one Douyin video page using the already-running CDP browser.

    The temporary page blocks images/fonts to keep CPU, memory and bandwidth
    low while preserving layout. Scripts/XHR/media stay enabled because Douyin
    needs them to produce the signed stream URL.
    """
    source_url = str(source_url or "").strip()
    if "douyin.com/" not in source_url.lower() and "iesdouyin.com/" not in source_url.lower():
        raise ValueError("Douyin media resolver only accepts Douyin URLs")
    source_path_id = urlsplit(source_url).path.rstrip("/").rsplit("/", 1)[-1]
    source_video_id = source_path_id if source_path_id.isdigit() else None

    async with open_douyin_cdp_session(cdp_url) as session:
        # If the shared session had to create a probe page, reuse that page
        # instead of opening a second tab. Existing user tabs are never
        # navigated; in that common case we create one short-lived resolver tab.
        reuse_probe_page = bool(session.created_page)
        page = session.page if reuse_probe_page else await session.context.new_page()
        close_page_here = not reuse_probe_page
        network_candidates: list[_MediaCandidate] = []

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
                    network_candidates.append(_MediaCandidate(
                        url=candidate,
                        content_length=size,
                        aweme_ids=_aweme_ids_from_url(candidate),
                        provenance="network:video-response",
                        resolution_path="network",
                    ))
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
            try:
                await page.goto(
                    source_url,
                    wait_until="domcontentloaded",
                    timeout=navigation_timeout_ms,
                )
            except PlaywrightTimeoutError:
                # Douyin can keep domcontentloaded pending while the visible
                # video and signed media URL are already usable.
                pass

            async def _dom_media_urls():
                return await page.evaluate(
                    """() => Array.from(document.querySelectorAll('video')).flatMap((v, index) => {
                        const urls = [v.currentSrc, v.src];
                        for (const s of v.querySelectorAll('source')) urls.push(s.src);
                        const awemeIds = new Set();
                        let node = v;
                        for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                            for (const attr of Array.from(node.attributes || [])) {
                                const text = String(attr.value || '');
                                for (const match of text.matchAll(/(?:\/video\/|aweme[_-]?id[=:"']+)(\d+)/gi)) {
                                    awemeIds.add(match[1]);
                                }
                            }
                            for (const link of node.querySelectorAll?.('a[href*="/video/"]') || []) {
                                const match = String(link.href || '').match(/\/video\/(\d+)/);
                                if (match) awemeIds.add(match[1]);
                            }
                        }
                        return urls.filter(Boolean).map(url => ({
                            url,
                            awemeIds: Array.from(awemeIds),
                            provenance: `dom:video[${index}]`,
                        }));
                    })"""
                )

            def _resolved(candidate: _MediaCandidate) -> ResolvedDouyinMedia:
                return ResolvedDouyinMedia(
                    url=candidate.url,
                    headers={
                        **({"User-Agent": session.user_agent} if session.user_agent else {}),
                        "Referer": source_url,
                    },
                    source_url=source_url,
                    requested_aweme_id=source_video_id,
                    candidate_aweme_id=(
                        source_video_id
                        if source_video_id and source_video_id in candidate.aweme_ids
                        else candidate.aweme_ids[0] if candidate.aweme_ids else None
                    ),
                    provenance=candidate.provenance,
                    resolution_path=candidate.resolution_path,
                )

            observed: list[_MediaCandidate] = []

            def _read_dom_candidates(raw_candidates) -> list[_MediaCandidate]:
                candidates = []
                for raw in raw_candidates or []:
                    candidate = _normalize_dom_candidate(raw)
                    if candidate:
                        candidates.append(candidate)
                return candidates

            # A playable DOM element is only a candidate. Douyin pages preload
            # recommendation/autoplay videos, so direct /video/<id> requests
            # must prove the candidate belongs to that exact aweme.
            dom_candidates = _read_dom_candidates(await _dom_media_urls())
            observed.extend(dom_candidates)
            candidate = _verified_candidate(dom_candidates, source_video_id)
            if candidate:
                return _resolved(candidate)

            try:
                await page.wait_for_function(
                    """() => Array.from(document.querySelectorAll('video')).some(
                        v => (v.currentSrc || v.src || '').startsWith('http')
                    )""",
                    timeout=media_wait_timeout_ms,
                )
            except Exception:
                pass

            dom_candidates = _read_dom_candidates(await _dom_media_urls())
            observed.extend(dom_candidates)
            candidate = _verified_candidate(dom_candidates, source_video_id)
            if candidate:
                return _resolved(candidate)

            observed.extend(network_candidates)
            candidate = _verified_candidate(network_candidates, source_video_id)
            if candidate:
                return _resolved(candidate)

            # The page cannot prove identity. Fall back to the caption search
            # OUTSIDE this CDP session (it opens its own authenticated one).
            page_observation = _identity_observation(source_video_id, observed)
        finally:
            if close_page_here:
                try:
                    await page.close()
                except Exception:
                    pass

        if source_video_id and title_hint:
            search_candidates = await _search_caption_candidates(
                title_hint,
                source_video_id,
                cdp_url=cdp_url,
            )
            candidate = _verified_candidate(search_candidates, source_video_id)
            if candidate:
                return _resolved(candidate)

        raise RuntimeError(page_observation)
