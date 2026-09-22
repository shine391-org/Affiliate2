# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
AI client factory — BYOK (Bring Your Own Key).

Supports Anthropic, OpenAI, and OpenRouter. Keys come from `.env`
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
    "HTTP-Referer": "https://github.com/openclaw-easy/ViralMint",
    "X-Title": "ViralMint (OSS)",
}


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
        client_kwargs = {"api_key": self.api_key, "timeout": 55.0}
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


# ── Factory ───────────────────────────────────────────────────────────────────

def _resolve_user_provider_and_key(user_settings) -> tuple[Optional[AIProvider], Optional[str], Optional[str], Optional[str]]:
    """Read provider preference, decrypted API key, model and base_url from UserSettings, if set."""
    if not user_settings:
        return None, None, None, None

    provider_str = (getattr(user_settings, "ai_provider", None) or "").lower().strip()
    encrypted_key = getattr(user_settings, "ai_api_key_encrypted", None)
    base_url = getattr(user_settings, "ai_base_url", None)
    model = getattr(user_settings, "ai_model", None)
    if not provider_str or not encrypted_key:
        return None, None, None, None

    try:
        provider = AIProvider(provider_str)
    except ValueError:
        return None, None, None, None

    api_key = decrypt_safe(encrypted_key) or ""
    return provider, api_key or None, model or None, base_url or None


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
        return AIClient(
            provider=user_provider,
            api_key=user_key,
            model=u_model or user_model,
            base_url=u_base_url,
        )

    # 2. Check AI_PROVIDER from env/settings
    configured_provider = (getattr(settings, "AI_PROVIDER", "") or os.getenv("AI_PROVIDER", "")).lower().strip()
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

        if not api_key:
            raise AIKeyMissingError(
                "GEMINI_API_KEY REQUIRED: No AI_API_KEY or GEMINI_API_KEY configured for compatible provider."
            )
        if not base_url:
            raise ValueError("AI_BASE_URL is required when AI_PROVIDER=compatible.")

        return AIClient(
            provider=AIProvider.COMPATIBLE,
            api_key=api_key,
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
