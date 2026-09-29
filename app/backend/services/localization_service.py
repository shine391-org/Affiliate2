# SPDX-License-Identifier: AGPL-3.0-only
"""Canonical Phase-C HQ localization engine.

Donor-derived behavior:
- VideoLingo / SmartSub: word-aware semantic segmentation + validated translation.
- OpenCreator: chunk planner, measured TTS, post-synthesis atempo fitting.
- YouDub: clean background workflow (separation is orchestrated by tool runner).
- pyVideoTrans: bounded video-retiming concept as a last-resort fit strategy.

There is ONE active dubbing engine in this module. Raw Whisper segments are never
treated as immutable TTS units.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from backend.config import settings
from backend.core.localization import (
    LocalizationProfile,
    LocalizationSegment,
    LocalizationTranscript,
    load_profile_store,
    localization_transcript_from_source,
)
from backend.services.dubbing_planner import (
    DubbingChunk,
    DubbingConfig,
    DubbingCue,
    PlanItem,
    atempo_filter,
    borrow_following_silence,
    build_plan,
    chunk_text,
    estimate_spoken_ms,
    fit_timeline,
    required_speed,
)

logger = logging.getLogger(__name__)

_TRANSLATION_BATCH_SIZE = 12
_CONTEXT_CHARS = 10_000
_DUBBING_SYNTHESIS_RULES_VERSION = 1

_PRESET_INSTRUCTIONS = {
    "natural_vietnamese": (
        "Translate faithfully first, then make the wording natural and conversational "
        "in {language}. Preserve the exact intent, entities, relationships, quantities, "
        "steps and factual claims. Do not infer missing context or add sales language."
    ),
    "affiliate_conversational": (
        "Use natural, energetic {language} suitable for a product/review short. "
        "Keep it concise and spoken. Preserve source meaning exactly: do not "
        "invent benefits, prices, CTAs, guarantees or claims."
    ),
    "literal": (
        "Translate closely and faithfully. Prefer semantic accuracy over style; "
        "do not expand, summarize or add information."
    ),
    "custom": (
        "Preserve source meaning, names, numbers and factual claims. Follow the "
        "custom instructions only when they do not require inventing information."
    ),
}


class LocalizationTimingError(RuntimeError):
    def __init__(
        self,
        issues: list[dict],
        *,
        rewrite_count: int = 0,
        all_issues: list[dict] | None = None,
    ):
        self.issues = issues
        self.rewrite_count = int(rewrite_count or 0)
        self.all_issues = list(all_issues or issues)
        preview = "; ".join(
            (
                f"chunk {x.get('chunk_id')}: speed {x.get('required_speed', '?')}x"
                if x.get("chunk_id") is not None
                else f"{x.get('segment_id')}: {x.get('reason')}"
            )
            for x in issues[:5]
        )
        super().__init__(f"Unresolved dubbing timing: {preview}")


@dataclass
class TTSRuntimeConfig:
    provider: object
    default_voice: str
    api_key: str
    model: str | None
    base_url: str | None
    region: str | None
    connection_id: str | None = None


@dataclass
class TimedDubResult:
    transcript: LocalizationTranscript
    dub_track: Path
    block_paths: list[Path]
    issues: list[dict]
    chunk_count: int = 0
    max_speed_factor: float = 1.0
    max_fitted_speed_factor: float = 1.0
    rewrite_count: int = 0
    video_slowdown: float = 1.0
    plan: list[PlanItem] | None = None
    chunks: list[DubbingChunk] | None = None


def resolve_localization_profile(user_settings, profile_id: str | None = None) -> LocalizationProfile:
    store = load_profile_store(getattr(user_settings, "localization_profiles_json", None))
    wanted = profile_id or store.default_profile_id
    for profile in store.profiles:
        if profile.id == wanted:
            return profile
    raise ValueError(f"Localization profile not found: {wanted}")


def materialize_localization_profile(
    user_settings,
    profile: LocalizationProfile,
    *,
    target_language: str,
    for_dub: bool = True,
) -> LocalizationProfile:
    """Resolve non-secret route identities once for a reproducible job snapshot.

    Credentials remain encrypted/live so rotating a key can recover the same
    job. Connection ids/model/base URLs are snapshotted so a later global
    Settings switch cannot silently move an in-flight job to another endpoint.
    """
    translation_clients = _profile_ai_route_clients_compat(
        user_settings, profile, "translation"
    )
    translation_specs = _profile_ai_route_specs(
        user_settings, profile, "translation"
    )
    translation_routes = [
        {
            "connection_id": getattr(client, "connection_name", None)
                or getattr(getattr(client, "provider", None), "value", "ai"),
            # Model affects content and is snapshotted. Connection endpoint is
            # infrastructure: keep it live unless the profile explicitly pins
            # a per-route base_url override.
            "model": getattr(client, "model", None),
            "base_url": (
                translation_specs[index].get("base_url")
                if index < len(translation_specs) else None
            ),
        }
        for index, client in enumerate(translation_clients)
    ]

    data = profile.model_dump()
    data["translation_routes"] = translation_routes
    first = translation_routes[0]
    data.update({
        # Legacy fields stay populated for old manifests/UI/tests, but runtime
        # now consumes translation_routes.
        "translation_provider": first["connection_id"],
        "translation_model": first["model"],
        "translation_base_url": first["base_url"],
        "translation_fallback_provider": (
            translation_routes[1]["connection_id"]
            if len(translation_routes) > 1 else None
        ),
        "translation_fallback_model": (
            translation_routes[1]["model"]
            if len(translation_routes) > 1 else None
        ),
        "translation_fallback_base_url": (
            translation_routes[1]["base_url"]
            if len(translation_routes) > 1 else None
        ),
    })

    if for_dub:
        rewrite_clients = _profile_ai_route_clients_compat(
            user_settings, profile, "rewrite"
        )
        rewrite_specs = _profile_ai_route_specs(
            user_settings, profile, "rewrite"
        )
        rewrite_routes = [
            {
                "connection_id": getattr(client, "connection_name", None)
                    or getattr(getattr(client, "provider", None), "value", "ai"),
                "model": getattr(client, "model", None),
                "base_url": (
                    rewrite_specs[index].get("base_url")
                    if index < len(rewrite_specs) else None
                ),
            }
            for index, client in enumerate(rewrite_clients)
        ]
        data["rewrite_routes"] = rewrite_routes
        first_rewrite = rewrite_routes[0]
        data.update({
            "rewrite_provider": first_rewrite["connection_id"],
            "rewrite_model": first_rewrite["model"],
            "rewrite_base_url": first_rewrite["base_url"],
            "rewrite_fallback_provider": (
                rewrite_routes[1]["connection_id"]
                if len(rewrite_routes) > 1 else None
            ),
            "rewrite_fallback_model": (
                rewrite_routes[1]["model"]
                if len(rewrite_routes) > 1 else None
            ),
            "rewrite_fallback_base_url": (
                rewrite_routes[1]["base_url"]
                if len(rewrite_routes) > 1 else None
            ),
        })

        tts = _tts_config(user_settings, profile, target_language)
        data.update({
            "tts_connection_id": getattr(tts, "connection_id", None)
                or profile.tts_connection_id
                or getattr(user_settings, "tts_connection_id", None),
            "tts_provider": getattr(tts.provider, "value", str(tts.provider)),
            "tts_model": tts.model,
            "tts_voice": tts.default_voice or None,
            "tts_base_url": profile.tts_base_url,
            "tts_region": profile.tts_region,
        })

    return LocalizationProfile.model_validate(data)


async def transcribe_for_localization(
    media_path: Path | str,
    profile: LocalizationProfile,
    *,
    language: str | None = None,
    user_settings=None,
) -> LocalizationTranscript:
    """C5: faster-whisper transcription followed by optional WhisperX alignment."""
    from backend.services.whisper_service import whisper_service

    tx = await whisper_service.transcribe(
        str(media_path),
        language=language,
        quality=profile.asr_quality,
        model_name=profile.asr_model,
    )
    transcript = localization_transcript_from_source(
        text=tx.get("text"),
        language=tx.get("language"),
        source="whisper",
        raw_segments=tx.get("segments") or [],
    )
    if not transcript.segments:
        raise ValueError("No speech detected — nothing to localize.")

    if profile.alignment_mode != "off" or profile.diarization_mode != "off":
        from backend.services.alignment_service import align_localization_transcript
        hf_token = ""
        if user_settings is not None and getattr(user_settings, "huggingface_token_encrypted", None):
            from backend.core.crypto import decrypt_safe
            hf_token = decrypt_safe(user_settings.huggingface_token_encrypted) or ""
        transcript, report = await align_localization_transcript(
            media_path,
            transcript,
            profile,
            huggingface_token=hf_token,
        )
        transcript.metadata["alignment"] = asdict(report)
    return transcript


def _load_json_map(raw: str | None) -> dict[str, dict]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(key).strip().lower(): dict(cfg)
        for key, cfg in value.items()
        if str(key).strip() and isinstance(cfg, dict)
    }


def _saved_ai_provider_config(user_settings, provider_name: str) -> dict:
    """Legacy provider-keyed lookup kept for stored profiles/jobs."""
    provider = (provider_name or "").strip().lower()
    if not provider:
        return {}
    active = (getattr(user_settings, "ai_provider", None) or "").strip().lower()
    if provider == active:
        return {
            "provider": provider,
            "model": getattr(user_settings, "ai_model", None),
            "base_url": getattr(user_settings, "ai_base_url", None),
            "api_key_encrypted": getattr(user_settings, "ai_api_key_encrypted", None),
        }
    registry = _load_json_map(
        getattr(user_settings, "ai_provider_profiles_json", None)
    )
    value = registry.get(provider)
    if not isinstance(value, dict):
        return {}
    return {"provider": provider, **value}


def _saved_ai_connection_config(user_settings, connection_id: str) -> dict:
    """Resolve one stable connection id, falling back to legacy provider ids."""
    connection_id = (connection_id or "").strip().lower()
    if not connection_id:
        return {}

    active_id = (getattr(user_settings, "ai_connection_id", None) or "").strip().lower()
    active_provider = (getattr(user_settings, "ai_provider", None) or "").strip().lower()
    if connection_id == active_id and active_provider:
        return {
            "label": connection_id,
            "provider": active_provider,
            "model": getattr(user_settings, "ai_model", None),
            "base_url": getattr(user_settings, "ai_base_url", None),
            "api_key_encrypted": getattr(user_settings, "ai_api_key_encrypted", None),
        }

    registry = _load_json_map(
        getattr(user_settings, "ai_connections_json", None)
    )
    value = registry.get(connection_id)
    if isinstance(value, dict) and value.get("provider"):
        return value

    # Lazy migration compatibility: old profiles used provider name as identity.
    return _saved_ai_provider_config(user_settings, connection_id)


def _ai_client_for_connection(
    user_settings,
    connection_id: str | None,
    *,
    model: str | None = None,
    base_url: str | None = None,
):
    from backend.core.ai_provider import (
        AIProvider, COMPATIBLE_PRESETS, compatible_base_url,
        get_ai_client, make_ai_client,
    )
    from backend.core.crypto import decrypt_safe

    requested = (connection_id or "").strip().lower()
    active_id = (getattr(user_settings, "ai_connection_id", None) or "").strip().lower()
    active_provider = (getattr(user_settings, "ai_provider", None) or "").strip().lower()

    if not requested or requested == active_id:
        ai = get_ai_client(user_settings)
        if model:
            ai.model = model
        if base_url:
            ai.base_url = base_url.rstrip("/")
        ai.max_retries = 0
        ai.connection_name = requested or active_id or active_provider or getattr(ai.provider, "value", str(ai.provider))
        return ai

    saved = _saved_ai_connection_config(user_settings, requested)
    if saved:
        provider = str(saved.get("provider") or "").strip().lower()
        encrypted = saved.get("api_key_encrypted")
        key = decrypt_safe(encrypted) if encrypted else ""
        ai = make_ai_client(
            provider_name=provider,
            api_key=key or "",
            model=(model or saved.get("model")),
            base_url=(base_url or saved.get("base_url")),
        )
        ai.max_retries = 0
        ai.connection_name = requested
        return ai

    # Environment-backed legacy jobs may have snapshotted provider identity
    # without a DB connection row. Match only the exact effective env binding.
    try:
        env_ai = get_ai_client(None)
        matches_env = requested == getattr(env_ai.provider, "value", "")
        if requested in COMPATIBLE_PRESETS and env_ai.provider == AIProvider.COMPATIBLE:
            preset_base = compatible_base_url(requested, base_url)
            matches_env = bool(
                preset_base
                and str(getattr(env_ai, "base_url", "") or "").rstrip("/")
                    == preset_base.rstrip("/")
            )
        if matches_env:
            if model:
                env_ai.model = model
            if base_url:
                env_ai.base_url = base_url.rstrip("/")
            env_ai.max_retries = 0
            env_ai.connection_name = requested
            return env_ai
    except Exception:
        pass

    raise ValueError(f"Unknown AI connection: {requested}")


def _legacy_ai_route_specs(
    user_settings,
    profile: LocalizationProfile,
    purpose: str,
) -> list[dict]:
    if purpose == "rewrite":
        primary = {
            "connection_id": profile.rewrite_provider
                or getattr(user_settings, "ai_connection_id", None)
                or getattr(user_settings, "ai_provider", None),
            "model": profile.rewrite_model,
            "base_url": profile.rewrite_base_url,
        }
        fallback = {
            "connection_id": profile.rewrite_fallback_provider,
            "model": profile.rewrite_fallback_model,
            "base_url": profile.rewrite_fallback_base_url,
        }
    else:
        primary = {
            "connection_id": profile.translation_provider
                or getattr(user_settings, "ai_connection_id", None)
                or getattr(user_settings, "ai_provider", None),
            "model": profile.translation_model,
            "base_url": profile.translation_base_url,
        }
        fallback = {
            "connection_id": profile.translation_fallback_provider,
            "model": profile.translation_fallback_model,
            "base_url": profile.translation_fallback_base_url,
        }
    routes = [primary]
    if any(fallback.values()):
        # Same connection with another model remains a valid second route.
        fallback["connection_id"] = fallback["connection_id"] or primary["connection_id"]
        routes.append(fallback)
    return routes


def _profile_ai_route_specs(
    user_settings,
    profile: LocalizationProfile,
    purpose: str = "translation",
) -> list[dict]:
    configured = profile.rewrite_routes if purpose == "rewrite" else profile.translation_routes
    if configured:
        return [route.model_dump() for route in configured]
    return _legacy_ai_route_specs(user_settings, profile, purpose)


def _profile_ai_route_clients(
    user_settings,
    profile: LocalizationProfile,
    purpose: str = "translation",
) -> list:
    """Resolve the complete ordered route chain and fail closed on bad config."""
    clients = []
    errors = []
    for index, spec in enumerate(_profile_ai_route_specs(user_settings, profile, purpose)):
        connection_id = str(spec.get("connection_id") or "").strip().lower()
        if not connection_id:
            errors.append(f"route {index + 1}: missing connection_id")
            continue
        try:
            client = _ai_client_for_connection(
                user_settings,
                connection_id,
                model=spec.get("model"),
                base_url=spec.get("base_url"),
            )
        except Exception as exc:
            errors.append(f"route {index + 1} ({connection_id}): {exc}")
            continue
        clients.append(client)

    if errors:
        raise ValueError(
            f"Invalid {purpose} AI route configuration: " + "; ".join(errors)
        )
    if not clients:
        raise ValueError(f"No usable {purpose} AI route is configured.")
    return clients


def _profile_ai_route_clients_compat(
    user_settings,
    profile: LocalizationProfile,
    purpose: str = "translation",
) -> list:
    """Route resolver with a narrow seam for legacy tests/donor integrations.

    Production implementations of _profile_ai_client/_fallback both delegate
    back to the strict route resolver, so an invalid configured route still
    fails closed. A monkeypatched legacy accessor can still supply a client in
    isolated tests without forcing every donor-era test to understand v2
    connection registries.
    """
    try:
        return _profile_ai_route_clients(user_settings, profile, purpose)
    except Exception as route_exc:
        try:
            primary = _profile_ai_client(user_settings, profile, purpose)
        except Exception:
            raise route_exc
        clients = [primary]
        # Only consult the fallback accessor when the profile actually declares
        # a second route. This preserves donor-era tests that monkeypatch only
        # the primary accessor, while explicitly configured bad fallbacks still
        # fail closed.
        if len(_profile_ai_route_specs(user_settings, profile, purpose)) > 1:
            try:
                fallback = _profile_ai_fallback_client(
                    user_settings, profile, purpose, primary_ai=primary,
                )
            except Exception:
                raise route_exc
            if fallback is not None:
                clients.append(fallback)
        return clients


def _profile_ai_client(
    user_settings,
    profile: LocalizationProfile,
    purpose: str = "translation",
):
    """Backward-compatible primary route accessor."""
    return _profile_ai_route_clients(user_settings, profile, purpose)[0]


def _profile_ai_fallback_client(
    user_settings,
    profile: LocalizationProfile,
    purpose: str = "translation",
    *,
    primary_ai=None,
):
    """Backward-compatible first fallback accessor."""
    clients = _profile_ai_route_clients(user_settings, profile, purpose)
    return clients[1] if len(clients) > 1 else None


class _AIStageRouteState:
    def __init__(self, clients: list):
        if not clients:
            raise ValueError("AI route state requires at least one client")
        self.clients = list(clients)
        self.sticky_index = 0
        self.failure_chain: list[dict] = []

    @property
    def current(self):
        return self.clients[self.sticky_index]




async def prepare_semantic_transcript(
    transcript: LocalizationTranscript,
    *,
    profile: LocalizationProfile,
    user_settings,
    progress_cb=None,
) -> LocalizationTranscript:
    from backend.services.semantic_segmentation import (
        normalize_semantic_cues,
        refine_semantic_cues_with_ai,
    )

    normalized = normalize_semantic_cues(transcript)
    if not profile.semantic_segmentation or len(normalized.segments) <= 1:
        return normalized

    try:
        route_clients = _profile_ai_route_clients_compat(
            user_settings, profile, "translation"
        )
    except Exception as exc:
        # Semantic AI is optional. Configuration errors are surfaced by the
        # localization preflight before heavy work; if a resumed legacy job
        # reaches here, degrade safely rather than hanging/failing the video.
        logger.warning(
            "Semantic AI route unavailable; using deterministic segmentation: %s",
            exc,
        )
        if progress_cb is not None:
            await _emit_ai_progress(progress_cb, {
                "event": "deterministic_fallback",
                "stage": "semantic",
                "error": str(exc)[:220],
            })
        return normalized

    route_state = {"sticky_index": 0, "failure_chain": []}
    refined = await refine_semantic_cues_with_ai(
        normalized,
        route_clients[0],
        fallback_ai=(route_clients[1] if len(route_clients) > 1 else None),
        route_clients=route_clients,
        route_state=route_state,
        attempt_timeout_s=profile.semantic_ai_attempt_timeout_s,
        max_attempts=profile.semantic_ai_retry_attempts,
        total_timeout_s=profile.semantic_ai_total_timeout_s,
        progress_cb=progress_cb,
    )
    selected_index = min(
        int(route_state.get("sticky_index", 0) or 0),
        len(route_clients) - 1,
    )
    refined.metadata["semantic_ai"] = {
        "selected_connection_id": getattr(
            route_clients[selected_index], "connection_name", None
        ),
        "failure_chain": list(route_state.get("failure_chain") or []),
        "sticky_route_index": selected_index,
    }
    return refined


def _strip_json_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _translation_context(transcript: LocalizationTranscript) -> str:
    text = (transcript.text or "").strip()
    if not text:
        text = " ".join(s.source_text for s in transcript.segments)
    if len(text) <= _CONTEXT_CHARS:
        return text
    half = _CONTEXT_CHARS // 2
    return text[:half] + "\n...[context truncated]...\n" + text[-half:]


def _translation_prompt(
    *,
    target_language: str,
    batch: list[LocalizationSegment],
    transcript_context: str,
    profile: LocalizationProfile,
    for_dub: bool,
) -> str:
    preset = _PRESET_INSTRUCTIONS[profile.translation_preset].format(
        language=target_language
    )
    glossary = (
        "\n".join(f"- {src} => {dst}" for src, dst in profile.glossary.items())
        or "(none)"
    )
    custom = profile.translation_instructions or "(none)"
    items = [
        {
            "id": seg.id,
            "speaker_id": seg.speaker_id,
            "text": seg.source_text,
            "source_duration_ms": seg.source_end_ms - seg.source_start_ms,
        }
        for seg in batch
    ]
    timing = (
        "For dubbing, keep each translation concise and naturally speakable. "
        "The downstream chunk planner will combine adjacent lines and measure real TTS."
        if for_dub
        else "Keep each segment aligned to the same semantic unit."
    )
    return f"""You are localizing a video into {target_language}.

