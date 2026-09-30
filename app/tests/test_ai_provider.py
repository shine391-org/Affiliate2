# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Tests for backend.core.ai_provider — the BYOK AI client factory.

Covers get_ai_client() resolution order (user BYOK → env → error),
_resolve_user_provider_and_key() decryption/validation, PROVIDER_DEFAULTS
model selection, and AIClient.chat() using true non-streaming completion while chat_stream() remains
available for interactive callers. No real provider SDK is ever imported or called.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.core import ai_provider as ap
from backend.core.ai_provider import (
    AIClient, AIProvider, PROVIDER_DEFAULTS, get_ai_client,
    _resolve_user_provider_and_key, compatible_base_url, make_ai_client,
)
from backend.core.crypto import encrypt
from backend.core.exceptions import AIKeyMissingError


def _no_env_keys():
    """Patch all three env keys to empty so only the argument path is exercised."""
    return patch.multiple(
        ap.settings,
        ANTHROPIC_API_KEY="",
        OPENAI_API_KEY="",
        OPENROUTER_API_KEY="",
    )


# ── PROVIDER_DEFAULTS + AIClient construction ──────────────────────────────

def test_provider_defaults_cover_every_provider():
    for p in AIProvider:
        assert p in PROVIDER_DEFAULTS and PROVIDER_DEFAULTS[p]


def test_aiclient_uses_default_model_when_none():
    c = AIClient(AIProvider.ANTHROPIC, "sk-x")
    assert c.model == PROVIDER_DEFAULTS[AIProvider.ANTHROPIC]
    assert c.provider == AIProvider.ANTHROPIC and c.api_key == "sk-x"


def test_aiclient_honors_explicit_model():
    c = AIClient(AIProvider.OPENAI, "sk-y", model="gpt-custom")
    assert c.model == "gpt-custom"


# ── _resolve_user_provider_and_key ─────────────────────────────────────────

def test_resolve_none_when_no_settings():
    assert _resolve_user_provider_and_key(None) == (None, None, None, None)


def test_resolve_none_when_provider_or_key_missing():
    s = SimpleNamespace(ai_provider="", ai_api_key_encrypted="x")
    assert _resolve_user_provider_and_key(s) == (None, None, None, None)
    s2 = SimpleNamespace(ai_provider="anthropic", ai_api_key_encrypted=None)
    assert _resolve_user_provider_and_key(s2) == (None, None, None, None)


def test_resolve_none_when_provider_invalid():
    s = SimpleNamespace(ai_provider="not-a-provider", ai_api_key_encrypted=encrypt("k"))
    assert _resolve_user_provider_and_key(s) == (None, None, None, None)


def test_resolve_decrypts_valid_settings():
    s = SimpleNamespace(ai_provider="OpenAI", ai_api_key_encrypted=encrypt("secret-key"))
    provider, key, model, base_url = _resolve_user_provider_and_key(s)
    assert provider == AIProvider.OPENAI and key == "secret-key"
    assert model is None and base_url is None


# ── get_ai_client resolution order ─────────────────────────────────────────

def test_raises_when_nothing_configured():
    with _no_env_keys(), pytest.raises(AIKeyMissingError):
        get_ai_client(None)


def test_env_anthropic_wins_first():
    with patch.multiple(ap.settings, ANTHROPIC_API_KEY="anthr", OPENAI_API_KEY="oai",
                        OPENROUTER_API_KEY="ork"):
        c = get_ai_client(None)
    assert c.provider == AIProvider.ANTHROPIC and c.api_key == "anthr"


def test_env_openai_when_anthropic_empty():
    with patch.multiple(ap.settings, ANTHROPIC_API_KEY="", OPENAI_API_KEY="oai",
                        OPENROUTER_API_KEY="ork"):
        c = get_ai_client(None)
    assert c.provider == AIProvider.OPENAI and c.api_key == "oai"


def test_env_openrouter_last_resort():
    with patch.multiple(ap.settings, ANTHROPIC_API_KEY="", OPENAI_API_KEY="",
                        OPENROUTER_API_KEY="ork"):
        c = get_ai_client(None)
    assert c.provider == AIProvider.OPENROUTER and c.api_key == "ork"


def test_user_byok_overrides_env_and_uses_user_model():
    s = SimpleNamespace(
        ai_provider="openrouter",
        ai_api_key_encrypted=encrypt("byok-key"),
        ai_model="anthropic/claude-custom",
    )
    with patch.multiple(ap.settings, ANTHROPIC_API_KEY="env-anthr"):
        c = get_ai_client(s)
    assert c.provider == AIProvider.OPENROUTER
    assert c.api_key == "byok-key"
    assert c.model == "anthropic/claude-custom"


def test_env_path_still_applies_user_model():
    s = SimpleNamespace(ai_provider="", ai_api_key_encrypted="", ai_model="my-model")
    with patch.multiple(ap.settings, ANTHROPIC_API_KEY="env-anthr"):
        c = get_ai_client(s)
    assert c.provider == AIProvider.ANTHROPIC and c.model == "my-model"


# ── structured chat is non-streaming; interactive streaming remains separate ─

async def test_chat_routes_to_non_streaming_provider_completion():
    c = AIClient(AIProvider.ANTHROPIC, "sk-x")
    seen = {}

    async def _fake_complete(messages, system, max_tokens):
        seen["complete"] = True
        return "Hello world"

    async def _forbidden_stream(*args, **kwargs):
        raise AssertionError("structured chat must not use SSE stream")

    with patch.object(c, "_anthropic_complete", _fake_complete), patch.object(c, "_anthropic_stream", _forbidden_stream):
        out = await c.chat([{"role": "user", "content": "hi"}])
    assert out == "Hello world"
    assert seen["complete"] is True


