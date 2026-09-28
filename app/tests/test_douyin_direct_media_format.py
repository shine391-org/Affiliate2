"""A resolved Douyin CDN URL is a direct media file, not a format ladder.

yt-dlp's generic extractor reports one format with no height metadata, so every
`[height<=N]` selector fails and the download dies even though the bytes are
right there. The downloader must ask for the direct file instead.
"""
from __future__ import annotations

import pytest

from backend.services.ytdlp_service import (
    FORMAT_FALLBACK_CHAIN,
    _format_chain_for_source,
)


def test_plain_urls_keep_the_canonical_ladder():
    chain = _format_chain_for_source(
        "https://www.youtube.com/watch?v=abc", None, FORMAT_FALLBACK_CHAIN
    )
    assert chain == FORMAT_FALLBACK_CHAIN


def test_resolved_cdp_media_url_asks_for_the_direct_file():
    chain = _format_chain_for_source(
        "https://v3-web.douyinvod.com/abc/video/tos/x/?mime_type=video_mp4",
        "douyin_cdp",
        FORMAT_FALLBACK_CHAIN,
    )
    assert chain == ["best"]


def test_resolved_cdp_media_url_drops_the_capped_selector():
    chain = _format_chain_for_source(
        "https://v3-web.douyinvod.com/abc/video.mp4",
        "douyin_cdp",
        ["best[height<=720]"],
    )
    assert "best[height<=720]" not in chain
    assert chain == ["best"]