SEMANTIC GUARDRAILS (editable per profile in Settings):
{profile.translation_guardrails}

LOCKED PROTOCOL:
- If editable semantic guardrails or profile instructions conflict with this protocol, this protocol wins.
- Preserve segment IDs and order.
- Return every input ID exactly once and no extra IDs.
- Return only the required JSON array; do not add prose or markdown.
- {timing}

STYLE:
{preset}

CUSTOM INSTRUCTIONS:
{custom}

GLOSSARY:
{glossary}

FULL SOURCE CONTEXT:
{transcript_context}

SEGMENTS:
{json.dumps(items, ensure_ascii=False)}

Return ONLY a JSON array:
[{{"id":"segment-id","text":"translated speech"}}]
Every input ID must appear exactly once and no extra IDs are allowed."""


def _validate_translation_payload(raw: str, batch: list[LocalizationSegment]) -> list[str]:
    payload = json.loads(_strip_json_fence(raw))
    if not isinstance(payload, list):
        raise ValueError("translation response must be a JSON array")
    expected = [s.id for s in batch]
    if len(payload) != len(expected):
        raise ValueError("translation response count mismatch")
    seen: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("translation item must be an object")
        seg_id = str(item.get("id") or "")
        text = str(item.get("text") or "").strip()
        if not seg_id or not text or seg_id in seen:
            raise ValueError("translation item has missing/duplicate id or empty text")
        seen[seg_id] = text
    if set(seen) != set(expected):
        raise ValueError("translation response IDs do not match input IDs")
    return [seen[seg_id] for seg_id in expected]


async def _emit_ai_progress(progress_cb, payload: dict) -> None:
    if progress_cb is None:
        return
    result = progress_cb(payload)
    if asyncio.iscoroutine(result):
        await result


def _safe_ai_error_text(exc: Exception, client=None, limit: int = 240) -> str:
    text = str(exc)
    key = str(getattr(client, "api_key", "") or "")
    if key and len(key) >= 4:
        text = text.replace(key, "***")
    return text[:limit]


def _attach_ai_failure_context(exc: Exception, stage: str, route_state: _AIStageRouteState | None):
    if route_state is None:
        return exc
    try:
        exc.ai_stage = stage
        exc.ai_failure_chain = list(route_state.failure_chain)
        exc.ai_selected_connection = getattr(route_state.current, "connection_name", None)
    except Exception:
        pass
    return exc


async def _call_ai_chat_resilient(
    ai,
    fallback_ai,
    messages: list[dict],
    max_tokens: int = 3000,
    max_attempts: int = 3,
    *,
    attempt_timeout_s: float = 75,
    total_timeout_s: float = 180,
    progress_cb=None,
    stage: str = "translation",
    route_state: _AIStageRouteState | None = None,
    deadline_at: float | None = None,
) -> str:
    """One owner for AI retry/fallback with hard wall-clock budgets.

    SDK retries are disabled by the callers. Each attempt is wrapped in
    asyncio.wait_for so a half-open SSE/proxy transport cannot keep a stage
    stuck forever. A saved fallback provider/model is tried after the primary.
    """
    import random
    from backend.core.ai_provider import is_transient_ai_error

    loop = asyncio.get_running_loop()
    local_deadline = loop.time() + float(total_timeout_s)
    deadline = min(local_deadline, deadline_at) if deadline_at is not None else local_deadline
    last_error: Exception | None = None

    if route_state is not None:
        start_index = min(route_state.sticky_index, len(route_state.clients) - 1)
        usable_clients = [
            (
                "primary" if idx == 0 else f"fallback-{idx}",
                route_state.clients[idx],
                idx,
            )
            for idx in range(start_index, len(route_state.clients))
        ]
    else:
        raw_clients = [ai] + ([fallback_ai] if fallback_ai is not None else [])
        usable_clients = [
            ("primary" if idx == 0 else f"fallback-{idx}", client, idx)
            for idx, client in enumerate(raw_clients)
            if client is not None
        ]

    for client_pos, (role, client, route_index) in enumerate(usable_clients):
        if hasattr(client, "max_retries"):
            client.max_retries = 0

        # Reserve a fair share of the remaining wall-clock budget for every
        # provider still in the chain. Otherwise 3 hung primary attempts can
        # consume the entire global deadline and the configured fallback never
        # gets a single request.
        providers_left = len(usable_clients) - client_pos
        provider_deadline = min(
            deadline,
            loop.time() + max(0.0, (deadline - loop.time()) / max(providers_left, 1)),
        )

        for attempt in range(1, max_attempts + 1):
            global_remaining = deadline - loop.time()
            provider_remaining = provider_deadline - loop.time()
            if global_remaining <= 0:
                timeout_error = TimeoutError(
                    f"{stage} AI deadline exceeded after {int(total_timeout_s)}s"
                )
                raise _attach_ai_failure_context(timeout_error, stage, route_state) from last_error
            if provider_remaining <= 0:
                break

            timeout_s = min(
                float(attempt_timeout_s),
                global_remaining,
                provider_remaining,
            )
            await _emit_ai_progress(progress_cb, {
                "event": "attempt",
                "stage": stage,
                "role": role,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "provider": getattr(client, "connection_name", None) or getattr(getattr(client, "provider", None), "value", str(getattr(client, "provider", ""))),
                "model": getattr(client, "model", None),
                "timeout_s": round(timeout_s, 1),
            })
            try:
                result = await asyncio.wait_for(
                    client.chat(messages=messages, max_tokens=max_tokens),
                    timeout=timeout_s,
                )
                if route_state is not None:
                    route_state.sticky_index = route_index
                return result
            except Exception as exc:
                last_error = exc
                failure = {
                    "connection_id": getattr(client, "connection_name", None),
                    "provider": getattr(getattr(client, "provider", None), "value", str(getattr(client, "provider", ""))),
                    "model": getattr(client, "model", None),
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "error": _safe_ai_error_text(exc, client),
                }
                if route_state is not None:
                    route_state.failure_chain.append(failure)
                await _emit_ai_progress(progress_cb, {
                    "event": "error",
                    "stage": stage,
                    "role": role,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "provider": getattr(client, "connection_name", None) or getattr(getattr(client, "provider", None), "value", str(getattr(client, "provider", ""))),
                    "model": getattr(client, "model", None),
                    "error": _safe_ai_error_text(exc, client),
                })
                transient = is_transient_ai_error(exc)
                # Non-transient errors do not deserve repeated attempts on the
                # same provider, but a separately configured fallback may still
                # be healthy (e.g. bad/deprecated primary model).
                if not transient:
                    break
                if attempt < max_attempts:
                    remaining = min(
                        deadline - loop.time(),
                        provider_deadline - loop.time(),
                    )
                    if remaining <= 0:
                        break
                    backoff = min(
                        8.0,
                        (1.5 ** (attempt - 1)) + random.uniform(0.1, 0.4),
                        remaining,
                    )
                    await _emit_ai_progress(progress_cb, {
                        "event": "retry_wait",
                        "stage": stage,
                        "role": role,
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "backoff_s": round(backoff, 2),
                    })
                    await asyncio.sleep(max(0.0, backoff))

        next_pos = client_pos + 1
        if next_pos < len(usable_clients):
            _next_role, next_client, _next_index = usable_clients[next_pos]
            await _emit_ai_progress(progress_cb, {
                "event": "switch_fallback",
                "stage": stage,
                "provider": getattr(next_client, "connection_name", None) or getattr(getattr(next_client, "provider", None), "value", str(getattr(next_client, "provider", ""))),
                "model": getattr(next_client, "model", None),
            })

    if loop.time() >= deadline:
        timeout_error = TimeoutError(
            f"{stage} AI deadline exceeded after {int(total_timeout_s)}s"
        )
        raise _attach_ai_failure_context(timeout_error, stage, route_state) from last_error
    if last_error is not None:
        raise _attach_ai_failure_context(last_error, stage, route_state)
    raise RuntimeError(f"{stage} AI has no usable provider")


async def _translate_batch_resilient(
    ai,
    batch: list[LocalizationSegment],
    *,
    route_state: _AIStageRouteState | None = None,
    target_language: str,
    context: str,
    profile: LocalizationProfile,
    for_dub: bool,
    fallback_ai=None,
    progress_cb=None,
    deadline_at: float | None = None,
) -> tuple[list[str], set[int]]:
    if deadline_at is None:
        deadline_at = asyncio.get_running_loop().time() + float(
            profile.translation_total_timeout_s
        )
    prompt = _translation_prompt(
        target_language=target_language,
        batch=batch,
        transcript_context=context,
        profile=profile,
        for_dub=for_dub,
    )
    raw = await _call_ai_chat_resilient(
        ai,
        fallback_ai,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=3000,
        max_attempts=profile.translation_retry_attempts,
        attempt_timeout_s=profile.translation_attempt_timeout_s,
        total_timeout_s=profile.translation_total_timeout_s,
        progress_cb=progress_cb,
        stage="translation",
        route_state=route_state,
        deadline_at=deadline_at,
    )
    try:
        return _validate_translation_payload(raw, batch), set()
    except (json.JSONDecodeError, ValueError) as exc:
        # A transport-level 200 with unusable structured output is still a
        # route failure for this structured stage. Advance to the next route
        # before recursively shrinking the batch; otherwise one unhealthy model
        # can silently degrade every singleton back to source text while a
        # configured fallback is never tried.
        if route_state is not None and route_state.sticky_index + 1 < len(route_state.clients):
            current = route_state.current
            route_state.failure_chain.append({
                "connection_id": getattr(current, "connection_name", None),
                "provider": getattr(getattr(current, "provider", None), "value", str(getattr(current, "provider", ""))),
                "model": getattr(current, "model", None),
                "attempt": 1,
                "error_type": "InvalidStructuredOutput",
                "error": str(exc)[:240],
            })
            route_state.sticky_index += 1
            next_client = route_state.current
            await _emit_ai_progress(progress_cb, {
                "event": "switch_fallback",
                "stage": "translation",
                "reason": "invalid_structured_output",
                "provider": getattr(next_client, "connection_name", None) or getattr(getattr(next_client, "provider", None), "value", str(getattr(next_client, "provider", ""))),
                "model": getattr(next_client, "model", None),
            })
            return await _translate_batch_resilient(
                ai, batch, route_state=route_state,
                target_language=target_language, context=context,
                profile=profile, for_dub=for_dub, fallback_ai=fallback_ai,
                progress_cb=progress_cb, deadline_at=deadline_at,
            )

        if len(batch) == 1:
            logger.warning("Translation shape failed for %s: %s", batch[0].id, exc)
            return [batch[0].source_text], {0}
        await _emit_ai_progress(progress_cb, {
            "event": "shape_split",
            "stage": "translation",
            "batch_size": len(batch),
            "error": str(exc)[:180],
        })
        mid = len(batch) // 2
        left, lf = await _translate_batch_resilient(
            ai, batch[:mid], route_state=route_state,
            target_language=target_language, context=context,
            profile=profile, for_dub=for_dub, fallback_ai=fallback_ai,
            progress_cb=progress_cb, deadline_at=deadline_at,
        )
        right, rf = await _translate_batch_resilient(
            ai, batch[mid:], route_state=route_state,
            target_language=target_language, context=context,
            profile=profile, for_dub=for_dub, fallback_ai=fallback_ai,
            progress_cb=progress_cb, deadline_at=deadline_at,
        )
        return left + right, lf | {mid + i for i in rf}


async def translate_transcript(
    transcript: LocalizationTranscript,
    *,
    target_language: str,
    profile: LocalizationProfile,
    user_settings,
    for_dub: bool,
    checkpoint_dir: Path | None = None,
    progress_cb=None,
) -> LocalizationTranscript:
    import hashlib
    route_clients = _profile_ai_route_clients_compat(
        user_settings, profile, "translation"
    )
    route_state = _AIStageRouteState(route_clients)
    ai = route_clients[0]
    fallback_ai = route_clients[1] if len(route_clients) > 1 else None

    out = transcript.model_copy(deep=True)
    context = _translation_context(transcript)
    failed_total = 0

    checkpoint_file = (checkpoint_dir / "checkpoint.json") if checkpoint_dir else None
    semantic_sig = hashlib.sha256(
        "".join(f"{s.id}:{s.source_text}" for s in transcript.segments).encode("utf-8")
    ).hexdigest()[:16]
    glossary_str = json.dumps(getattr(profile, "glossary", {}) or {}, sort_keys=True, ensure_ascii=False)
    glossary_hash = hashlib.sha256(glossary_str.encode("utf-8")).hexdigest()[:16]
    route_specs = _profile_ai_route_specs(user_settings, profile, "translation")
    route_fingerprint = [
        {
            "connection_id": getattr(client, "connection_name", None),
            "provider": getattr(getattr(client, "provider", None), "value", str(getattr(client, "provider", ""))),
            "model": getattr(client, "model", ""),
            "base_url_override": str(
                (route_specs[index].get("base_url") if index < len(route_specs) else "") or ""
            ).rstrip("/"),
        }
        for index, client in enumerate(route_clients)
    ]
    checkpoint_meta = {
        "semantic_hash": semantic_sig,
        "target_language": target_language,
        "routes": route_fingerprint,
        "preset": profile.translation_preset,
        "instructions": (getattr(profile, "translation_instructions", "") or "").strip(),
        "guardrails": (getattr(profile, "translation_guardrails", "") or "").strip(),
        "glossary_hash": glossary_hash,
    }
    completed_batches: dict[str, dict] = {}
    if checkpoint_file and checkpoint_file.exists():
        try:
            cp_data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            if all(cp_data.get(k) == v for k, v in checkpoint_meta.items()):
                completed_batches = cp_data.get("batches", {})
                logger.info(
                    "Resuming translation from checkpoint (%d completed batches)",
                    len(completed_batches),
                )
            else:
                logger.info("Translation configuration changed; invalidating incompatible checkpoint")
        except Exception as cp_err:
            logger.warning("Could not read translation checkpoint: %s", cp_err)

    for start in range(0, len(out.segments), _TRANSLATION_BATCH_SIZE):
        batch = out.segments[start:start + _TRANSLATION_BATCH_SIZE]
        batch_key = str(start)
        if batch_key in completed_batches and not completed_batches[batch_key].get("failed"):
            texts = completed_batches[batch_key]["texts"]
            failed = set()
        else:
            total_batches = max(
                1,
                (len(out.segments) + _TRANSLATION_BATCH_SIZE - 1) // _TRANSLATION_BATCH_SIZE,
            )
            batch_number = (start // _TRANSLATION_BATCH_SIZE) + 1
            await _emit_ai_progress(progress_cb, {
                "event": "batch",
                "stage": "translation",
                "batch": batch_number,
                "total_batches": total_batches,
            })
            texts, failed = await _translate_batch_resilient(
                ai, batch, route_state=route_state,
                target_language=target_language,
                context=context,
                profile=profile,
                for_dub=for_dub,
                fallback_ai=fallback_ai,
                progress_cb=progress_cb,
            )
            if failed:
                completed_batches.pop(batch_key, None)
            else:
                completed_batches[batch_key] = {"texts": texts, "failed": []}
            if checkpoint_file:
                try:
                    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
                    tmp_cp = checkpoint_file.with_suffix(".tmp")
                    tmp_cp.write_text(
                        json.dumps({**checkpoint_meta, "batches": completed_batches}, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    tmp_cp.replace(checkpoint_file)
                except Exception as cp_save_err:
                    logger.warning("Could not persist translation batch checkpoint: %s", cp_save_err)

        failed_total += len(failed)
        for idx, (seg, text) in enumerate(zip(batch, texts)):
            seg.translated_text = text
            seg.translation_status = "untranslated" if idx in failed else "success"
            if not for_dub:
                seg.target_start_ms = seg.source_start_ms
                seg.target_end_ms = seg.source_end_ms
                seg.timing_status = "fit"
                seg.qc_status = "review" if idx in failed else "pass"

    if failed_total == len(out.segments):
        raise RuntimeError("Translation failed for every segment.")
    if failed_total and profile.qc_profile == "strict":
        raise RuntimeError(
            f"Translation left {failed_total} segment(s) untranslated under strict QC."
        )
    out.metadata["translation_ai"] = {
        "selected_connection_id": getattr(route_state.current, "connection_name", None),
        "route_chain": route_fingerprint,
        "failure_chain": list(route_state.failure_chain),
        "sticky_route_index": route_state.sticky_index,
    }
    return out


_DEFAULT_SPEECH_VOICES = {
    "vi": "vi-VN-HoaiMyNeural",
    "vietnamese": "vi-VN-HoaiMyNeural",
    "en": "en-US-AndrewMultilingualNeural",
    "english": "en-US-AndrewMultilingualNeural",
    "es": "es-ES-XimenaNeural",
    "spanish": "es-ES-XimenaNeural",
    "fr": "fr-FR-VivienneMultilingualNeural",
    "french": "fr-FR-VivienneMultilingualNeural",
    "de": "de-DE-SeraphinaMultilingualNeural",
    "german": "de-DE-SeraphinaMultilingualNeural",
    "it": "it-IT-GiuseppeMultilingualNeural",
    "italian": "it-IT-GiuseppeMultilingualNeural",
    "pt": "pt-BR-ThalitaMultilingualNeural",
    "portuguese": "pt-BR-ThalitaMultilingualNeural",
    "ja": "ja-JP-NanamiNeural",
    "japanese": "ja-JP-NanamiNeural",
    "ko": "ko-KR-HyunsuMultilingualNeural",
    "korean": "ko-KR-HyunsuMultilingualNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "chinese": "zh-CN-XiaoxiaoNeural",
    "nl": "nl-NL-ColetteNeural",
    "dutch": "nl-NL-ColetteNeural",
    "pl": "pl-PL-ZofiaNeural",
    "polish": "pl-PL-ZofiaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "russian": "ru-RU-SvetlanaNeural",
    "ar": "ar-SA-ZariyahNeural",
    "arabic": "ar-SA-ZariyahNeural",
    "hi": "hi-IN-SwaraNeural",
    "hindi": "hi-IN-SwaraNeural",
    "tr": "tr-TR-EmelNeural",
    "turkish": "tr-TR-EmelNeural",
    "sv": "sv-SE-SofieNeural",
    "swedish": "sv-SE-SofieNeural",
    "da": "da-DK-ChristelNeural",
    "danish": "da-DK-ChristelNeural",
    "no": "nb-NO-PernilleNeural",
    "norwegian": "nb-NO-PernilleNeural",
    "fi": "fi-FI-NooraNeural",
    "finnish": "fi-FI-NooraNeural",
}


def _default_speech_voice(target_language: str) -> str:
    key = (target_language or "").strip().lower()
    return _DEFAULT_SPEECH_VOICES.get(key, "en-US-AndrewMultilingualNeural")


def _tts_config(user_settings, profile: LocalizationProfile, target_language: str) -> TTSRuntimeConfig:
    from backend.core.crypto import decrypt_safe
    from backend.services.tts_service import DEFAULT_VOICES, TTSProvider

    active_connection_id = (
        getattr(user_settings, "tts_connection_id", None)
        if user_settings is not None else None
    )
    active_connection_id = (active_connection_id or "").strip().lower() or None
    active_provider = (
        getattr(user_settings, "tts_provider", None)
        if user_settings is not None else None
    )
    active_provider = (active_provider or "").strip().lower() or None
    requested_connection_id = (profile.tts_connection_id or "").strip().lower() or None

    registry = _load_json_map(
        getattr(user_settings, "tts_connections_json", None)
        if user_settings is not None else None
    )

    connection_cfg: dict = {}
    connection_id = requested_connection_id

    if requested_connection_id:
        if requested_connection_id == active_connection_id and active_provider:
            connection_cfg = {
                "provider": active_provider,
                "voice": getattr(user_settings, "preferred_tts_voice", None),
                "model": getattr(user_settings, "tts_model", None),
                "base_url": getattr(user_settings, "tts_base_url", None),
                "region": getattr(user_settings, "tts_region", None),
                "api_key_encrypted": getattr(user_settings, "tts_api_key_encrypted", None),
            }
        else:
            connection_cfg = dict(registry.get(requested_connection_id) or {})
        if not connection_cfg:
            raise ValueError(f"Unknown TTS connection: {requested_connection_id}")
    else:
        requested_provider = (profile.tts_provider or "").strip().lower() or None
        # Legacy/materialized jobs may carry only provider. Resolve it to one
        # saved connection when unambiguous so changing global active TTS does
        # not silently alter the old job.
        if requested_provider and requested_provider != active_provider:
            matches = [
                (cid, cfg) for cid, cfg in registry.items()
                if str(cfg.get("provider") or "").strip().lower() == requested_provider
            ]
            if len(matches) == 1:
                connection_id, connection_cfg = matches[0]
            elif len(matches) > 1:
                raise ValueError(
                    f"Multiple TTS connections use provider '{requested_provider}'. "
                    "Pin tts_connection_id in the localization profile."
                )
            elif requested_provider == TTSProvider.EDGE_TTS.value:
                connection_cfg = {"provider": requested_provider}
                connection_id = None
            else:
                # Legacy provider registry fallback.
                legacy = _load_json_map(
                    getattr(user_settings, "tts_provider_profiles_json", None)
                    if user_settings is not None else None
                ).get(requested_provider) or {}
                if legacy:
                    connection_cfg = {"provider": requested_provider, **legacy}
                    connection_id = requested_provider
                else:
                    raise ValueError(
                        f"Profile requests TTS provider '{requested_provider}', "
                        "but no saved TTS connection exists for that provider."
                    )
        else:
            connection_id = active_connection_id
            connection_cfg = {
                "provider": active_provider or TTSProvider.EDGE_TTS.value,
                "voice": getattr(user_settings, "preferred_tts_voice", None)
                    if user_settings is not None else None,
                "model": getattr(user_settings, "tts_model", None)
                    if user_settings is not None else None,
                "base_url": getattr(user_settings, "tts_base_url", None)
                    if user_settings is not None else None,
                "region": getattr(user_settings, "tts_region", None)
                    if user_settings is not None else None,
                "api_key_encrypted": getattr(user_settings, "tts_api_key_encrypted", None)
                    if user_settings is not None else None,
            }

    raw_provider = (
        (profile.tts_provider or "").strip().lower()
        or str(connection_cfg.get("provider") or "").strip().lower()
        or TTSProvider.EDGE_TTS.value
    )
    try:
        provider = TTSProvider(raw_provider)
    except ValueError as exc:
        raise ValueError(f"Unsupported TTS provider: {raw_provider}") from exc

    # Provider type is part of connection identity. A materialized profile may
    # pin it for reproducibility, but it must not reinterpret another
    # connection's secret as a different provider.
    cfg_provider = str(connection_cfg.get("provider") or raw_provider).strip().lower()
    if connection_cfg and cfg_provider and cfg_provider != raw_provider:
        raise ValueError(
            f"TTS connection '{connection_id}' is {cfg_provider}, "
            f"but profile requests {raw_provider}."
        )

    settings_voice = connection_cfg.get("voice")
    explicit_voice = profile.tts_voice or settings_voice
    if explicit_voice:
        voice = explicit_voice
    elif provider in {TTSProvider.EDGE_TTS, TTSProvider.AZURE}:
        voice = _default_speech_voice(target_language)
    else:
        voice = DEFAULT_VOICES.get(provider, "")

    if provider == TTSProvider.ELEVENLABS and not voice:
        raise ValueError("ElevenLabs TTS requires a voice ID.")

    encrypted_tts = connection_cfg.get("api_key_encrypted")
    api_key = decrypt_safe(encrypted_tts) if encrypted_tts else ""

    # OpenAI TTS can reuse an OpenAI AI connection key, but only from an
    # explicitly OpenAI connection. Never reinterpret another provider's key.
    if provider == TTSProvider.OPENAI_TTS and not api_key:
        ai_encrypted = None
        if user_settings is not None:
            active_ai_provider = (getattr(user_settings, "ai_provider", None) or "").strip().lower()
            if active_ai_provider == "openai":
                ai_encrypted = getattr(user_settings, "ai_api_key_encrypted", None)
            if not ai_encrypted:
                ai_connections = _load_json_map(
                    getattr(user_settings, "ai_connections_json", None)
                )
                openai_matches = [
                    cfg for cfg in ai_connections.values()
                    if str(cfg.get("provider") or "").strip().lower() == "openai"
                    and cfg.get("api_key_encrypted")
                ]
                if len(openai_matches) == 1:
                    ai_encrypted = openai_matches[0].get("api_key_encrypted")
            if not ai_encrypted:
                legacy_openai = _load_json_map(
                    getattr(user_settings, "ai_provider_profiles_json", None)
                ).get("openai") or {}
                ai_encrypted = legacy_openai.get("api_key_encrypted")
        if ai_encrypted:
            api_key = decrypt_safe(ai_encrypted) or ""
        api_key = api_key or settings.OPENAI_API_KEY

    # Model/base/region are snapshotted into the effective profile for
    # reproducibility; otherwise inherit from the selected connection.
    model = profile.tts_model or connection_cfg.get("model")
    base_url = profile.tts_base_url or connection_cfg.get("base_url")
    region = profile.tts_region or connection_cfg.get("region")

    if provider == TTSProvider.ELEVENLABS:
        from backend.services.elevenlabs_tts_service import canonicalize_base_url
        base_url = canonicalize_base_url(base_url)

    return TTSRuntimeConfig(
        provider, voice or "", api_key, model, base_url, region,
        connection_id=connection_id,
    )


async def validate_localization_tts_preflight(
    user_settings,
    profile: LocalizationProfile,
    target_language: str,
) -> dict:
    """Fail before separation/ASR when the full-dub TTS binding is unusable."""
    from backend.services import tts_service

    runtime = _tts_config(user_settings, profile, target_language)

    # GPT-SoVITS donor adapter is intentionally language-limited. Validate that
    # deterministic incompatibility before Demucs/ASR/translation consume time.
    if runtime.provider == tts_service.TTSProvider.GPT_SOVITS:
        from backend.services.gpt_sovits_service import _normalize_gpt_sovits_language
        try:
            _normalize_gpt_sovits_language(target_language)
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    health = await tts_service.test_tts_connection(
        provider=runtime.provider,
        api_key=runtime.api_key,
        base_url=runtime.base_url,
        model=runtime.model,
        voice_id=runtime.default_voice,
        region=runtime.region,
    )
    if not health.get("ok"):
        raise ValueError(health.get("message") or "TTS configuration is not usable.")
    return health


def _chunk_speaker(plan: list[PlanItem], chunk: DubbingChunk) -> str | None:
    speakers = {
        plan[idx].speaker_id
        for idx in chunk.item_indexes
        if plan[idx].speaker_id is not None
    }
    if len(speakers) > 1:
        raise RuntimeError(f"Dubbing chunk {chunk.id} crosses speaker boundary")
    return next(iter(speakers)) if speakers else chunk.speaker_id


def _voice_for_chunk(
    profile: LocalizationProfile,
    runtime: TTSRuntimeConfig,
    speaker_id: str | None,
    explicit_voice: str | None = None,
) -> str:
    if explicit_voice:
        return explicit_voice
    if speaker_id and speaker_id in profile.speaker_voices:
        return profile.speaker_voices[speaker_id]
    return runtime.default_voice


def _reference_for_chunk(
    profile: LocalizationProfile,
    speaker_id: str | None,
) -> str | None:
    if speaker_id and speaker_id in profile.speaker_reference_audio:
        return profile.speaker_reference_audio[speaker_id]
    return profile.tts_reference_audio


def _extract_reference_clip_sync(
    media_path: Path,
    output_path: Path,
    *,
    start_ms: int,
    end_ms: int,
) -> Path:
    """Extract a clean speech reference without touching video pixels."""
    if end_ms <= start_ms:
        raise ValueError("reference end must be after start")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start_ms / 1000.0:.3f}",
        "-t", f"{(end_ms - start_ms) / 1000.0:.3f}",
        "-i", str(media_path),
        "-vn", "-ac", "1", "-ar", "32000",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(
            f"Reference audio extraction failed: {(result.stderr or '')[-500:]}"
        )
    return output_path


async def _build_auto_voice_references(
    transcript: LocalizationTranscript,
    *,
    reference_media: Path,
    profile: LocalizationProfile,
    work_dir: Path,
) -> dict[str, dict]:
    """VideoLingo-style automatic source-reference extraction for cloning.

    Prefer a 2–10s source utterance per speaker. For global mode choose the
    single longest eligible utterance. Manual profile references always win.
    """
    mode = profile.voice_clone_reference_mode
    if mode == "manual":
        return {}

    eligible = [
        seg for seg in transcript.segments
        if 1500 <= (seg.source_end_ms - seg.source_start_ms) <= 12000
        and seg.source_text.strip()
    ]
    if not eligible:
        eligible = [
            seg for seg in transcript.segments
            if (seg.source_end_ms - seg.source_start_ms) >= 600
            and seg.source_text.strip()
        ]
    if not eligible:
        return {}

    chosen: dict[str, LocalizationSegment] = {}
    if mode == "global_auto":
        seg = max(eligible, key=lambda x: x.source_end_ms - x.source_start_ms)
        chosen["__default__"] = seg
    else:
        for seg in eligible:
            key = seg.speaker_id or "__default__"
            current = chosen.get(key)
            if (
                current is None
                or (seg.source_end_ms - seg.source_start_ms)
                > (current.source_end_ms - current.source_start_ms)
            ):
                chosen[key] = seg

    refs: dict[str, dict] = {}
    ref_dir = work_dir / "references"
    for key, seg in chosen.items():
        # Explicit profile references override auto extraction for that speaker.
        manual = (
            profile.speaker_reference_audio.get(key)
            if key != "__default__"
            else profile.tts_reference_audio
        )
        if manual:
            refs[key] = {
                "audio": manual,
                "text": profile.tts_reference_text or seg.source_text,
                "language": profile.tts_reference_language or transcript.language or "en",
                "source": "manual",
            }
            continue

        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)
        path = ref_dir / f"{safe}.wav"
        await asyncio.to_thread(
            _extract_reference_clip_sync,
            reference_media,
            path,
            start_ms=seg.source_start_ms,
            end_ms=seg.source_end_ms,
        )
        refs[key] = {
            "audio": str(path),
            "text": seg.source_text,
            "language": transcript.language or profile.tts_reference_language or "en",
            "source": "auto",
        }
    return refs


async def _audio_duration_ms(path: Path) -> int:
    from backend.services.video_utils import probe_duration
    seconds = await asyncio.to_thread(probe_duration, path, 0.0)
    return max(0, round(float(seconds) * 1000))


async def _generate_tts_chunk(
    text: str,
    *,
    runtime: TTSRuntimeConfig,
    voice: str,
    profile: LocalizationProfile,
    speaker_id: str | None,
    target_language: str,
    output_path: Path,
    reference_audio: str | None = None,
    reference_text: str | None = None,
    reference_language: str | None = None,
    attempts: int = 3,
) -> int:
    from backend.services.tts_service import generate_tts

    last: Exception | None = None
    for _ in range(max(1, attempts)):
        output_path.unlink(missing_ok=True)
        try:
            await generate_tts(
                text,
                provider=runtime.provider,
                voice_id=voice,
                api_key=runtime.api_key,
                output_path=output_path,
                model=runtime.model,
                speed=1.0,
                base_url=runtime.base_url,
                region=runtime.region,
                reference_audio=reference_audio or _reference_for_chunk(profile, speaker_id),
                reference_text=reference_text or profile.tts_reference_text,
                reference_language=reference_language or profile.tts_reference_language,
                target_language=target_language,
            )
            duration = await _audio_duration_ms(output_path)
            if duration > 0:
                return duration
            last = RuntimeError("TTS generated zero-duration audio")
        except Exception as exc:
            last = exc
    raise RuntimeError(f"TTS chunk failed after {attempts} attempts: {last}")



def _chunk_synthesis_identity(
    *,
    text: str,
    runtime: TTSRuntimeConfig,
    voice: str,
    target_language: str,
    speaker_id: str | None,
    reference_audio: str | None,
    reference_text: str | None,
    reference_language: str | None,
) -> str:
    """Fingerprint every input that can change a raw natural-rate TTS chunk.

    This is intentionally separate from timing/fitting inputs: a timing-only
    edit may safely reuse natural TTS, while any speech/voice/provider change
    must regenerate it.
    """
    provider = getattr(runtime.provider, "value", str(runtime.provider))
    payload = {
        "rules": _DUBBING_SYNTHESIS_RULES_VERSION,
        "provider": provider,
        "model": runtime.model,
        "base_url": runtime.base_url,
        "region": runtime.region,
        "voice": voice,
        "language": target_language,
        "speaker_id": speaker_id,
        "text": text,
        "reference_audio": reference_audio,
        "reference_text": reference_text,
        "reference_language": reference_language,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _chunk_identity_path(audio_path: Path) -> Path:
    return audio_path.with_name(audio_path.name + ".input.json")


def _chunk_identity_payload(audio_path: Path) -> dict | None:
    sidecar = _chunk_identity_path(audio_path)
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _chunk_identity_matches(audio_path: Path, identity: str) -> bool:
    payload = _chunk_identity_payload(audio_path)
    return bool(
        payload
        and payload.get("rules") == _DUBBING_SYNTHESIS_RULES_VERSION
        and payload.get("identity") == identity
    )


def _chunk_persisted_duration_ms(audio_path: Path, identity: str) -> int | None:
    payload = _chunk_identity_payload(audio_path)
    if not (
        payload
        and payload.get("rules") == _DUBBING_SYNTHESIS_RULES_VERSION
        and payload.get("identity") == identity
    ):
        return None
    try:
        duration_ms = int(payload.get("duration_ms"))
    except (TypeError, ValueError):
        return None
    return duration_ms if duration_ms > 0 else None


def _write_chunk_identity(
    audio_path: Path,
    identity: str,
    duration_ms: int | None = None,
) -> None:
    sidecar = _chunk_identity_path(audio_path)
    payload = {
        "rules": _DUBBING_SYNTHESIS_RULES_VERSION,
        "identity": identity,
    }
    if duration_ms is not None and duration_ms > 0:
        payload["duration_ms"] = int(duration_ms)
    tmp = sidecar.with_name(sidecar.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(sidecar)


async def _generate_tts_chunk_atomic(
    text: str,
    *,
    output_path: Path,
    identity: str,
    **kwargs,
) -> int:
    """Publish a new raw chunk only after TTS and duration probing succeed."""
    pending = output_path.with_name(output_path.stem + ".pending" + output_path.suffix)
    pending.unlink(missing_ok=True)
    try:
        duration = await _generate_tts_chunk(
            text,
            output_path=pending,
            **kwargs,
        )
        pending.replace(output_path)
        try:
            _write_chunk_identity(output_path, identity, duration)
        except Exception as exc:
            # Missing identity only disables future reuse; it must not invalidate
            # a TTS artifact that was already generated and measured correctly.
            logger.warning(
                "Could not persist synthesis identity for %s: %s",
                output_path.name,
                exc,
            )
        return duration
    finally:
        pending.unlink(missing_ok=True)


async def _adapt_chunk_items(
    ai,
    *,
    fallback_ai=None,
    route_state: _AIStageRouteState | None = None,
    deadline_at: float | None = None,
    progress_cb=None,
    plan: list[PlanItem],
    chunk: DubbingChunk,
    segment_by_id: dict[str, LocalizationSegment],
    target_language: str,
    target_ms: int,
    profile: LocalizationProfile,
    issues: list[dict] | None = None,
    state: dict | None = None,
    setup_error: Exception | None = None,
) -> bool:
    items = []
    for idx in chunk.item_indexes:
        item = plan[idx]
        seg = segment_by_id[item.segment_id]
        items.append({
            "id": item.segment_id,
            "source": seg.source_text,
            "current": seg.translated_text or item.spoken_text,
        })
    current_words = sum(len(item["current"].split()) for item in items)
    duration_ms = int((state or {}).get("measured_duration_ms") or sum(
        plan[idx].estimated_duration_ms for idx in chunk.item_indexes
    ))
    # Feed the actual natural-rate measurement back into the next attempt,
    # rather than asking the same vague question again. Leave modest headroom
    # for TTS pauses; this budget is guidance, never permission to drop facts.
    ratio = min(0.85, target_ms / max(duration_ms, 1))
    word_budget = max(len(items), math.floor(current_words * ratio * 0.95))
    prompt = f"""Rewrite these {target_language} dubbing lines so the WHOLE group can be spoken naturally in about {target_ms / 1000.0:.2f} seconds.
