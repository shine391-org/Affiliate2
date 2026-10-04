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
from contextlib import asynccontextmanager, aclosing
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import asyncio
import json
import logging
import math

from backend.config import settings
from backend.core.crypto import decrypt_safe
from backend.core.exceptions import AIKeyMissingError
from backend.core.concurrency import AIProviderCooldownError, AIRequestBudgetError, get_ai_gate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AIRequestContext:
    deadline_at: float | None = None
    request_timeout_s: float | None = None
    progress_cb: Optional[object] = None
    dispatch_budget: Optional[object] = None


def parse_ai_retry_after(value) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            when = parsedate_to_datetime(str(value))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _ai_error_body(exc):
    body = getattr(exc, 'body', None)
    return (json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list))
            else str(body or exc)).lower()


def is_ai_rate_limit_error(exc) -> bool:
    # Read .code too: a real 429 exposed only via .code would otherwise be
    # treated as a plain transient error and retried WITHOUT publishing the
    # shared cooldown this gate exists to enforce.
    status = getattr(exc, 'status_code', None) or getattr(exc, 'code', None)
    if status == 429:
        return True
    return status == 502 and any(marker in _ai_error_body(exc) for marker in (
        'gửi tin quá thường xuyên', 'too many messages', 'too many requests', 'rate limit',
    ))


async def _ai_progress(context, event):
    if context.progress_cb is None:
        return
    try:
        result = context.progress_cb(event)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.warning('AI request progress callback failed', exc_info=True)


# Retain best-effort dispatch observers until they finish. Observability must
# not consume a deadline/budget between admission and the actual SDK send.
_ai_progress_tasks: set[asyncio.Task] = set()

# ── Traffic policy ─────────────────────────────────────────────────────────

_TRAFFIC_POLICY_FIELDS = frozenset({
    "enabled", "min_gap_s", "cooldown_initial_s", "cooldown_max_s", "resource_group",
})


