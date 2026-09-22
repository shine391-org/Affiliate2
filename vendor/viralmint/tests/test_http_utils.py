# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Tests for backend/core/http_utils.py — user agent rotation, headers, delays."""
import pytest

from backend.core.http_utils import (
    get_user_agent,
    get_default_headers,
    jittered_delay,
    INTER_DOWNLOAD_DELAY_MIN,
    INTER_DOWNLOAD_DELAY_MAX,
    _USER_AGENTS,
)


class TestGetUserAgent:
    def test_returns_string(self):
        ua = get_user_agent()
        assert isinstance(ua, str)
        assert len(ua) > 50  # real user agents are long

    def test_contains_browser_identifier(self):
        ua = get_user_agent()
        # Should contain at least one browser name
        browsers = ["Chrome", "Firefox", "Safari", "Edg", "Brave", "OPR"]
        assert any(b in ua for b in browsers), f"UA doesn't contain browser name: {ua}"

    def test_returns_from_pool(self):
        """Result should be one of the predefined UAs."""
        ua = get_user_agent()
        assert ua in _USER_AGENTS

    def test_randomness(self):
        """Over many calls, we should get different UAs."""
        results = {get_user_agent() for _ in range(100)}
        assert len(results) > 1  # should pick more than one UA


class TestGetDefaultHeaders:
    def test_returns_dict_with_required_keys(self):
        headers = get_default_headers()
        assert "User-Agent" in headers
        assert "Accept-Language" in headers

    def test_user_agent_is_from_pool(self):
        headers = get_default_headers()
        assert headers["User-Agent"] in _USER_AGENTS

    def test_accept_language_is_english(self):
        headers = get_default_headers()
        assert "en" in headers["Accept-Language"]


class TestJitteredDelay:
    def test_returns_float_in_range(self):
        delay = jittered_delay()
        assert isinstance(delay, float)
        assert INTER_DOWNLOAD_DELAY_MIN <= delay <= INTER_DOWNLOAD_DELAY_MAX

    def test_varies_across_calls(self):
        delays = {jittered_delay() for _ in range(50)}
        assert len(delays) > 1  # should not return same value every time

    def test_delay_bounds(self):
        """All delays should be within configured bounds."""
        for _ in range(100):
            d = jittered_delay()
            assert d >= INTER_DOWNLOAD_DELAY_MIN
            assert d <= INTER_DOWNLOAD_DELAY_MAX