Duration feedback: current duration {duration_ms} ms; target duration {target_ms} ms.
Attempt {(state or {}).get('attempt', 1)}. Combined word budget: at most {word_budget} whitespace-separated words (current: {current_words}).
SEMANTIC GUARDRAILS (editable per profile in Settings):
{profile.rewrite_guardrails}

LOCKED PROTOCOL:
- If editable semantic guardrails or profile instructions conflict with this protocol, this protocol wins.
- Preserve every item ID and order.
- Return every input ID exactly once and no extra IDs.
- Return ONLY JSON: [{{"id":"...","text":"..."}}]
- Make wording concise and natural for speech only after the semantic guardrails above are satisfied.

ITEMS:
{json.dumps(items, ensure_ascii=False)}"""
    if profile.glossary:
        prompt += "\nGlossary: " + "; ".join(
            f"{k} => {v}" for k, v in profile.glossary.items()
        )[:1800]
    if profile.rewrite_instructions:
        prompt += (
            "\nPROFILE REWRITE INSTRUCTIONS (supplemental; semantic guardrails and locked protocol above remain active): "
            + profile.rewrite_instructions[:2400]
        )

    def record_unavailable(exc: Exception) -> bool:
        logger.warning(
            "Dubbing rewrite unavailable for chunk %s: %s",
            chunk.id,
            exc,
        )
        if state is not None:
            state["unavailable"] = True
        if issues is not None:
            issues.append({
                "chunk_id": chunk.id,
                "reason": "rewrite_ai_unavailable",
                "error_type": type(exc).__name__,
                "message": str(exc)[:300],
                "segment_ids": [plan[i].segment_id for i in chunk.item_indexes],
            })
        return False

    def record_invalid(detail: str) -> bool:
        if issues is not None:
            issues.append({
                "chunk_id": chunk.id,
                "reason": "rewrite_invalid_response",
                "detail": detail,
                "segment_ids": [plan[i].segment_id for i in chunk.item_indexes],
            })
        return False

    async def retry_on_invalid(detail: str) -> bool | None:
        if route_state is None or route_state.sticky_index + 1 >= len(route_state.clients):
            return None
        current = route_state.current
        route_state.failure_chain.append({
            "connection_id": getattr(current, "connection_name", None),
            "provider": getattr(getattr(current, "provider", None), "value", str(getattr(current, "provider", ""))),
            "model": getattr(current, "model", None),
            "attempt": 1,
            "error_type": "InvalidStructuredOutput",
            "error": f"rewrite {detail}",
        })
        route_state.sticky_index += 1
        next_client = route_state.current
        await _emit_ai_progress(progress_cb, {
            "event": "switch_fallback",
            "stage": "rewrite",
            "reason": detail,
            "provider": getattr(next_client, "connection_name", None) or getattr(getattr(next_client, "provider", None), "value", str(getattr(next_client, "provider", ""))),
            "model": getattr(next_client, "model", None),
        })
        return await _adapt_chunk_items(
            ai,
            fallback_ai=fallback_ai,
            route_state=route_state,
            deadline_at=deadline_at,
            progress_cb=progress_cb,
            plan=plan,
            chunk=chunk,
            segment_by_id=segment_by_id,
            target_language=target_language,
            target_ms=target_ms,
            profile=profile,
            issues=issues,
            state=state,
            setup_error=setup_error,
        )

    if ai is None:
        return record_unavailable(
            setup_error or RuntimeError("Duration rewrite AI client is unavailable")
        )

    try:
        # Translation already uses the canonical bounded transient retry helper.
        # Duration rewrite is equally exposed to provider 429/5xx/timeout bursts;
        # disabling rewrite after the first transient response can turn a
        # recoverable provider hiccup into a guaranteed timing overflow. Keep
        # retries owned here instead of nesting the SDK's implicit retry loop.
        if hasattr(ai, "max_retries"):
            ai.max_retries = 0
        raw = await _call_ai_chat_resilient(
            ai,
            fallback_ai,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            max_attempts=profile.rewrite_retry_attempts,
            attempt_timeout_s=profile.rewrite_attempt_timeout_s,
            total_timeout_s=profile.rewrite_total_timeout_s,
            progress_cb=progress_cb,
            stage="rewrite",
            route_state=route_state,
            deadline_at=deadline_at,
        )
    except Exception as exc:
        return record_unavailable(exc)

    try:
        payload = json.loads(_strip_json_fence(raw))
    except Exception:
        if route_state is not None and route_state.sticky_index + 1 < len(route_state.clients):
            current = route_state.current
            route_state.failure_chain.append({
                "connection_id": getattr(current, "connection_name", None),
                "provider": getattr(getattr(current, "provider", None), "value", str(getattr(current, "provider", ""))),
                "model": getattr(current, "model", None),
                "attempt": 1,
                "error_type": "InvalidStructuredOutput",
                "error": "rewrite json_parse",
            })
            route_state.sticky_index += 1
            return await _adapt_chunk_items(
                ai,
                fallback_ai=fallback_ai,
                route_state=route_state,
                deadline_at=deadline_at,
                progress_cb=progress_cb,
                plan=plan,
                chunk=chunk,
                segment_by_id=segment_by_id,
                target_language=target_language,
                target_ms=target_ms,
                profile=profile,
                issues=issues,
                state=state,
                setup_error=setup_error,
            )
        return record_invalid("json_parse")
    if not isinstance(payload, list) or len(payload) != len(items):
        if route_state is not None and route_state.sticky_index + 1 < len(route_state.clients):
            current = route_state.current
            route_state.failure_chain.append({
                "connection_id": getattr(current, "connection_name", None),
                "provider": getattr(getattr(current, "provider", None), "value", str(getattr(current, "provider", ""))),
                "model": getattr(current, "model", None),
                "attempt": 1,
                "error_type": "InvalidStructuredOutput",
                "error": "rewrite shape_or_length",
            })
            route_state.sticky_index += 1
            return await _adapt_chunk_items(
                ai,
                fallback_ai=fallback_ai,
                route_state=route_state,
                deadline_at=deadline_at,
                progress_cb=progress_cb,
                plan=plan,
                chunk=chunk,
                segment_by_id=segment_by_id,
                target_language=target_language,
                target_ms=target_ms,
                profile=profile,
                issues=issues,
                state=state,
                setup_error=setup_error,
            )
        return record_invalid("shape_or_length")
    mapped: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            retried = await retry_on_invalid("item_not_object")
            return retried if retried is not None else record_invalid("item_not_object")
        seg_id = str(item.get("id") or "")
        text = str(item.get("text") or "").strip()
        if not seg_id or not text or seg_id in mapped:
            retried = await retry_on_invalid("invalid_or_duplicate_item")
            return retried if retried is not None else record_invalid("invalid_or_duplicate_item")
        mapped[seg_id] = text
    expected = [x["id"] for x in items]
    if set(mapped) != set(expected):
        retried = await retry_on_invalid("id_mismatch")
        return retried if retried is not None else record_invalid("id_mismatch")

    current_text = chunk_text(plan, chunk)
    candidate_text = " ".join(mapped[item["id"]] for item in items)
    # A paraphrase that grows cannot solve a duration overflow. Reject it
    # atomically so neither the plan nor the caption transcript drifts.
    if len(candidate_text) >= len(current_text) or (
        current_words > 1
        and len(candidate_text.split()) >= current_words
    ):
        retried = await retry_on_invalid("rewrite_not_shorter")
        if retried is not None:
            return retried
        if issues is not None:
            issues.append({
                "chunk_id": chunk.id,
                "reason": "rewrite_not_shorter",
                "segment_ids": expected,
            })
        return False

    changed = False
    for idx in chunk.item_indexes:
        item = plan[idx]
        new_text = mapped[item.segment_id]
        if new_text != item.spoken_text:
            changed = True
        item.spoken_text = new_text
        item.estimated_duration_ms = estimate_spoken_ms(new_text, target_language)
        item.rewrite_attempts += 1
        segment_by_id[item.segment_id].translated_text = new_text
    return changed


def _fit_chunk_audio_sync(source: Path, destination: Path, speed_factor: float) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(source),
        "-filter:a", atempo_filter(speed_factor),
        "-ar", "48000", "-ac", "2",
        "-c:a", "pcm_s16le",
        str(destination),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0 or not destination.exists():
        raise RuntimeError(f"Chunk audio fit failed: {(result.stderr or '')[-500:]}")
    return destination


def _compose_chunk_track_sync(
    timed_chunks: list[tuple[int, Path]],
    *,
    total_duration_ms: int,
    output_path: Path,
) -> Path:
    if not timed_chunks:
        raise RuntimeError("No fitted dubbing chunks to compose")
    total_s = max(total_duration_ms / 1000.0, 0.1)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-t", f"{total_s:.3f}",
        "-i", "anullsrc=r=48000:cl=stereo",
    ]
    for _, path in timed_chunks:
        cmd += ["-i", str(path)]
    filters = ["[0:a]volume=0[base]"]
    labels = ["[base]"]
    for input_idx, (start_ms, _) in enumerate(timed_chunks, start=1):
        delay = max(0, int(start_ms))
        label = f"d{input_idx}"
        filters.append(f"[{input_idx}:a]adelay={delay}|{delay}[{label}]")
        labels.append(f"[{label}]")
    filters.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"
          f"alimiter=limit=0.95,atrim=0:{total_s:.3f}[mix]"
    )
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[mix]", "-c:a", "pcm_s16le", str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"Dub-track composition failed: {(result.stderr or '')[-500:]}")
    return output_path


def _scale_chunks_for_video_slowdown(
    chunks: list[DubbingChunk],
    factor: float,
) -> None:
    for chunk in chunks:
        chunk.start_ms = round(chunk.start_ms * factor)
        chunk.end_ms = round(chunk.end_ms * factor)


async def synthesize_timed_dub(
    transcript: LocalizationTranscript,
    *,
    source_media: Path,
    target_language: str,
    profile: LocalizationProfile,
    user_settings,
    work_dir: Path,
    fail_on_overflow: bool = True,
    regenerate_chunk_ids: set[int] | None = None,
    reuse_existing_chunks: bool = False,
    reference_media: Path | None = None,
    progress_cb=None,
) -> TimedDubResult:
    """C7 donor-derived chunk planner with measured TTS and bounded retiming."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = transcript.model_copy(deep=True)
    rewrite_ai_error: Exception | None = None
    route_state: _AIStageRouteState | None = None
    try:
        rewrite_clients = _profile_ai_route_clients_compat(
            user_settings, profile, "rewrite"
        )
        route_state = _AIStageRouteState(rewrite_clients)
        ai = rewrite_clients[0]
        fallback_ai = rewrite_clients[1] if len(rewrite_clients) > 1 else None
    except Exception as exc:
        ai = None
        fallback_ai = None
        rewrite_ai_error = exc
        logger.warning("Duration rewrite AI routes unavailable: %s", exc)

    rewrite_deadline_at = (
        asyncio.get_running_loop().time() + float(profile.rewrite_total_timeout_s)
        if ai is not None else None
    )
    runtime = _tts_config(user_settings, profile, target_language)

    valid_segments = [
        seg for seg in out.segments
        if seg.translation_status == "success" and (seg.translated_text or "").strip()
    ]
    if not valid_segments:
        raise RuntimeError("No translated speech is available for dubbing.")

    config = DubbingConfig(
        min_cue_duration_ms=profile.dub_min_cue_duration_ms,
        max_chunk_size=profile.dub_max_chunk_size,
        gap_tolerance_ms=profile.dub_gap_tolerance_ms,
        speed_accept=profile.dub_speed_accept,
        speed_max=profile.dub_speed_max,
        rewrite_max_attempts=profile.dub_rewrite_attempts,
    )
    cues = []
    for i, seg in enumerate(valid_segments):
        override = seg.overrides or {}
        start_ms = int(override.get("target_start_ms", seg.source_start_ms))
        end_ms = int(override.get("target_end_ms", seg.source_end_ms))
        if end_ms <= start_ms:
            raise ValueError(f"Invalid expert timing override for {seg.id}")
        cues.append(DubbingCue(
            index=i + 1,
            segment_id=seg.id,
            start_ms=start_ms,
            end_ms=end_ms,
            text=seg.translated_text or "",
            speaker_id=seg.speaker_id,
            voice_id=(str(override.get("voice_id")).strip() or None)
                if override.get("voice_id") is not None else None,
        ))
    plan, chunks = build_plan(cues, config, language=target_language)
    segment_by_id = {seg.id: seg for seg in out.segments}

    # Probe once up front so the planner can use real trailing source silence
    # for an ordinary final chunk. An explicit expert timing override is a hard
    # user boundary, so never extend that final chunk beyond its requested end.
    # The same measured duration is reused later when composing the dub track.
    from backend.services.video_utils import probe_duration
    source_duration_s = await asyncio.to_thread(probe_duration, source_media, 0.0)
    source_duration_ms = (
        round(source_duration_s * 1000)
        if source_duration_s > 0
        else None
    )
    final_chunk_has_timing_override = bool(chunks) and any(
        any(
            key in (segment_by_id[plan[idx].segment_id].overrides or {})
            for key in ("target_start_ms", "target_end_ms")
        )
        for idx in chunks[-1].item_indexes
    )
    chunks = borrow_following_silence(
        chunks,
        max_borrow_ms=min(profile.dub_gap_tolerance_ms, 1500),
        timeline_end_ms=(
            None if final_chunk_has_timing_override else source_duration_ms
        ),
    )

    auto_references: dict[str, dict] = {}
    provider_value = getattr(runtime.provider, "value", str(runtime.provider))
    if provider_value == "gpt_sovits":
        auto_references = await _build_auto_voice_references(
            out,
            reference_media=Path(reference_media or source_media),
            profile=profile,
            work_dir=work_dir,
        )

    raw_paths: dict[int, Path] = {}
    block_paths: list[Path] = []
    regenerate_chunk_ids = set(regenerate_chunk_ids or set())
    targeted_regeneration = bool(regenerate_chunk_ids)
    issues: list[dict] = []
    rewrite_count = 0
    rewrite_disabled = ai is None
    rewrite_unavailable_recorded = False

    def record_rewrite_setup_unavailable(chunk: DubbingChunk) -> None:
        nonlocal rewrite_unavailable_recorded
        if rewrite_unavailable_recorded:
            return
        exc = rewrite_ai_error or RuntimeError(
            "Duration rewrite AI client is unavailable"
        )
        issues.append({
            "chunk_id": chunk.id,
            "reason": "rewrite_ai_unavailable",
            "error_type": type(exc).__name__,
            "message": str(exc)[:300],
            "segment_ids": [plan[i].segment_id for i in chunk.item_indexes],
        })
        rewrite_unavailable_recorded = True

    for chunk in chunks:
        speaker_id = _chunk_speaker(plan, chunk)
        explicit_voice = chunk.voice_id
        voice = _voice_for_chunk(profile, runtime, speaker_id, explicit_voice)
        path = work_dir / f"chunk_{chunk.id:04d}.wav"
        raw_paths[chunk.id] = path
        block_paths.append(path)

        ref_key = speaker_id or "__default__"
        ref = auto_references.get(ref_key) or auto_references.get("__default__") or {}
        initial_text = chunk_text(plan, chunk)
        initial_identity = _chunk_synthesis_identity(
            text=initial_text,
            runtime=runtime,
            voice=voice,
            target_language=target_language,
            speaker_id=speaker_id,
            reference_audio=ref.get("audio"),
            reference_text=ref.get("text"),
            reference_language=ref.get("language"),
        )

        # Targeted/resumed reuse is permitted only when the raw artifact's
        # synthesis identity proves it still matches current speech inputs.
        # This mirrors SmartSub's input-key invariant instead of trusting a
        # filename left over from a previous attempt.
        reuse_raw = (
            reuse_existing_chunks
            and chunk.id not in regenerate_chunk_ids
            and path.exists()
            and path.stat().st_size > 0
            and _chunk_identity_matches(path, initial_identity)
        )
        is_target_chunk = not targeted_regeneration or chunk.id in regenerate_chunk_ids
        if targeted_regeneration and not is_target_chunk and not reuse_raw:
            raise RuntimeError(
                f"Cannot safely targeted-regenerate chunk(s) {sorted(regenerate_chunk_ids)} "
                f"because non-target chunk {chunk.id} has no valid reusable raw artifact."
            )

        # OpenCreator-style estimator: try a bounded rewrite before spending on
        # a clearly impossible TTS request. In targeted mode, non-target chunks
        # are immutable even when their measured duration later exceeds speed_max.
        estimated = sum(plan[i].estimated_duration_ms for i in chunk.item_indexes)
        attempts = 0
        rewrite_state: dict = {}
        can_optional_rewrite = is_target_chunk and not rewrite_disabled
        if (
            not reuse_raw
            and can_optional_rewrite
            and estimated > chunk.available_ms * config.speed_max
            and attempts < config.rewrite_max_attempts
        ):
            changed = await _adapt_chunk_items(
                ai, fallback_ai=fallback_ai, route_state=route_state,
                deadline_at=rewrite_deadline_at, progress_cb=progress_cb,
                plan=plan, chunk=chunk, segment_by_id=segment_by_id,
                target_language=target_language, target_ms=chunk.available_ms,
                profile=profile, issues=issues, state=rewrite_state,
                setup_error=rewrite_ai_error,
            )
            attempts += 1
            if rewrite_state.get("unavailable"):
                rewrite_disabled = True
                rewrite_unavailable_recorded = True
                attempts = config.rewrite_max_attempts
            if changed:
                rewrite_count += 1
        elif (
            not reuse_raw
            and is_target_chunk
            and rewrite_disabled
            and estimated > chunk.available_ms * config.speed_max
            and config.rewrite_max_attempts > 0
        ):
            record_rewrite_setup_unavailable(chunk)

        speech_text = chunk_text(plan, chunk)
        synthesis_identity = _chunk_synthesis_identity(
            text=speech_text,
            runtime=runtime,
            voice=voice,
            target_language=target_language,
            speaker_id=speaker_id,
            reference_audio=ref.get("audio"),
            reference_text=ref.get("text"),
            reference_language=ref.get("language"),
        )
        # If a rewrite occurred, reuse_raw was already false. Recomputing the
        # identity here makes the newly generated artifact authoritative for the
        # exact post-rewrite speech text.
        reuse_raw = reuse_raw and synthesis_identity == initial_identity

        duration_ms = 0
        if reuse_raw:
            duration_ms = _chunk_persisted_duration_ms(path, initial_identity) or 0
            if duration_ms <= 0:
                try:
                    duration_ms = await _audio_duration_ms(path)
                except Exception as exc:
                    logger.warning(
                        "Could not reuse dubbing chunk %s; regenerating: %s",
                        chunk.id,
                        exc,
                    )
                    reuse_raw = False
                if duration_ms > 0:
                    try:
                        _write_chunk_identity(path, initial_identity, duration_ms)
                    except Exception as exc:
                        logger.warning(
                            "Could not heal synthesis duration metadata for %s: %s",
                            path.name,
                            exc,
                        )
            if duration_ms <= 0:
                reuse_raw = False

        if targeted_regeneration and not is_target_chunk and not reuse_raw:
            raise RuntimeError(
                f"Cannot safely targeted-regenerate chunk(s) {sorted(regenerate_chunk_ids)} "
                f"because non-target chunk {chunk.id} could not be reused/measured."
            )

        if not reuse_raw:
            duration_ms = await _generate_tts_chunk_atomic(
                speech_text,
                runtime=runtime,
                voice=voice,
                profile=profile,
                speaker_id=speaker_id,
                target_language=target_language,
                output_path=path,
                identity=synthesis_identity,
                reference_audio=ref.get("audio"),
                reference_text=ref.get("text"),
                reference_language=ref.get("language"),
            )

        while (
            is_target_chunk
            and not rewrite_disabled
            and required_speed(duration_ms, chunk.available_ms) > config.speed_max
            and attempts < config.rewrite_max_attempts
        ):
            rewrite_state.update(measured_duration_ms=duration_ms, attempt=attempts + 1)
            changed = await _adapt_chunk_items(
                ai, fallback_ai=fallback_ai, route_state=route_state,
                deadline_at=rewrite_deadline_at, progress_cb=progress_cb,
                plan=plan, chunk=chunk, segment_by_id=segment_by_id,
                target_language=target_language, target_ms=chunk.available_ms,
                profile=profile, issues=issues, state=rewrite_state,
                setup_error=rewrite_ai_error,
            )
            attempts += 1
            if rewrite_state.get("unavailable"):
                rewrite_disabled = True
                rewrite_unavailable_recorded = True
                attempts = config.rewrite_max_attempts
            if not changed:
                continue
            rewrite_count += 1
            speech_text = chunk_text(plan, chunk)
            synthesis_identity = _chunk_synthesis_identity(
                text=speech_text,
                runtime=runtime,
                voice=voice,
                target_language=target_language,
                speaker_id=speaker_id,
                reference_audio=ref.get("audio"),
                reference_text=ref.get("text"),
                reference_language=ref.get("language"),
            )
            duration_ms = await _generate_tts_chunk_atomic(
                speech_text,
                runtime=runtime,
                voice=voice,
                profile=profile,
                speaker_id=speaker_id,
                target_language=target_language,
                output_path=path,
                identity=synthesis_identity,
                reference_audio=ref.get("audio"),
                reference_text=ref.get("text"),
                reference_language=ref.get("language"),
            )
        if (
            is_target_chunk
            and rewrite_disabled
            and not rewrite_unavailable_recorded
            and required_speed(duration_ms, chunk.available_ms) > config.speed_max
            and config.rewrite_max_attempts > 0
        ):
            record_rewrite_setup_unavailable(chunk)
        chunk.actual_duration_ms = duration_ms

    # Last resort from the pyVideoTrans family of strategies: if the donor
    # planner still requires over-max speech, enlarge ALL source windows by one
    # bounded visual slowdown. The final renderer applies this in the same
    # visual encode as reframe/captions; TTS itself remains natural.
    raw_required = [
        required_speed(c.actual_duration_ms, c.available_ms)
        for c in chunks
    ]
    max_raw_required_speed = max([1.0, *raw_required])
    needed_slowdown = max(
        [1.0] + [speed / config.speed_max for speed in raw_required if speed > config.speed_max]
    )
    video_slowdown = 1.0
    if needed_slowdown > 1.0 and profile.video_retime_mode == "fallback":
        video_slowdown = min(needed_slowdown, profile.video_retime_max_slowdown)
        # Independent rounding of the two endpoints can shrink a window by
        # almost 1ms. If the ideal factor would still overflow on that integer
        # timeline, reserve one rounding tick before applying the SAME global
        # factor once. Never relax the speed or configured retime ceiling.
        if any(
            required_speed(
                c.actual_duration_ms,
                round(c.end_ms * video_slowdown) - round(c.start_ms * video_slowdown),
            ) > config.speed_max
            for c in chunks
        ):
            rounding_safe_slowdown = max(
                (math.ceil(c.actual_duration_ms / config.speed_max) + 1) / c.available_ms
                for c in chunks
            )
            video_slowdown = min(
                max(video_slowdown, rounding_safe_slowdown),
                profile.video_retime_max_slowdown,
            )
        _scale_chunks_for_video_slowdown(chunks, video_slowdown)

    unresolved: list[dict] = []
    for chunk in chunks:
        speed = required_speed(chunk.actual_duration_ms, chunk.available_ms)
        if speed > config.speed_max + 1e-6:
            unresolved.append({
                "chunk_id": chunk.id,
                "reason": "timing_overflow",
                "required_speed": round(speed, 4),
                "speed_max": config.speed_max,
                "available_ms": chunk.available_ms,
                "tts_duration_ms": chunk.actual_duration_ms,
                "segment_ids": [plan[i].segment_id for i in chunk.item_indexes],
            })
    if unresolved and fail_on_overflow:
        raise LocalizationTimingError(
            unresolved,
            rewrite_count=rewrite_count,
            all_issues=[*issues, *unresolved],
        )
    issues.extend(unresolved)

    fitted_plan, fitted_chunks, report = fit_timeline(plan, chunks, config)
    report.rewrite_count = rewrite_count

    timed_chunks: list[tuple[int, Path]] = []
    fitted_dir = work_dir / "fitted"
    for chunk in fitted_chunks:
        if abs(chunk.speed_factor - 1.0) <= 0.001:
            timed_path = raw_paths[chunk.id]
        else:
            fitted = fitted_dir / f"chunk_{chunk.id:04d}.wav"
            await asyncio.to_thread(
                _fit_chunk_audio_sync,
                raw_paths[chunk.id],
                fitted,
                chunk.speed_factor,
            )
            block_paths.append(fitted)
            timed_path = fitted
        timed_chunks.append((chunk.start_ms, timed_path))
        if chunk.speed_factor > config.speed_accept:
            issues.append({
                "chunk_id": chunk.id,
                "reason": "speed_warning",
                "required_speed": round(chunk.speed_factor, 4),
                "speed_accept": config.speed_accept,
                "segment_ids": [fitted_plan[i].segment_id for i in chunk.item_indexes],
            })

    item_by_segment = {item.segment_id: item for item in fitted_plan}
    for seg in out.segments:
        item = item_by_segment.get(seg.id)
        if item is None:
            if seg.translation_status != "success":
                seg.qc_status = "review"
            continue
        seg.target_start_ms = item.target_start_ms
        seg.target_end_ms = item.target_end_ms
        seg.tts_duration_ms = item.target_end_ms - item.target_start_ms
        seg.timing_status = "adjusted" if (
            item.speed_factor > 1.001 or video_slowdown > 1.001
        ) else "fit"
        seg.qc_status = "review" if item.speed_factor > config.speed_accept else "pass"

    total_duration_ms = max(
        round(source_duration_s * 1000 * video_slowdown),
        max((seg.target_end_ms or 0) for seg in out.segments),
    )
    dub_track = work_dir / "dub_track.wav"
    await asyncio.to_thread(
        _compose_chunk_track_sync,
        timed_chunks,
        total_duration_ms=total_duration_ms,
        output_path=dub_track,
    )
    max_fitted_speed = max([1.0, *[c.speed_factor for c in fitted_chunks]])
    out.metadata["dubbing"] = {
        "chunk_count": len(fitted_chunks),
        "max_raw_required_speed": max_raw_required_speed,
        "max_fitted_speed": max_fitted_speed,
        "rewrite_count": rewrite_count,
        "video_slowdown": video_slowdown,
        "issues": issues,
        "rewrite_ai": {
            "selected_connection_id": (
                getattr(route_state.current, "connection_name", None)
                if route_state is not None else None
            ),
            "failure_chain": (
                list(route_state.failure_chain)
                if route_state is not None else []
            ),
            "sticky_route_index": (
                route_state.sticky_index if route_state is not None else None
            ),
        },
    }
    return TimedDubResult(
        transcript=out,
        dub_track=dub_track,
        block_paths=block_paths,
        issues=issues,
        chunk_count=len(fitted_chunks),
        max_speed_factor=max_raw_required_speed,
        max_fitted_speed_factor=max_fitted_speed,
        rewrite_count=rewrite_count,
        video_slowdown=video_slowdown,
        plan=fitted_plan,
        chunks=fitted_chunks,
    )