async def test_chat_stream_dispatches_to_openai_impl():
    c = AIClient(AIProvider.OPENAI, "sk-y")
    seen = {}

    async def _fake_openai(messages, system, max_tokens):
        seen["hit"] = True
        yield "x"

    with patch.object(c, "_openai_stream", _fake_openai):
        chunks = [t async for t in c.chat_stream([{"role": "user", "content": "q"}])]
    assert chunks == ["x"] and seen.get("hit")


def test_local_provider_needs_no_api_key_and_uses_preset_url():
    s = SimpleNamespace(
        ai_provider="ollama",
        ai_api_key_encrypted=None,
        ai_model="qwen3:8b",
        ai_base_url=None,
    )
    provider, key, model, base_url = _resolve_user_provider_and_key(s)
    assert provider == AIProvider.COMPATIBLE
    assert key == "local"
    assert model == "qwen3:8b"
    assert base_url == "http://localhost:11434/v1"


def test_compatible_base_url_prefers_explicit_override():
    assert compatible_base_url("ollama", "http://127.0.0.1:9999/v1/") == "http://127.0.0.1:9999/v1"


def test_make_local_client_requires_model_but_not_key():
    c = make_ai_client("lmstudio", model="local-model")
    assert c.provider == AIProvider.COMPATIBLE
    assert c.api_key == "local"
    assert c.model == "local-model"
    assert c.base_url == "http://localhost:1234/v1"


def test_make_compatible_client_requires_base_url():
    with pytest.raises(ValueError, match="Base URL"):
        make_ai_client("compatible", model="x")


def test_gemini_preset_uses_official_compatible_endpoint_and_byok_key():
    s = SimpleNamespace(
        ai_provider="gemini",
        ai_api_key_encrypted=encrypt("AIza-test"),
        ai_model="gemini-3.8-flash",
        ai_base_url=None,
    )
    provider, key, model, base_url = _resolve_user_provider_and_key(s)
    assert provider == AIProvider.COMPATIBLE
    assert key == "AIza-test"
    assert model == "gemini-3.8-flash"
    assert base_url == "https://generativelanguage.googleapis.com/v1beta/openai"


def test_make_gemini_client_requires_key_but_reuses_compatible_runtime():
    with pytest.raises(AIKeyMissingError, match="Gemini API key"):
        make_ai_client("gemini", model="gemini-3.8-flash")

    client = make_ai_client(
        "gemini",
        api_key="AIza-test",
        model="gemini-3.8-flash",
    )
    assert client.provider == AIProvider.COMPATIBLE
    assert client.api_key == "AIza-test"
    assert client.model == "gemini-3.8-flash"
    assert client.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"


def test_openrouter_attribution_uses_affiliate2_not_donor_brand():
    assert "Affiliate2" in ap._OPENROUTER_HEADERS["X-Title"]
    assert "ViralMint" not in str(ap._OPENROUTER_HEADERS)


def test_local_preset_never_reuses_stored_cloud_secret():
    s = SimpleNamespace(
        ai_provider="ollama",
        ai_api_key_encrypted=encrypt("SHOULD-NOT-LEAK"),
        ai_model="qwen3:8b",
        ai_base_url=None,
    )
    provider, key, model, base_url = _resolve_user_provider_and_key(s)
    assert provider == AIProvider.COMPATIBLE
    assert key == "local"
    assert model == "qwen3:8b"
    assert base_url == "http://localhost:11434/v1"


def test_env_named_gemini_uses_gemini_key_and_compatible_runtime():
    with patch.multiple(
        ap.settings,
        AI_PROVIDER="gemini",
        AI_MODEL="gemini-3.8-flash",
        GEMINI_API_KEY="AIza-env",
        ANTHROPIC_API_KEY="",
        OPENAI_API_KEY="",
        OPENROUTER_API_KEY="",
    ):
        c = get_ai_client(None)
    assert c.provider == AIProvider.COMPATIBLE
    assert c.api_key == "AIza-env"
    assert c.model == "gemini-3.8-flash"
    assert c.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"


def test_env_compatible_local_can_run_without_api_key():
    with patch.multiple(
        ap.settings,
        AI_PROVIDER="compatible",
        AI_BASE_URL="http://localhost:11434/v1",
        AI_API_KEY="",
        AI_MODEL="qwen3:8b",
        GEMINI_API_KEY="",
        ANTHROPIC_API_KEY="",
        OPENAI_API_KEY="",
        OPENROUTER_API_KEY="",
    ):
        c = get_ai_client(None)
    assert c.provider == AIProvider.COMPATIBLE
    assert c.api_key == "local"
    assert c.model == "qwen3:8b"
    assert c.base_url == "http://localhost:11434/v1"



def test_local_compatible_gets_longer_request_timeout():
    assert ap._openai_request_timeout_seconds(
        AIProvider.COMPATIBLE,
        "http://127.0.0.1:1234/v1",
    ) == 120.0
    assert ap._openai_request_timeout_seconds(
        AIProvider.COMPATIBLE,
        "http://localhost:11434/v1",
    ) == 120.0
    assert ap._openai_request_timeout_seconds(
        AIProvider.COMPATIBLE,
        "https://api.example.com/v1",
    ) == 55.0
    assert ap._openai_request_timeout_seconds(
        AIProvider.OPENAI,
        "https://api.openai.com/v1",
    ) == 55.0