def normalize_ai_traffic_policy(value) -> dict | None:
    """Validate and canonicalize an optional AI traffic policy.

    Returns None for None input. Returns a canonical dict with all five
    fields for valid input. Raises ValueError for invalid input.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("traffic_policy must be a dict or None")

    unknown = set(value) - _TRAFFIC_POLICY_FIELDS
    if unknown:
        raise ValueError(f"Unknown traffic_policy field(s): {', '.join(sorted(unknown))}")

    canonical: dict = {}

    # enabled — strict bool
    if "enabled" in value:
        enabled = value["enabled"]
        if not isinstance(enabled, bool):
            raise ValueError("traffic_policy.enabled must be a bool")
        canonical["enabled"] = enabled
    else:
        canonical["enabled"] = False

    # min_gap_s — non-negative finite float, default 10
    if "min_gap_s" in value:
        min_gap = value["min_gap_s"]
        if isinstance(min_gap, bool) or not isinstance(min_gap, (int, float)):
            raise ValueError("traffic_policy.min_gap_s must be a number")
        min_gap = float(min_gap)
        if math.isnan(min_gap) or math.isinf(min_gap):
            raise ValueError("traffic_policy.min_gap_s must be finite")
        if min_gap < 0:
            raise ValueError("traffic_policy.min_gap_s must be non-negative")
        canonical["min_gap_s"] = min_gap
    else:
        canonical["min_gap_s"] = 10.0

    # cooldown_initial_s — non-negative finite float, default 30
    if "cooldown_initial_s" in value:
        ci = value["cooldown_initial_s"]
        if isinstance(ci, bool) or not isinstance(ci, (int, float)):
            raise ValueError("traffic_policy.cooldown_initial_s must be a number")
        ci = float(ci)
        if math.isnan(ci) or math.isinf(ci):
            raise ValueError("traffic_policy.cooldown_initial_s must be finite")
        if ci < 0:
            raise ValueError("traffic_policy.cooldown_initial_s must be non-negative")
        canonical["cooldown_initial_s"] = ci
    else:
        canonical["cooldown_initial_s"] = 30.0

    # cooldown_max_s — non-negative finite float, default 300
    if "cooldown_max_s" in value:
        cm = value["cooldown_max_s"]
        if isinstance(cm, bool) or not isinstance(cm, (int, float)):
            raise ValueError("traffic_policy.cooldown_max_s must be a number")
        cm = float(cm)
        if math.isnan(cm) or math.isinf(cm):
            raise ValueError("traffic_policy.cooldown_max_s must be finite")
        if cm < 0:
            raise ValueError("traffic_policy.cooldown_max_s must be non-negative")
        canonical["cooldown_max_s"] = cm
    else:
        canonical["cooldown_max_s"] = 300.0

    # resource_group — optional string, default None
    if "resource_group" in value:
        rg = value["resource_group"]
        if rg is None:
            canonical["resource_group"] = None
        elif isinstance(rg, str):
            stripped = rg.strip()
            canonical["resource_group"] = stripped or None
        else:
            raise ValueError("traffic_policy.resource_group must be a string or None")
    else:
        canonical["resource_group"] = None

    return canonical


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


def is_transient_ai_error(exc: Exception) -> bool:
    """Classify whether an exception from an AI provider is transient and retryable."""
    if any(marker in _ai_error_body(exc) for marker in (
        'no data.biz_data.challenge', '40003', 'login expired', 'token expired', 'not logged in',
    )):
        return False
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status_code in (408, 429, 500, 502, 503, 504):
        return True
    if status_code in (400, 401, 403, 404):
        return False
    msg = str(exc).lower()
    for code in ("503", "502", "504", "500", "429", "408"):
        if code in msg:
            return True
    for kw in (
        "service unavailable", "overloaded", "too many requests",
        "rate limit", "resource exhausted", "timed out", "timeout",
        "connection reset", "connection refused", "bad gateway",
        "gateway timeout",
    ):
        if kw in msg:
            return True
    name = type(exc).__name__.lower()
    if any(k in name for k in ("timeout", "connection", "ratelimit", "internalservererror", "badgateway", "serviceunavailable")):
        return True
    return False


class AIClient:
    """Universal AI client — same interface regardless of underlying provider."""

    def __init__(
        self,
        provider: AIProvider,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        max_retries: Optional[int] = None,
        traffic_policy: dict | None = None,
    ):
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model or PROVIDER_DEFAULTS.get(provider, "gpt-4o-mini")
        self.max_retries = max_retries
        self.traffic_policy = normalize_ai_traffic_policy(traffic_policy)
        if self.traffic_policy and self.traffic_policy['enabled']:
            self.max_retries = 0

    def _resource_endpoint(self):
        if self.provider == AIProvider.OPENROUTER:
            base = 'https://openrouter.ai/api/v1'
        else:
            base = self.base_url or ('https://api.anthropic.com/v1' if self.provider == AIProvider.ANTHROPIC
                                     else 'https://api.openai.com/v1')
        parsed = urlparse(base)
        host = (parsed.hostname or '').lower()
        if host in {'localhost', '127.0.0.1', '::1'}:
            host = 'loopback'
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        return f'{parsed.scheme.lower()}://{host}:{port}{parsed.path.rstrip("/")}'

    @asynccontextmanager
    async def _request_scope(self, request_context) -> AsyncIterator[None]:
        context = request_context or AIRequestContext()
        loop = asyncio.get_running_loop()
        gate = None
        acquired = False
        dispatched = False
        policy = self.traffic_policy or {}
        if policy.get('enabled'):
            gate = get_ai_gate(self._resource_endpoint(), policy.get('resource_group'), policy)
        try:
            if context.deadline_at is not None and loop.time() >= context.deadline_at:
                raise AIProviderCooldownError('AI admission deadline already expired; no dispatch')
            if gate is not None:
                if gate.lock.locked():
                    await _ai_progress(context, {'event': 'provider_wait', 'reason': 'in_flight',
                                                'wait_s': None})
                try:
                    async with asyncio.timeout_at(context.deadline_at):
                        await gate.lock.acquire()
                    acquired = True
                except TimeoutError as exc:
                    raise AIProviderCooldownError('AI admission deadline expired waiting for in-flight request') from exc
                while True:
                    now = loop.time()
                    ready = max(gate.last_finished + gate.min_gap_s, gate.cooldown_until)
                    wait = max(0.0, ready - now)
                    if context.deadline_at is not None and ready >= context.deadline_at:
                        raise AIProviderCooldownError('Provider cooldown/minimum gap cannot fit AI deadline; no dispatch')
                    if not wait:
                        break
                    await _ai_progress(context, {'event': 'provider_wait',
                        'reason': 'cooldown' if gate.cooldown_until > now else 'minimum_gap',
                        'wait_s': round(wait, 3)})
                    await asyncio.sleep(wait)
            if context.deadline_at is not None and loop.time() >= context.deadline_at:
                raise AIProviderCooldownError('AI deadline expired before dispatch')
            if context.dispatch_budget is not None:
                context.dispatch_budget.consume()
            if context.progress_cb is not None:
                observer = loop.create_task(_ai_progress(context, {'event': 'http_dispatch',
                    'provider': getattr(self, 'connection_name', None) or self.provider.value,
                    'model': self.model}))
                _ai_progress_tasks.add(observer)
                observer.add_done_callback(_ai_progress_tasks.discard)
            dispatched = True
            timeout = context.request_timeout_s
            wire_deadline = loop.time() + timeout if timeout is not None else None
            if context.deadline_at is not None:
                wire_deadline = min(wire_deadline, context.deadline_at) if wire_deadline is not None else context.deadline_at
            try:
                # Gate waiting is outside this timeout. Hold the slot through
                # stream consumption, including disconnect/cancel/explicit close.
                async with asyncio.timeout_at(wire_deadline):
                    yield
            except Exception as exc:
                if gate is not None and is_ai_rate_limit_error(exc):
                    response = getattr(exc, 'response', None)
                    headers = getattr(response, 'headers', {}) or {}
                    gate.rate_failed(loop.time(), parse_ai_retry_after(headers.get('retry-after')))
                raise
            else:
                if gate is not None:
                    gate.rate_failures = 0
        finally:
            if acquired:
                if dispatched:
                    gate.last_finished = loop.time()
                gate.lock.release()

    async def chat_stream(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        max_tokens: int = 2048,
        request_context: AIRequestContext | None = None,
    ) -> AsyncIterator[str]:
        """Yield text chunks as they stream in."""
        if self.provider == AIProvider.ANTHROPIC:
            stream = self._anthropic_stream(messages, system, max_tokens)
        elif self.provider in (AIProvider.OPENAI, AIProvider.COMPATIBLE):
            stream = self._openai_stream(messages, system, max_tokens)
        elif self.provider == AIProvider.OPENROUTER:
            stream = self._openrouter_stream(messages, system, max_tokens)
        else:
            raise ValueError(f'Unsupported AI provider: {self.provider}')
        async with self._request_scope(request_context):
            async with aclosing(stream):
                async for chunk in stream:
                    yield chunk

    async def chat(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        max_tokens: int = 2048,
        request_context: AIRequestContext | None = None,
    ) -> str:
        """True non-streaming completion for structured/background stages."""
        async with self._request_scope(request_context):
            if self.provider == AIProvider.ANTHROPIC:
                return await self._anthropic_complete(messages, system, max_tokens)
            if self.provider in (AIProvider.OPENAI, AIProvider.COMPATIBLE):
                return await self._openai_complete(messages, system, max_tokens)
            if self.provider == AIProvider.OPENROUTER:
                return await self._openrouter_complete(messages, system, max_tokens)
            raise ValueError(f"Unsupported AI provider: {self.provider}")

    async def _anthropic_complete(self, messages, system, max_tokens):
        import anthropic
        kwargs = {'api_key': self.api_key}
        if self.max_retries is not None:
            kwargs['max_retries'] = self.max_retries
        async with anthropic.AsyncAnthropic(**kwargs) as client:
            response = await client.messages.create(
                model=self.model, max_tokens=max_tokens,
                system=system or "", messages=messages,
            )
        return "".join(
            getattr(part, "text", "") or ""
            for part in (getattr(response, "content", None) or [])
        )

    def _openai_client_kwargs(self) -> dict:
        kwargs = {
            "api_key": self.api_key,
            "timeout": _openai_request_timeout_seconds(self.provider, self.base_url),
        }
        if self.max_retries is not None:
            kwargs["max_retries"] = self.max_retries
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return kwargs

    def _messages_with_system(self, messages, system):
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)
        return full_messages

    async def _openai_complete(self, messages, system, max_tokens):
        import openai
        m = self.model.lower()
        token_kwargs = (
            {"max_completion_tokens": max_tokens}
            if m.startswith(("gpt-5", "o1", "o3", "o4"))
            else {"max_tokens": max_tokens}
        )
        async with openai.AsyncOpenAI(**self._openai_client_kwargs()) as client:
            response = await client.chat.completions.create(
                model=self.model,
                messages=self._messages_with_system(messages, system),
                stream=False,
                **token_kwargs,
            )
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""

    async def _openrouter_complete(self, messages, system, max_tokens):
        import openai
        client_kwargs = {
            "api_key": self.api_key,
            "base_url": "https://openrouter.ai/api/v1",
            "default_headers": _OPENROUTER_HEADERS,
            "timeout": 55.0,
        }
        if self.max_retries is not None:
            client_kwargs["max_retries"] = self.max_retries
        async with openai.AsyncOpenAI(**client_kwargs) as client:
            response = await client.chat.completions.create(
                model=self.model,
                messages=self._messages_with_system(messages, system),
                stream=False,
                max_tokens=max_tokens,
            )
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""

    # ── Provider implementations ──────────────────────────────────────────────

    async def _anthropic_stream(self, messages, system, max_tokens):
        import anthropic
        kwargs = {'api_key': self.api_key}
        if self.max_retries is not None:
            kwargs['max_retries'] = self.max_retries
        async with anthropic.AsyncAnthropic(**kwargs) as client:
            async with client.messages.stream(
                model=self.model, max_tokens=max_tokens,
                system=system or "", messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield text

    async def _openai_stream(self, messages, system, max_tokens):
        import openai
        client_kwargs = {
            "api_key": self.api_key,
            "timeout": _openai_request_timeout_seconds(self.provider, self.base_url),
        }
        if self.max_retries is not None:
            client_kwargs["max_retries"] = self.max_retries
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
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

        async with openai.AsyncOpenAI(**client_kwargs) as client:
            stream = await client.chat.completions.create(
                model=self.model, messages=full_messages,
                stream=True, **token_kwargs,
            )
            async with stream:
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta

    async def _openrouter_stream(self, messages, system, max_tokens):
        """OpenRouter is OpenAI-compatible — same client, different base URL."""
        import openai
        client_kwargs = dict(api_key=self.api_key,
            base_url="https://openrouter.ai/api/v1", default_headers=_OPENROUTER_HEADERS,
            timeout=55.0)
        if self.max_retries is not None:
            client_kwargs['max_retries'] = self.max_retries
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        # OpenRouter normalises max_tokens vs max_completion_tokens internally
        # for downstream models, so we always send max_tokens here regardless
        # of which underlying provider is being routed to.
        async with openai.AsyncOpenAI(**client_kwargs) as client:
            stream = await client.chat.completions.create(
                model=self.model, messages=full_messages,
                stream=True, max_tokens=max_tokens,
            )
            async with stream:
                async for chunk in stream:
                    # OpenRouter can emit keepalives with an empty choices array.
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
    traffic_policy: dict | None = None,
) -> AIClient:
    """Build one canonical client from Settings-form values.

    Ollama/LM Studio/vLLM are presets over the same compatible transport, not
    separate providers. Local compatible servers may legitimately use no key.

    traffic_policy is an optional normalized policy dict (see
    normalize_ai_traffic_policy). It is stored on the client for the
    admission/cooldown gate to consume.
    """
    name = (provider_name or "").lower().strip()
    if traffic_policy is not None:
        traffic_policy = normalize_ai_traffic_policy(traffic_policy)
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
            traffic_policy=traffic_policy,
        )

    try:
        provider = AIProvider(name)
    except ValueError as exc:
        raise ValueError(f"Unknown AI provider: {provider_name}") from exc
    if not api_key:
        raise AIKeyMissingError(f"API key is required for {name}.")
    return AIClient(provider=provider, api_key=api_key, model=model, base_url=base_url, traffic_policy=traffic_policy)


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


def _resolve_traffic_policy(user_settings) -> dict | None:
    """Resolve the traffic_policy from the active AI connection in ai_connections_json."""
    if not user_settings:
        return None
    raw = getattr(user_settings, "ai_connections_json", None)
    if not raw:
        return None
    try:
        connections = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(connections, dict):
        return None
    active_id = (getattr(user_settings, "ai_connection_id", None) or "").strip().lower()
    if not active_id:
        return None
    cfg = connections.get(active_id)
    if not isinstance(cfg, dict):
        return None
    if cfg.get("archived"):
        return None
    policy = cfg.get("traffic_policy")
    if policy is None:
        return None
    return normalize_ai_traffic_policy(policy)


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
    traffic_policy = _resolve_traffic_policy(user_settings)

    # 1. User-configured BYOK
    user_provider, user_key, u_model, u_base_url = _resolve_user_provider_and_key(user_settings)
    if user_provider and user_key:
        raw_name = (getattr(user_settings, "ai_provider", None) or user_provider.value)
        return make_ai_client(
            provider_name=raw_name,
            api_key=user_key,
            model=u_model or user_model,
            base_url=u_base_url,
            traffic_policy=traffic_policy,
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
