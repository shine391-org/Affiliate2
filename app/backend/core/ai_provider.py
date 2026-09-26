# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
AI client factory — BYOK (Bring Your Own Key).

Supports Anthropic, OpenAI, OpenRouter, and OpenAI-compatible cloud/local endpoints. Keys come from `.env`
(system-wide default) or per-user `UserSettings` (overrides env). No
cloud-managed keys.

OpenRouter is a unified gateway that resells access to Claude / GPT /
Gemini / Llama / Mistral / etc. behind a single key. We talk to it via
the OpenAI-compatible chat-completions endpoint at
`https://openrouter.ai/api/v1`, with `model` set to a vendor-prefixed
slug like `anthropic/claude-opus-4.7`.
"""
from enum import Enum
from typing import AsyncIterator, Optional
from urllib.parse import urlparse
import logging

from backend.config import settings
from backend.core.crypto import decrypt_safe
from backend.core.exceptions import AIKeyMissingError

logger = logging.getLogger(__name__)


class AIProvider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    COMPATIBLE = "compatible"


# UI presets only. Runtime stays ONE OpenAI-compatible implementation.
# Keeping aliases out of AIProvider avoids branching the actual client for
# products that expose the same /v1 contract.
COMPATIBLE_PRESETS = {
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8000/v1",
    # Google documents an OpenAI-compatible Gemini endpoint. This is a UI
    # preset over the same compatible runtime, not a second provider engine.
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


def compatible_base_url(provider_name: str, explicit: str | None = None) -> str | None:
    value = (explicit or COMPATIBLE_PRESETS.get((provider_name or "").lower().strip()) or "").strip()
    return value.rstrip("/") or None


PROVIDER_DEFAULTS = {
    AIProvider.ANTHROPIC:  "claude-sonnet-4-6",
    AIProvider.OPENAI:     "gpt-5.4-mini",
    # OpenRouter BYOK users pay their own bill — default to a premium
    # model so the experience matches the gateway's value prop. Users
    # can pick a cheaper model in Settings → Model dropdown.
    AIProvider.OPENROUTER: "anthropic/claude-opus-4.7",
    AIProvider.COMPATIBLE: "gemini-2.5-flash",
}

# OpenRouter's optional analytics headers — show up on the public model
# leaderboard at openrouter.ai/rankings. Harmless to send; helps us
# track how much OSS-variant traffic flows through the gateway.
_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/shine391-org/Affiliate2",
    "X-Title": "Affiliate2",
}


def _openai_request_timeout_seconds(provider: AIProvider, base_url: str | None) -> float:
    """Give local compatible models enough time without weakening cloud failure detection."""
    if provider == AIProvider.COMPATIBLE and base_url:
        host = (urlparse(base_url).hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "::1"}:
            return 120.0
    return 55.0


class AIClient:
    """Universal AI client — same interface regardless of underlying provider."""

    def __init__(
        self,
        provider: AIProvider,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model or PROVIDER_DEFAULTS.get(provider, "gpt-4o-mini")

    async def chat_stream(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        """Yield text chunks as they stream in."""
        if self.provider == AIProvider.ANTHROPIC:
            async for chunk in self._anthropic_stream(messages, system, max_tokens):
                yield chunk
        elif self.provider in (AIProvider.OPENAI, AIProvider.COMPATIBLE):
            async for chunk in self._openai_stream(messages, system, max_tokens):
                yield chunk
        elif self.provider == AIProvider.OPENROUTER:
            async for chunk in self._openrouter_stream(messages, system, max_tokens):
                yield chunk

    async def chat(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        max_tokens: int = 2048,
    ) -> str:
        """Non-streaming — returns full response string."""
        chunks = []
        async for chunk in self.chat_stream(messages, system, max_tokens):
            chunks.append(chunk)
        return "".join(chunks)

    # ── Provider implementations ──────────────────────────────────────────────

    async def _anthropic_stream(self, messages, system, max_tokens):
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        async with client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "",
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def _openai_stream(self, messages, system, max_tokens):
        import openai
        client_kwargs = {
            "api_key": self.api_key,
            "timeout": _openai_request_timeout_seconds(self.provider, self.base_url),
        }
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = openai.AsyncOpenAI(**client_kwargs)
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        # GPT-5+ and o-series reasoning models reject `max_tokens` and require
        # `max_completion_tokens` (OpenAI API change, late 2024). Older models
        # accept either, but we only ship gpt-5.x in the registry, so this
        # branch is the common path.
        m = self.model.lower()
        uses_completion_tokens = (
            m.startswith(("gpt-5", "o1", "o3", "o4"))
        )
        token_kwargs = (
            {"max_completion_tokens": max_tokens}
            if uses_completion_tokens
            else {"max_tokens": max_tokens}
        )

        stream = await client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            stream=True,
            **token_kwargs,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    async def _openrouter_stream(self, messages, system, max_tokens):
        """OpenRouter is OpenAI-compatible — same client, different base URL."""
        import openai
        client = openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers=_OPENROUTER_HEADERS,
            timeout=55.0,
        )
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        # OpenRouter normalises max_tokens vs max_completion_tokens internally
        # for downstream models, so we always send max_tokens here regardless
        # of which underlying provider is being routed to.
        stream = await client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            stream=True,
            max_tokens=max_tokens,
        )
        async for chunk in stream:
            # Defensive: OpenRouter sometimes emits keepalive chunks with
            # an empty `choices` array before real tokens start flowing.
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


async def list_openai_compatible_models(base_url: str, api_key: str = "") -> list[str]:
    """List model IDs from an OpenAI-compatible /models endpoint."""
    import openai

    client = openai.AsyncOpenAI(
        api_key=(api_key or "local"),
        base_url=base_url.rstrip("/"),
        timeout=20.0,
    )
    page = await client.models.list()
    ids = {str(item.id).strip() for item in (page.data or []) if getattr(item, "id", None)}
    return sorted(x for x in ids if x)[:500]


def make_ai_client(
    provider_name: str,
    api_key: str = "",
    model: str | None = None,
    base_url: str | None = None,
) -> AIClient:
    """Build one canonical client from Settings-form values.

    Ollama/LM Studio/vLLM are presets over the same compatible transport, not
    separate providers. Local compatible servers may legitimately use no key.
    """
    name = (provider_name or "").lower().strip()
    if name in COMPATIBLE_PRESETS or name == AIProvider.COMPATIBLE.value:
        resolved_base = compatible_base_url(name, base_url)
        if not resolved_base:
            raise ValueError("Base URL is required for an OpenAI-compatible provider.")
        if not (model or "").strip():
            raise ValueError("Model ID is required for an OpenAI-compatible provider.")
        if name == "gemini" and not (api_key or "").strip():
            raise AIKeyMissingError("Google Gemini API key is required.")
        return AIClient(
            provider=AIProvider.COMPATIBLE,
            api_key=(api_key or "local"),
            model=model.strip(),
            base_url=resolved_base,
        )

    try:
        provider = AIProvider(name)
    except ValueError as exc:
        raise ValueError(f"Unknown AI provider: {provider_name}") from exc
    if not api_key:
        raise AIKeyMissingError(f"API key is required for {name}.")
    return AIClient(provider=provider, api_key=api_key, model=model, base_url=base_url)


# ── Factory ───────────────────────────────────────────────────────────────────

def _resolve_user_provider_and_key(user_settings) -> tuple[Optional[AIProvider], Optional[str], Optional[str], Optional[str]]:
    """Resolve stored settings onto the canonical runtime provider."""
    if not user_settings:
        return None, None, None, None

    provider_str = (getattr(user_settings, "ai_provider", None) or "").lower().strip()
    encrypted_key = getattr(user_settings, "ai_api_key_encrypted", None)
    base_url = getattr(user_settings, "ai_base_url", None)
    model = getattr(user_settings, "ai_model", None)
    if not provider_str:
        return None, None, None, None

    if provider_str in COMPATIBLE_PRESETS:
        # Local presets use a dummy key. Gemini is cloud-hosted and must keep
        # its own BYOK secret while still sharing the compatible transport.
        if provider_str == "gemini":
            api_key = decrypt_safe(encrypted_key) if encrypted_key else ""
            return (
                AIProvider.COMPATIBLE,
                api_key or None,
                model or None,
                compatible_base_url(provider_str, base_url),
            )
        return (
            AIProvider.COMPATIBLE,
            "local",
            model or None,
            compatible_base_url(provider_str, base_url),
        )
    if provider_str == AIProvider.COMPATIBLE.value:
        api_key = decrypt_safe(encrypted_key) if encrypted_key else ""
        return (
            AIProvider.COMPATIBLE,
            api_key or "local",
            model or None,
            compatible_base_url(provider_str, base_url),
        )

    try:
        provider = AIProvider(provider_str)
    except ValueError:
        return None, None, None, None
    if not encrypted_key:
        return None, None, None, None

    api_key = decrypt_safe(encrypted_key) or ""
    return provider, api_key or None, model or None, base_url or None


def describe_effective_ai(user_settings=None) -> dict:
    """Return non-secret provenance for the AI client get_ai_client would use."""
    import os

    user_provider, user_key, user_model, user_base_url = _resolve_user_provider_and_key(
        user_settings
    )
    if user_provider and user_key:
        raw_name = (
            getattr(user_settings, "ai_provider", None)
            or user_provider.value
        )
        model = (
            user_model
            or getattr(user_settings, "ai_model", None)
            or PROVIDER_DEFAULTS.get(user_provider)
        )
        return {
            "configured": True,
            "source": "settings",
            "provider": str(raw_name),
            "runtime_provider": user_provider.value,
            "model": model,
            "base_url": user_base_url,
        }

    configured_provider = (
        getattr(settings, "AI_PROVIDER", "")
        or os.getenv("AI_PROVIDER", "")
    ).lower().strip()
    if configured_provider == "gemini":
        api_key = (
            getattr(settings, "GEMINI_API_KEY", "")
            or os.getenv("GEMINI_API_KEY", "")
        ).strip()
        model = (
            getattr(user_settings, "ai_model", None) if user_settings else None
        ) or (
            getattr(settings, "AI_MODEL", "")
            or os.getenv("AI_MODEL", "")
        ).strip() or "gemini-3.8-flash"
        return {
            "configured": bool(api_key),
            "source": "environment",
            "provider": "gemini",
            "runtime_provider": "compatible",
            "model": model,
            "base_url": compatible_base_url("gemini"),
        }
    if configured_provider == "compatible":
        base_url = (
            getattr(settings, "AI_BASE_URL", "")
            or os.getenv("AI_BASE_URL", "")
        ).strip() or None
        model = (
            getattr(user_settings, "ai_model", None) if user_settings else None
        ) or (
            getattr(settings, "AI_MODEL", "")
            or os.getenv("AI_MODEL", "")
        ).strip() or None
        return {
            "configured": bool(base_url and model),
            "source": "environment",
            "provider": "compatible",
            "runtime_provider": "compatible",
            "model": model,
            "base_url": base_url,
        }

    env_candidates = [
        ("anthropic", getattr(settings, "ANTHROPIC_API_KEY", "")),
        ("openai", getattr(settings, "OPENAI_API_KEY", "")),
        ("openrouter", getattr(settings, "OPENROUTER_API_KEY", "")),
    ]
    for name, key in env_candidates:
        if key:
            provider = AIProvider(name)
            return {
                "configured": True,
                "source": "environment",
                "provider": name,
                "runtime_provider": name,
                "model": (
                    getattr(user_settings, "ai_model", None)
                    if user_settings else None
                ) or PROVIDER_DEFAULTS.get(provider),
                "base_url": None,
            }

    return {
        "configured": False,
        "source": None,
        "provider": None,
        "runtime_provider": None,
        "model": None,
        "base_url": None,
    }


def get_ai_client(user_settings=None, **_kwargs) -> AIClient:
    """
    Return a configured AI client.

    Resolution order:
      1. UserSettings.ai_provider + UserSettings.ai_api_key_encrypted (BYOK from UI)
      2. .env / environment config:
         - If AI_PROVIDER == "compatible":
             Uses AI_BASE_URL, AI_API_KEY (or GEMINI_API_KEY), AI_MODEL
         - ANTHROPIC_API_KEY → OPENAI_API_KEY → OPENROUTER_API_KEY
      3. Raise AIKeyMissingError with setup instructions.
    """
    import os
    user_model = getattr(user_settings, "ai_model", None) if user_settings else None

    # 1. User-configured BYOK
    user_provider, user_key, u_model, u_base_url = _resolve_user_provider_and_key(user_settings)
    if user_provider and user_key:
        raw_name = (getattr(user_settings, "ai_provider", None) or user_provider.value)
        return make_ai_client(
            provider_name=raw_name,
            api_key=user_key,
            model=u_model or user_model,
            base_url=u_base_url,
        )

    # 2. Check AI_PROVIDER from env/settings
    configured_provider = (getattr(settings, "AI_PROVIDER", "") or os.getenv("AI_PROVIDER", "")).lower().strip()
    if configured_provider == "gemini":
        api_key = (
            getattr(settings, "GEMINI_API_KEY", "")
            or os.getenv("GEMINI_API_KEY", "")
        ).strip()
        if not api_key:
            raise AIKeyMissingError("GEMINI_API_KEY is required when AI_PROVIDER=gemini.")
        model = (
            user_model
            or getattr(settings, "AI_MODEL", "")
            or os.getenv("AI_MODEL", "")
            or "gemini-3.8-flash"
        ).strip()
        return make_ai_client(
            "gemini",
            api_key=api_key,
            model=model,
            base_url=compatible_base_url("gemini"),
        )
    if configured_provider == AIProvider.COMPATIBLE or configured_provider == "compatible":
        api_key = (
            getattr(settings, "AI_API_KEY", "")
            or os.getenv("AI_API_KEY", "")
            or getattr(settings, "GEMINI_API_KEY", "")
            or os.getenv("GEMINI_API_KEY", "")
        ).strip()
        base_url = (
            getattr(settings, "AI_BASE_URL", "")
            or os.getenv("AI_BASE_URL", "")
        ).strip()
        model = (
            user_model
            or getattr(settings, "AI_MODEL", "")
            or os.getenv("AI_MODEL", "")
        ).strip() or None

        if not base_url:
            raise ValueError("AI_BASE_URL is required when AI_PROVIDER=compatible.")
        if not model:
            raise ValueError("AI_MODEL is required when AI_PROVIDER=compatible.")

        return AIClient(
            provider=AIProvider.COMPATIBLE,
            api_key=api_key or "local",
            model=model,
            base_url=base_url,
        )

    # Fallback to existing providers
    if settings.ANTHROPIC_API_KEY:
        return AIClient(
            provider=AIProvider.ANTHROPIC,
            api_key=settings.ANTHROPIC_API_KEY,
            model=user_model,
        )
    if settings.OPENAI_API_KEY:
        return AIClient(
            provider=AIProvider.OPENAI,
            api_key=settings.OPENAI_API_KEY,
            model=user_model,
        )
    if settings.OPENROUTER_API_KEY:
        return AIClient(
            provider=AIProvider.OPENROUTER,
            api_key=settings.OPENROUTER_API_KEY,
            model=user_model,
        )

    raise AIKeyMissingError(
        "No AI provider configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
        "OPENROUTER_API_KEY, or AI_PROVIDER=compatible in your .env, or configure your provider and key in Settings."
    )
