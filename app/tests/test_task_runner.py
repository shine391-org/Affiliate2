# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Tests for backend/core/task_runner.py — _detect_platform helper."""
import pytest

from backend.core.task_runner import _detect_platform


class TestDetectPlatform:
    def test_youtube_urls(self):
        assert _detect_platform("https://www.youtube.com/watch?v=abc123") == "youtube"
        assert _detect_platform("https://youtube.com/watch?v=abc") == "youtube"
        assert _detect_platform("https://m.youtube.com/watch?v=abc") == "youtube"

    def test_youtu_be_shortlink(self):
        assert _detect_platform("https://youtu.be/abc123") == "youtube"

    def test_tiktok_urls(self):
        assert _detect_platform("https://www.tiktok.com/@user/video/123") == "tiktok"
        assert _detect_platform("https://tiktok.com/@user/video/123") == "tiktok"

    def test_bilibili_url(self):
        assert _detect_platform("https://www.bilibili.com/video/BV123") == "bilibili"

    def test_reddit_url(self):
        assert _detect_platform("https://www.reddit.com/r/test/comments/abc") == "reddit"

    def test_instagram_url(self):
        assert _detect_platform("https://www.instagram.com/p/abc123/") == "instagram"

    def test_soundcloud_url(self):
        assert _detect_platform("https://soundcloud.com/artist/track") == "soundcloud"

    def test_vimeo_url(self):
        assert _detect_platform("https://vimeo.com/123456") == "vimeo"

    def test_unknown_url(self):
        result = _detect_platform("https://example.com/page")
        assert result == "example"

    def test_invalid_url_returns_empty(self):
        # urlparse gives hostname=None for non-URLs, so host="" → domain=""
        assert _detect_platform("not-a-url") == ""
        assert _detect_platform("") == ""

    def test_url_with_path_and_query(self):
        url = "https://www.youtube.com/watch?v=abc&t=30s"
        assert _detect_platform(url) == "youtube"

    def test_douyin_url(self):
        assert _detect_platform("https://www.douyin.com/video/123") == "douyin"
