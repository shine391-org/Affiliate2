# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Tests for backend/core/exceptions.py — safe_json_loads and exception hierarchy."""
import pytest

from backend.core.exceptions import (
    safe_json_loads,
    ViralMintError,
    ScoutError,
    PlatformUnavailableError,
    CookieExpiredError,
    QuotaExceededError,
    DownloadError,
    RateLimitError,
    VideoUnavailableError,
    GenerationError,
    VoiceGenerationError,
    VideoGenerationError,
    UploadError,
    UploadAuthError,
    AIProviderError,
    AIKeyMissingError,
)


# ── safe_json_loads ────────────────────────────────────────────────────────────

class TestSafeJsonLoads:
    def test_parses_valid_json(self):
        assert safe_json_loads('{"key": "value"}') == {"key": "value"}

    def test_parses_array(self):
        assert safe_json_loads('[1, 2, 3]') == [1, 2, 3]

    def test_returns_default_on_empty(self):
        assert safe_json_loads("") is None
        assert safe_json_loads(None) is None
        assert safe_json_loads("", default={}) == {}

    def test_returns_default_on_invalid_json(self):
        assert safe_json_loads("{broken") is None
        assert safe_json_loads("not json") is None
        assert safe_json_loads("{broken", default=[]) == []

    def test_returns_default_on_type_error(self):
        assert safe_json_loads(12345) is None  # type: ignore

    def test_custom_default(self):
        assert safe_json_loads("bad", default={"fallback": True}) == {"fallback": True}

    def test_logger_called_on_failure(self):
        """When a logger is passed, it should log warnings on parse failure."""
        import logging
        mock_logger = logging.getLogger("test_safe_json")
        # Should not raise even with logger
        result = safe_json_loads("{invalid", logger=mock_logger)
        assert result is None

    def test_parses_nested_json(self):
        raw = '{"a": {"b": [1, 2, {"c": true}]}}'
        result = safe_json_loads(raw)
        assert result["a"]["b"][2]["c"] is True

    def test_parses_json_with_unicode(self):
        raw = '{"name": "测试用户"}'
        result = safe_json_loads(raw)
        assert result["name"] == "测试用户"


# ── Exception hierarchy ───────────────────────────────────────────────────────

class TestExceptionHierarchy:
    def test_all_errors_inherit_from_viralmint_error(self):
        errors = [
            ScoutError, PlatformUnavailableError, CookieExpiredError,
            QuotaExceededError, DownloadError, RateLimitError,
            VideoUnavailableError, GenerationError, VoiceGenerationError,
            VideoGenerationError, UploadError, UploadAuthError,
            AIProviderError, AIKeyMissingError,
        ]
        for error_cls in errors:
            assert issubclass(error_cls, ViralMintError), f"{error_cls.__name__} should inherit from ViralMintError"

    def test_scout_error_hierarchy(self):
        assert issubclass(PlatformUnavailableError, ScoutError)
        assert issubclass(CookieExpiredError, ScoutError)
        assert issubclass(QuotaExceededError, ScoutError)

    def test_download_error_hierarchy(self):
        assert issubclass(RateLimitError, DownloadError)
        assert issubclass(VideoUnavailableError, DownloadError)

    def test_generation_error_hierarchy(self):
        assert issubclass(VoiceGenerationError, GenerationError)
        assert issubclass(VideoGenerationError, GenerationError)

    def test_upload_error_hierarchy(self):
        assert issubclass(UploadAuthError, UploadError)

    def test_ai_error_hierarchy(self):
        assert issubclass(AIKeyMissingError, AIProviderError)

    def test_errors_are_catchable(self):
        with pytest.raises(ViralMintError):
            raise ScoutError("test")

        with pytest.raises(ScoutError):
            raise CookieExpiredError("expired")

        with pytest.raises(DownloadError):
            raise RateLimitError("429")