def _mix_audio_sync(
    source_video: Path,
    dub_track: Path,
    output_path: Path,
    *,
    audio_mode: str,
    background_file: Path | None,
    preserve_background: bool,
    background_gain_db: float,
    dub_gain_db: float,
    video_slowdown: float,
) -> Path:
    """Create the final mixed AUDIO track; no picture is encoded here."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)

    if audio_mode == "hq_clean":
        if background_file is None or not background_file.exists():
            raise RuntimeError("HQ clean dub requires a separated background track.")
        cmd = ["ffmpeg", "-y", "-i", str(dub_track), "-i", str(background_file)]
        bg_chain = f"[1:a]volume={background_gain_db}dB"
        if video_slowdown > 1.001:
            bg_chain += f",atempo={1.0 / video_slowdown:.6f}"
        bg_chain += "[bg]"
        fc = (
            f"[0:a]volume={dub_gain_db}dB[dub];"
            + bg_chain + ";"
            "[dub][bg]amix=inputs=2:duration=longest:normalize=0,"
            "alimiter=limit=0.95[mix]"
        )
    else:
        from backend.services.ffmpeg_service import has_audio_stream_sync
        if not preserve_background or not has_audio_stream_sync(source_video):
            cmd = ["ffmpeg", "-y", "-i", str(dub_track)]
            fc = f"[0:a]volume={dub_gain_db}dB,alimiter=limit=0.95[mix]"
        else:
            cmd = ["ffmpeg", "-y", "-i", str(dub_track), "-i", str(source_video)]
            bg_chain = f"[1:a]volume={background_gain_db}dB"
            if video_slowdown > 1.001:
                bg_chain += f",atempo={1.0 / video_slowdown:.6f}"
            bg_chain += "[bg]"
            fc = (
                f"[0:a]volume={dub_gain_db}dB[dub];"
                + bg_chain + ";"
                "[bg][dub]sidechaincompress=threshold=0.025:ratio=12:"
                "attack=15:release=220[ducked];"
                "[ducked][dub]amix=inputs=2:duration=longest:normalize=0,"
                "alimiter=limit=0.95[mix]"
            )

    cmd += [
        "-filter_complex", fc,
        "-map", "[mix]",
        "-ar", "48000", "-ac", "2",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"Dub/background audio mix failed: {(result.stderr or '')[-500:]}")
    return output_path


async def mix_dub_audio(
    source_video: Path,
    dub_track: Path,
    output_path: Path,
    profile: LocalizationProfile,
    *,
    background_file: Path | None = None,
    video_slowdown: float = 1.0,
) -> Path:
    return await asyncio.to_thread(
        _mix_audio_sync,
        source_video,
        dub_track,
        output_path,
        audio_mode=profile.audio_mode,
        background_file=background_file,
        preserve_background=profile.preserve_background,
        background_gain_db=profile.background_gain_db,
        dub_gain_db=profile.dub_gain_db,
        video_slowdown=video_slowdown,
    )


def persist_dubbing_rewrites(
    translation: LocalizationTranscript,
    dubbed: LocalizationTranscript,
) -> LocalizationTranscript:
    """Persist only spoken-text decisions from C7 back into C6 state.

    C7 may shorten translated lines so real TTS fits the timeline. Target
    timing remains a dubbing-stage concern, but the final spoken wording must
    survive targeted chunk regeneration/resume or reused raw audio can diverge
    from captions/text.
    """
    out = translation.model_copy(deep=True)
    dubbed_by_id = {seg.id: seg for seg in dubbed.segments}
    for seg in out.segments:
        resolved = dubbed_by_id.get(seg.id)
        if resolved is None:
            continue
        if (resolved.translated_text or "").strip():
            seg.translated_text = resolved.translated_text
            seg.translation_status = resolved.translation_status
    return out


def caption_segments_from_localization(transcript: LocalizationTranscript) -> list[dict]:
    out: list[dict] = []
    for seg in transcript.segments:
        text = (seg.translated_text or "").strip()
        if not text:
            continue
        start_ms = seg.target_start_ms
        end_ms = seg.target_end_ms
        if start_ms is None or end_ms is None:
            start_ms, end_ms = seg.source_start_ms, seg.source_end_ms
        if end_ms <= start_ms:
            continue
        out.append({
            "start": start_ms / 1000.0,
            "end": end_ms / 1000.0,
            "text": text,
            "speaker_id": seg.speaker_id,
        })
    return out
