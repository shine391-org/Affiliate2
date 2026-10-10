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
import re
import os
import random
import subprocess
import uuid
import time as _time
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
    allocate_silence,
    build_plan,
    chunk_text,
    clean_text_for_speech,
    estimate_spoken_ms,
    fit_timeline,
    required_speed,
)

from backend.services.dubbing_job_state import (
    DubbingJobState, reference_identity, resolve_speaker_voices, tts_binding, write_json_atomic,
    speaker_gender_warnings,
)
from backend.services.dubbing_cadence import read_cue_spans, place_cue_spans

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp for audit records."""
    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())

_TRANSLATION_BATCH_SIZE = 12
_C6_TRANSLATION_PROMPT_VERSION = 3  # C6 checkpoint invalidation for changed guidance.
_CONTEXT_CHARS = 10_000
_DUBBING_SYNTHESIS_RULES_VERSION = 1
_EDGE_TTS_BOUNDARY_RULE = "edge-boundary-trim-v3-neutral-pitch"
_TTS_REQUEST_TIMEOUT_S = 120.0
_TTS_CHUNK_DEADLINE_S = 300.0
_POSTFIT_ROUNDING_TOLERANCE_MS = 30
_C7_REWRITE_POLICY_VERSION = 4


# Abandoned per-attempt TTS scratch files. A provider that streams from a
# worker thread cannot be stopped by cancelling the await: the thread finishes
# and RE-CREATES its file after the failure was already reported. The failing
# call never sleeps waiting for that thread; instead the path is registered
# here and swept by a detached task, so cleanup is bounded and does not block
# or mask the Timeout/JobCancelledError on its way out.
_ABANDONED_TTS_ATTEMPTS: set[Path] = set()
_TTS_ORPHAN_SWEEP_DEADLINE_S = 30.0
# A path is only retired after it has stayed absent for this long. Two absent
# polls are not enough: a writer thread that is still blocked on the network
# looks exactly like an absent file until it finally lands its bytes.
_TTS_ORPHAN_SETTLE_S = 1.0
_TTS_ORPHAN_SWEEPER: asyncio.Task | None = None


def _register_abandoned_tts_attempt(path: Path) -> None:
    """Drop a scratch file now, and keep sweeping it in case it comes back.

    The immediate unlink satisfies the common case (no writer thread outlived
    this attempt, so the file is simply garbage); the sweeper covers the case
    where a blocked writer thread re-creates it moments later. Registering is
    therefore always safe and never blocks the caller.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    _ABANDONED_TTS_ATTEMPTS.add(path)
    _ensure_tts_orphan_sweeper()


def _ensure_tts_orphan_sweeper() -> None:
    global _TTS_ORPHAN_SWEEPER
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    existing = _TTS_ORPHAN_SWEEPER
    if existing is not None and not existing.done() and existing.get_loop() is loop:
        return
    _TTS_ORPHAN_SWEEPER = loop.create_task(_sweep_abandoned_tts_attempts())


async def _sweep_abandoned_tts_attempts() -> None:
    """Re-delete registered scratch files until they stop coming back.

    A path is retired after it is observed absent twice in a row, which is the
    only completion signal an unstoppable writer thread can give us. The whole
    sweep is additionally bounded so a permanently undeletable file cannot pin
    the task forever.
    """
    loop = asyncio.get_running_loop()
    now = loop.time()
    deadline = now + _TTS_ORPHAN_SWEEP_DEADLINE_S
    # A path is retired only once it has been continuously absent since we last
    # saw it, or since registration when it was already gone.
    stable_since: dict[Path, float] = dict.fromkeys(_ABANDONED_TTS_ATTEMPTS, now)
    while _ABANDONED_TTS_ATTEMPTS and loop.time() < deadline:
        for path in list(_ABANDONED_TTS_ATTEMPTS):
            since = stable_since.setdefault(path, loop.time())
            try:
                path.unlink(missing_ok=True)
            except OSError:
                stable_since[path] = loop.time()
                continue
            if loop.time() - since >= _TTS_ORPHAN_SETTLE_S:
                # Retired: a later registration re-adds the path and re-arms it.
                _ABANDONED_TTS_ATTEMPTS.discard(path)
                stable_since.pop(path, None)
        if not _ABANDONED_TTS_ATTEMPTS:
            break
        await asyncio.sleep(0.05)


async def _sweep_late_artifacts() -> None:
    """Force the abandoned-attempt sweep to finish and clear what it tracked.

    Test/diagnostic hook: it waits only for a sweeper that belongs to THIS loop,
    so it never touches a task left behind by another event loop.
    """
    loop = asyncio.get_running_loop()
    sweeper = _TTS_ORPHAN_SWEEPER
    if sweeper is not None and not sweeper.done() \
            and sweeper.get_loop() is loop:
        try:
            await asyncio.wait_for(asyncio.shield(sweeper), _TTS_ORPHAN_SWEEP_DEADLINE_S)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
    for path in list(_ABANDONED_TTS_ATTEMPTS):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        _ABANDONED_TTS_ATTEMPTS.discard(path)


async def _cancel_checkpoint(cancel_check, context: str) -> None:
    if cancel_check is None:
        return
    cancelled = cancel_check()
    if asyncio.iscoroutine(cancelled):
        cancelled = await cancelled
    if cancelled:
        from backend.core.exceptions import JobCancelledError
        raise JobCancelledError(f"Localization cancelled during {context}")


async def _await_provider_operation(operation, *, timeout_s: float, cancel_check=None):
    """Bound cancellable network I/O; this does NOT kill remote/native compute."""
    task = asyncio.ensure_future(operation)
    try:
        async with asyncio.timeout(timeout_s):
            while not task.done():
                await _cancel_checkpoint(cancel_check, "provider request")
                await asyncio.wait({task}, timeout=0.25)
            await _cancel_checkpoint(cancel_check, "provider result publication")
            return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

# Edge TTS often wraps very short utterances in relatively large digital
# leading/trailing silence. Measuring that container padding as spoken duration
# makes the timeline planner accelerate speech that is not actually long.
# Trim only clearly silent boundaries, retain a small safety pad, and leave all
# other providers untouched.
_EDGE_TTS_TRIM_THRESHOLD_DB = -60
_EDGE_TTS_TRIM_MIN_SILENCE_S = 0.12
_EDGE_TTS_TRIM_START_PAD_S = 0.06
_EDGE_TTS_TRIM_END_PAD_S = 0.08
_EDGE_TTS_TRIM_MIN_SAVING_MS = 20

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
        rewrite_ai: dict | None = None,
    ):
        self.issues = issues
        self.rewrite_count = int(rewrite_count or 0)
        self.all_issues = list(all_issues or issues)
        self.rewrite_ai = dict(rewrite_ai or {})
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

    Connection IDs and selected models are snapshotted. Credentials and the
    endpoint owned by a saved connection remain live so key rotation or an
    endpoint repair can recover an in-flight job without rebinding it to a new
    identity. A profile-level per-route base_url override is snapshotted
    because that override is part of the profile itself.
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
    # Model selection is content identity: resolve quality aliases ONCE, so
    # reruns cannot silently inherit a later quality-map/default change.
    if profile.asr_provider == "faster_whisper":
        from backend.services.whisper_service import resolve_whisper_model
        data["asr_model"] = resolve_whisper_model(profile.asr_quality, profile.asr_model)
    else:
        data["asr_model"] = profile.asr_model or "whisper-large-v3-turbo"
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
            "tts_connection_id": getattr(tts, "connection_id", None),
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
    gpu_gate=None,
    cancel_check=None,
) -> LocalizationTranscript:
    """Canonical C5 routing. Remote I/O never holds the local GPU gate."""
    from contextlib import asynccontextmanager
    from backend.services.whisper_service import whisper_service, resolve_whisper_model

    if gpu_gate is None:
        from backend.core.tool_runners import localization_gpu_gate
        gpu_gate = localization_gpu_gate

    @asynccontextmanager
    async def local_compute():
        async with gpu_gate():
            try:
                yield
            finally:
                from backend.core.tool_runners import _release_localization_torch_cache_sync
                await asyncio.to_thread(_release_localization_torch_cache_sync)

    fallback_reason = None
    requested_provider = profile.asr_provider
    model = profile.asr_model
    transcript = None
    if requested_provider == "groq":
        from backend.services.groq_asr_service import transcribe_groq, GroqASRError
        try:
            remote_call = transcribe_groq(
                media_path, profile, user_settings, language=language,
            )
            if cancel_check is None:
                transcript = await remote_call
            else:
                # Existing jobs cancel via a persisted DB flag, not Task.cancel.
                task = asyncio.create_task(remote_call)
                try:
                    while not task.done():
                        await asyncio.wait({task}, timeout=0.5)
                        if await cancel_check():
                            from backend.core.exceptions import JobCancelledError
                            raise JobCancelledError("Localization cancelled during Groq ASR")
                    transcript = await task
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        except GroqASRError as exc:
            if not (profile.asr_fallback_to_local and exc.fallback_eligible):
                raise
            # The fallback model is its own explicit selection, never Groq's
            # model name or an implicit quality downgrade to base.
            model = profile.asr_local_model
            if not model or not str(model).strip():
                raise ValueError("Choose an explicit local ASR fallback model.") from exc
            fallback_reason = str(exc)

    if transcript is None:
        model = resolve_whisper_model(profile.asr_quality, model)
        async with local_compute():
            tx = await whisper_service.transcribe(
                str(media_path), language=language,
                quality=profile.asr_quality, model_name=model,
            )
        transcript = localization_transcript_from_source(
            text=tx.get("text"), language=tx.get("language"), source="whisper",
            raw_segments=tx.get("segments") or [],
        )
        executed = dict(tx.get("asr_provenance") or {})
        transcript.metadata["asr"] = {**executed,
            "executor_fallback_reason": executed.get("fallback_reason")}
    if not transcript.segments:
        raise ValueError("No speech detected — nothing to localize.")
    transcript.metadata["asr"] = {
        **transcript.metadata.get("asr", {}),
        "provider": "faster_whisper" if fallback_reason or requested_provider == "faster_whisper" else "groq",
        "model": model or "whisper-large-v3-turbo",
        "requested_provider": requested_provider,
        "requested_model": profile.asr_model,
        "connection_id": getattr(profile, "asr_connection_id", None),
        "fallback_reason": fallback_reason,
    }

    if profile.alignment_mode != "off" or profile.diarization_mode != "off":
        from backend.services.alignment_service import align_localization_transcript
        hf_token = ""
        if user_settings is not None and getattr(user_settings, "huggingface_token_encrypted", None):
            from backend.core.crypto import decrypt_safe
            hf_token = decrypt_safe(user_settings.huggingface_token_encrypted) or ""
        async with local_compute():
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
            "traffic_policy": (_load_json_map(getattr(user_settings, "ai_connections_json", None))
                               .get(connection_id) or {}).get("traffic_policy"),
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
            traffic_policy=saved.get("traffic_policy"),
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


_MAX_AI_FAILURE_HISTORY = 64


class _AIStageRouteState:
    def __init__(self, clients: list):
        if not clients:
            raise ValueError("AI route state requires at least one client")
        self.clients = list(clients)
        self.sticky_index = 0
        self.failure_chain: list[dict] = []
        self.attempt_count = 0
        self.success_count = 0

    @property
    def current(self):
        return self.clients[self.sticky_index]


def _append_ai_failure(route_state: _AIStageRouteState | None, failure: dict) -> None:
    if route_state is None:
        return
    route_state.failure_chain.append(dict(failure))
    if len(route_state.failure_chain) > _MAX_AI_FAILURE_HISTORY:
        del route_state.failure_chain[:-_MAX_AI_FAILURE_HISTORY]


def _selected_ai_connection_id(route_state: _AIStageRouteState | None) -> str | None:
    """Return only a connection that was actually attempted by the stage."""
    if route_state is None or route_state.attempt_count <= 0:
        return None
    if route_state.success_count <= 0 and route_state.failure_chain:
        return route_state.failure_chain[-1].get("connection_id")
    return getattr(route_state.current, "connection_name", None)


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
    localized_terms: dict[str, str] | None = None,
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
            "source_voice": (profile.speaker_genders or {}).get(seg.speaker_id, "unknown"),
        }
        for seg in batch
    ]
    timing = (
        "For dubbing, keep each translation concise and naturally speakable. "
        "The downstream chunk planner will combine adjacent lines and measure real TTS. "
        "When two target-language phrasings preserve exactly the same facts and intent, "
        "prefer the shorter naturally spoken one. Treat source_duration_ms as a "
        "soft pacing hint, never a hard word limit; never omit facts to fit time."
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
- Preserve questions, exclamations, pauses, emphasis and the speaker's intent. Keep short replies short; do not invent emotional cues or change who is addressing whom.
- Preserve all numeric facts, including age. Express written quantities as Arabic digits with the equivalent target-language unit (十八岁 => 18 tuổi).
- For Vietnamese, translate common nouns and use established Vietnamese/Hán–Việt forms for Chinese personal names (吴小彤 => Ngô Tiểu Đồng), place names and forms of address in BOTH captions and spoken text. Do not substitute English/Pinyin pronunciation or invent another person's identity. Explicit glossary/protected terms take priority.
- Include localized_terms for proper names in each item; reuse the job dictionary exactly and use the same name across speakers/batches. Empty {{}} is allowed when no proper name occurs.

STYLE:
{preset}

CUSTOM INSTRUCTIONS:
{custom}

GLOSSARY:
{glossary}

PROTECTED TERMS (preserve literally when present):
{json.dumps(profile.protected_terms, ensure_ascii=False)}

JOB NAME DICTIONARY (already accepted in earlier batches):
{json.dumps(localized_terms or {}, ensure_ascii=False)}

FULL SOURCE CONTEXT:
{transcript_context}

SEGMENTS:
{json.dumps(items, ensure_ascii=False)}

Return ONLY a JSON array:
[{{"id":"segment-id","text":"translated speech","localized_terms":{{"source proper name":"Vietnamese name"}}}}]
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
        if not isinstance(item.get("id"), str) or not isinstance(item.get("text"), str):
            raise ValueError("translation id/text must be strings")
        seg_id = item["id"]
        text = item["text"].strip()
        if not seg_id or not text or seg_id in seen:
            raise ValueError("translation item has missing/duplicate id or empty text")
        seen[seg_id] = text
    if set(seen) != set(expected):
        raise ValueError("translation response IDs do not match input IDs")
    return [seen[seg_id] for seg_id in expected]


def _validate_localized_terms(raw, batch, texts, known, profile, target_language):
    """Accept a job dictionary atomically with the C6 text that uses it."""
    from backend.services.dubbing_rewrite_guard import check_translation_meaning, untranslated_han
    terms = dict(known or {})
    by_id = {item["id"]: item for item in json.loads(_strip_json_fence(raw))}
    for segment, text in zip(batch, texts):
        additions = by_id[segment.id].get("localized_terms", {})
        if not isinstance(additions, dict):
            raise ValueError("localized_terms must be an object")
        for source, target in additions.items():
            if (not isinstance(source, str) or not isinstance(target, str)
                    or not source.strip() or len(source) > 120 or len(target) > 120
                    or source not in segment.source_text or not target.strip() or target not in text
                    or untranslated_han(target, target_language, allowed=profile.protected_terms)):
                raise ValueError("Invalid localized proper name")
            if source in terms and terms[source] != target:
                raise ValueError(f"Inconsistent localized name: {source}")
            if source in profile.glossary and profile.glossary[source] != target:
                raise ValueError(f"Localized name conflicts with glossary: {source}")
            terms[source] = target
    for segment, text in zip(batch, texts):
        violations = check_translation_meaning(
            segment.source_text, text, None, target_language,
            glossary={**terms, **profile.glossary}, protected_terms=profile.protected_terms,
        )
        # Language-aware numeric/polarity checks run in the caller; this check
        # enforces dictionary consistency even when the model omits that field.
        if any(item["kind"] in {"term_lost", "unlocalized_text"} for item in violations):
            raise ValueError(f"Localized dictionary missing from {segment.id}: {violations}")
    return terms


def _translation_guard_checks(batch, texts, profile, source_language, target_language, terms, context_segments):
    from backend.services.dubbing_rewrite_guard import (
        GuardContext, check_translation_meaning, check_translation_group_meaning)

    rows = list(context_segments or batch)
    positions = {segment.id: index for index, segment in enumerate(rows)}
    targets = {s.id:s.translated_text for s in rows if s.translation_status == 'success' and s.translated_text}
    targets.update({segment.id: text for segment, text in zip(batch, texts)})
    contexts = {}
    for segment in batch:
        index = positions.get(segment.id)
        before = rows[index - 1] if index is not None and index > 0 else None
        after = rows[index + 1] if index is not None and index + 1 < len(rows) else None
        if before and not (0 <= segment.source_start_ms - before.source_end_ms <= 1500):
            before = None
        if after and not (0 <= after.source_start_ms - segment.source_end_ms <= 1500):
            after = None
        contexts[segment.id] = GuardContext(
            source_before=before.source_text if before else '', source_after=after.source_text if after else '')
    verdicts = {segment.id: check_translation_meaning(segment.source_text, text,
        source_language, target_language, glossary={**terms, **profile.glossary},
        protected_terms=profile.protected_terms, context=contexts[segment.id], include_advisories=True)
        for segment, text in zip(batch, texts)}
    # Only syntactically open clauses may move their speech-act particle into
    # the following cue. Do not join complete/unrelated questions to hide loss.
    open_clause = re.compile(r'(?:超过|超過|比|在|给|給|对|對|把|被|和|与|與|到|从|從|关于|關於|有沒有|有没有|是否|能不能|会不会|會不會)\s*$')
    for first, second in zip(rows, rows[1:]):
        if first.id not in targets or second.id not in targets:
            continue
        if (first.speaker_id != second.speaker_id
                or not 0 <= second.source_start_ms - first.source_end_ms <= 250
                or second.source_end_ms - first.source_start_ms > 8000
                or not open_clause.search(first.source_text)):
            continue
        group_id = f'{first.id}:{second.id}'
        group = check_translation_group_meaning([
            {'id':s.id,'source_text':s.source_text,'translated_text':targets[s.id]} for s in (first,second)],
            source_language,target_language,glossary={**terms,**profile.glossary},
            protected_terms=profile.protected_terms,include_advisories=True,group_id=group_id)
        for segment in (first, second):
            if segment.id not in verdicts:
                continue
            verdicts[segment.id] = [issue for issue in verdicts[segment.id]
                                   if issue['kind'] not in {'question_changed','exclamation_changed'}]
            verdicts[segment.id].extend({**issue,'group_id':group_id,'segment_ids':[first.id,second.id]}
                for issue in group[segment.id] if issue['kind'] in {'question_changed','exclamation_changed'})
    return verdicts


def _translation_candidates(raw, batch, known, profile, source_language, target_language, *, context_segments=None):
    """Separate shape failures from item-level semantics; commit only safe terms.

    A conflicting proposed name invalidates ALL dependent items, not whichever
    dictionary happened to be visited last. Independent accepted items survive.
    """
    from backend.services.dubbing_rewrite_guard import check_translation_meaning

    texts = _validate_translation_payload(raw, batch)
    by_id = {item["id"]: item for item in json.loads(_strip_json_fence(raw))}
    accepted = {}
    rejected = {}
    choices = {}
    checks = _translation_guard_checks(batch, texts, profile, source_language, target_language, known or {}, context_segments)
    for segment, text in zip(batch, texts):
        item = by_id[segment.id]
        try:
            terms = _validate_localized_terms(json.dumps([item], ensure_ascii=False),
                [segment], [text], known, profile, target_language)
            for source, target in item.get("localized_terms", {}).items():
                choices.setdefault(source, set()).add(target)
            hard = [issue for issue in checks[segment.id] if issue.get('severity') != 'uncertain']
            if hard:
                rejected[segment.id] = hard
            else:
                accepted[segment.id] = {**item, "text": text}
        except ValueError as exc:
            rejected[segment.id] = [{"kind": "localized_terms_invalid", "detail": str(exc)[:180]}]

    conflicts = {source for source, targets in choices.items() if len(targets) > 1}
    for segment in batch:
        if any(source in segment.source_text for source in conflicts):
            accepted.pop(segment.id, None)
            rejected[segment.id] = [{"kind": "term_conflict", "detail": "conflicting localized name"}]

    # A dictionary introduced by one accepted item must also be honoured by its
    # siblings, including siblings omitting localized_terms. Recompute after a
    # rejection so a rejected proposal can never leak into the job dictionary.
    while True:
        terms = dict(known or {})
        for item in accepted.values():
            terms.update(item.get("localized_terms", {}))
        invalid = []
        checks = _translation_guard_checks(batch, texts, profile, source_language, target_language, terms, context_segments)
        for segment in batch:
            if segment.id not in accepted:
                continue
            hard = [issue for issue in checks[segment.id] if issue.get('severity') != 'uncertain']
            if hard:
                invalid.append(segment.id)
                rejected[segment.id] = hard
        if not invalid:
            return accepted, rejected, terms
        for seg_id in invalid:
            accepted.pop(seg_id)


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
        "reference_audio": reference_identity(reference_audio),
        "reference_text": reference_text,
        "reference_language": reference_language,
    }
    if runtime.connection_id:
        payload["connection_id"] = runtime.connection_id
    # Only Edge TTS changed its synthesis/post-processing contract. Include
    # that rule in Edge identities so cached chunks made with injected pause
    # punctuation regenerate once, without touching paid/clone providers.
    if provider == "edge_tts":
        payload["boundary_postprocess"] = _EDGE_TTS_BOUNDARY_RULE
    if provider == "azure_tts":
        payload["target_locale_policy"] = "explicit-vi-v1"
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _promote_chunk_bundle(candidate: Path, destination: Path) -> None:
    """Publish audio/events/identity together, or restore the last-good bundle.

    Invalidate the identity BEFORE changing bytes; publish it LAST. A process
    crash therefore leaves either a hash-bound bundle or a non-reusable artifact.
    Recoverable I/O errors restore the previous bundle, with identity last too.
    """
    import shutil
    import tempfile

    if not candidate.is_file():
        raise FileNotFoundError(f"Missing candidate audio: {candidate.name}")
    suffixes = ("", ".words.json", ".input.json")
    with tempfile.TemporaryDirectory(prefix="promote-", dir=destination.parent) as temp:
        backups = {}
        for suffix in suffixes:
            target = destination.with_name(destination.name + suffix)
            if target.exists():
                backup = Path(temp) / ("previous" + suffix)
                shutil.copy2(target, backup)
                backups[suffix] = backup
        identity = _chunk_identity_path(destination)
        identity.unlink(missing_ok=True)
        try:
            for suffix in suffixes:
                source = candidate.with_name(candidate.name + suffix)
                target = destination.with_name(destination.name + suffix)
                if source.exists():
                    source.replace(target)
                else:
                    target.unlink(missing_ok=True)
        except BaseException:
            identity.unlink(missing_ok=True)
            try:
                for suffix in suffixes:
                    target = destination.with_name(destination.name + suffix)
                    if suffix in backups:
                        backups[suffix].replace(target)
                    else:
                        target.unlink(missing_ok=True)
            except BaseException:
                # Never let an incomplete rollback retain a reuse receipt.
                identity.unlink(missing_ok=True)
                raise
            raise


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
        and payload.get("output") is not None
        and payload.get("output") == _chunk_output_identity(audio_path)
    )


def _chunk_persisted_duration_ms(audio_path: Path, identity: str) -> int | None:
    payload = _chunk_identity_payload(audio_path)
    if not (
        payload and _chunk_identity_matches(audio_path, identity)
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
        "output": _chunk_output_identity(audio_path),
    }
    if duration_ms is not None and duration_ms > 0:
        payload["duration_ms"] = int(duration_ms)
    tmp = sidecar.with_name(sidecar.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(sidecar)


def _chunk_output_identity(audio_path: Path) -> dict | None:
    try:
        digest = hashlib.sha256()
        with audio_path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return {"size": audio_path.stat().st_size, "sha256": digest.hexdigest()}
    except OSError:
        return None


async def _generate_tts_chunk_atomic(
    text: str,
    *,
    output_path: Path,
    identity: str,
    **kwargs,
) -> int:
    """Publish a new raw chunk only after TTS and duration probing succeed."""
    # A unique pending name per call: a provider writer thread that outlived a
    # cancelled attempt must not be able to land on the SAME path a later
    # attempt is using, which would publish one attempt's audio as another's.
    pending = output_path.with_name(
        f"{output_path.stem}.pending-{os.getpid()}-{uuid.uuid4().hex[:8]}{output_path.suffix}")
    pending.unlink(missing_ok=True)
    try:
        duration = await _generate_tts_chunk(
            text,
            output_path=pending,
            **kwargs,
        )
        try:
            _write_chunk_identity(pending, identity, duration)
        except Exception as exc:
            # Missing identity only disables future reuse; it must not invalidate
            # a TTS artifact that was already generated and measured correctly.
            logger.warning(
                "Could not persist synthesis identity for %s: %s",
                output_path.name,
                exc,
            )
        _promote_chunk_bundle(pending, output_path)
        return duration
    finally:
        # A provider that writes from a worker thread (asyncio.to_thread around
        # a SYNC SDK client) cannot be stopped by cancelling the await: the
        # thread finishes and recreates this file after the failure was already
        # reported. Hand the pending path to the bounded sweeper rather than
        # sleeping here - this widens cleanup only, and never publishes
        # anything nor swallows the Timeout/JobCancelledError on its way out.
        _register_abandoned_tts_attempt(pending)
        for suffix in (".words.json", ".input.json"):
            pending.with_name(pending.name + suffix).unlink(missing_ok=True)


def _rewrite_speech_budget_ms(measured_ms: int, window_ms: int, speed_max: float) -> int:
    """Advisory natural-TTS target, not a final playback deadline.

    A 1000-ms window at 1.30x permits up to 1300 ms of speech before the
    existing audio fit. Leave 5% headroom for TTS/FFmpeg variability, rather
    than prompting the LLM to delete meaning to reach 1000 ms naturally.
    This estimate NEVER approves a rewrite: actual TTS measurement and the
    regular/emergency fit ceilings remain authoritative.
    """
    if measured_ms <= 0 or window_ms <= 0 or not math.isfinite(speed_max):
        return max(0, min(measured_ms, window_ms))
    return max(1, min(measured_ms - 1, math.floor(window_ms * speed_max * 0.95)))


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
    # Retries and every other caller must prove a real over-max measurement.
    measured_ms = int((state or {}).get("measured_duration_ms") or 0)
    if measured_ms <= 0 or required_speed(measured_ms, chunk.available_ms) <= profile.dub_speed_max:
        return False
    items = []
    for idx in chunk.item_indexes:
        item = plan[idx]
        seg = segment_by_id[item.segment_id]
        items.append({
            "id": item.segment_id,
            "source": seg.source_text,
            # The model must shorten what will actually be spoken. Feeding it the
            # approved caption text (still in the source script) makes it echo
            # Han back, which is then rejected as unreadable Vietnamese.
            "current": item.spoken_text or seg.translated_text,
        })
    current_words = sum(len(item["current"].split()) for item in items)
    duration_ms = measured_ms
    # Feed the actual natural-rate measurement back into the next attempt,
    # rather than asking the same vague question again. Leave modest headroom
    # for TTS pauses; this budget is guidance, never permission to drop facts.
    speech_budget_ms = _rewrite_speech_budget_ms(
        duration_ms, chunk.available_ms, profile.dub_speed_max,
    )
    ratio = min(0.95, speech_budget_ms / max(duration_ms, 1))
    word_budget = max(len(items), math.floor(current_words * ratio))
    ordered_context = sorted(segment_by_id.values(), key=lambda s: (s.source_start_ms, s.id))
    selected_ids = {item["id"] for item in items}
    positions = [i for i, seg in enumerate(ordered_context) if seg.id in selected_ids]
    neighbours = ([ordered_context[min(positions)-1]] if positions and min(positions) > 0 else [])
    if positions and max(positions)+1 < len(ordered_context):
        neighbours.append(ordered_context[max(positions)+1])
    context = [{"id": seg.id, "speaker_id": seg.speaker_id,
                "source": seg.source_text[:600], "target": (seg.translated_text or "")[:600]}
               for seg in neighbours]
    prior_guard_feedback = (state or {}).get("last_meaning_guard") or []
    prompt = f"""Rewrite these {target_language} dubbing lines for natural speech that can be audio-fitted into the source window without exceeding the normal speed ceiling.
MEASURED TTS: current natural-rate duration {duration_ms} ms; available source window {target_ms} ms; normal playback speed ceiling {profile.dub_speed_max:.2f}x.
Advisory natural-rate speech target: approximately {speech_budget_ms} ms (includes 5% headroom before final audio fitting). This is NOT a requirement to speak naturally within {target_ms} ms, nor permission to lose meaning. Real TTS will be measured again before accepting timing.
Attempt {(state or {}).get('attempt', 1)}. Advisory word budget: about {word_budget} whitespace-separated words (current: {current_words}). This is guidance, NOT a quota or permission to remove facts.
SEMANTIC GUARDRAILS (editable per profile in Settings):
{profile.rewrite_guardrails}

LOCKED PROTOCOL:
- If editable semantic guardrails or profile instructions conflict with this protocol, this protocol wins.
- Preserve every item ID and order.
- Return every input ID exactly once and no extra IDs.
- Return ONLY JSON: [{{"id":"...","text":"..."}}]
- Make wording concise and natural for speech only after the semantic guardrails above are satisfied.
- Preserve who does what to whom, naming relations, negation, quantities and the speech act. Remove repetition/fillers before information.
- If no safe shortening exists, return the current text unchanged for that item. Do not invent or delete information to meet the timing goal.

PREVIOUS REJECTED PROPOSAL (invariant labels only; do not repeat those errors):
{json.dumps(prior_guard_feedback, ensure_ascii=False)}

NEIGHBOUR CONTEXT (read-only; do not output these IDs):
{json.dumps(context, ensure_ascii=False)}

ITEMS:
{json.dumps(items, ensure_ascii=False)}"""
    if profile.glossary:
        prompt += "\nGlossary: " + "; ".join(
            f"{k} => {v}" for k, v in profile.glossary.items()
        )[:1800]
    if profile.protected_terms:
        prompt += "\nProtected terms (preserve literally): " + json.dumps(profile.protected_terms, ensure_ascii=False)
    if profile.rewrite_instructions:
        prompt += (
            "\nPROFILE REWRITE INSTRUCTIONS (supplemental; semantic guardrails and locked protocol above remain active): "
            + profile.rewrite_instructions[:2400]
        )

    def record_unavailable(exc: Exception) -> bool:
        from backend.core.ai_provider import AIProviderCooldownError, AIRequestBudgetError, is_transient_ai_error
        expired = deadline_at is not None and asyncio.get_running_loop().time() >= deadline_at
        if expired or isinstance(exc, AIRequestBudgetError):
            reason, scope = "rewrite_budget_exhausted", "stage_budget"
        elif isinstance(exc, AIProviderCooldownError):
            reason, scope = "rewrite_ai_wait_budget", "admission"
        elif isinstance(exc, (TimeoutError, ConnectionError)) or is_transient_ai_error(exc):
            reason, scope = "rewrite_ai_transient", "chunk"
        else:
            reason, scope = "rewrite_ai_unavailable", "stage"
        logger.warning(
            "Dubbing rewrite unavailable for chunk %s: %s",
            chunk.id,
            exc,
        )
        if state is not None:
            state["retry_scope"] = scope
            state["unavailable"] = scope == "stage"
            state["budget_exhausted"] = scope == "stage_budget"
        if issues is not None:
            issues.append({
                "chunk_id": chunk.id,
                "reason": reason,
                "retry_scope": scope,
                "dispatched": False if isinstance(exc, (AIProviderCooldownError, AIRequestBudgetError)) else None,
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
                "response_chars": len(raw),
                "response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "segment_ids": [plan[i].segment_id for i in chunk.item_indexes],
            })
        return False

    async def retry_on_invalid(detail: str) -> bool | None:
        if route_state is None or route_state.sticky_index + 1 >= len(route_state.clients):
            return None
        current = route_state.current
        _append_ai_failure(route_state, {
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
            # Completion caps also include hidden reasoning on compatible
            # models. LIVE 1000-token runs ended before emitting any JSON.
            # Keep headroom bounded; stage/request deadlines still apply.
            max_tokens=4096,
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
            _append_ai_failure(route_state, {
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
            _append_ai_failure(route_state, {
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
        if not isinstance(item.get("id"), str) or not isinstance(item.get("text"), str):
            retried = await retry_on_invalid("item_types")
            return retried if retried is not None else record_invalid("item_types")
        seg_id = item["id"]
        text = item["text"].strip()
        if not seg_id or not text or seg_id in mapped:
            retried = await retry_on_invalid("invalid_or_duplicate_item")
            return retried if retried is not None else record_invalid("invalid_or_duplicate_item")
        mapped[seg_id] = text
    expected = [x["id"] for x in items]
    if set(mapped) != set(expected):
        retried = await retry_on_invalid("id_mismatch")
        return retried if retried is not None else record_invalid("id_mismatch")

    candidate_text = " ".join(mapped[item["id"]] for item in items)
    if all(mapped[item["id"]] == item["current"] for item in items):
        if state is not None:
            state["no_safe_shortening"] = True
        if issues is not None:
            issues.append({"chunk_id": chunk.id, "reason": "rewrite_no_safe_shortening",
                           "segment_ids": expected, "requires_human_review": True})
        return False
    from backend.services.dubbing_rewrite_guard import untranslated_han
    if untranslated_han(candidate_text, target_language,
            allowed=[*profile.protected_terms, *profile.glossary.values()]):
        return record_invalid("rewrite_reintroduced_foreign_script")
    # Do NOT use characters/whitespace token count as a proxy for speech time:
    # Vietnamese pronunciation, digits and punctuation make that unreliable.
    # The caller synthesizes into an isolated candidate artifact and only
    # accepts rewrites whose measured audio is strictly faster.

    changed = False
    # Meaning guard (Y3): validate EVERY proposed line against the approved
    # text before mutating anything. Atomic by construction: violations reject
    # the whole chunk, so a partially-validated rewrite can never half-apply.
    from backend.services.dubbing_rewrite_guard import check_rewrite_meaning
    meaning_violations: list[dict] = []
    for idx in chunk.item_indexes:
        item = plan[idx]
        new_text = mapped[item.segment_id]
        violations = check_rewrite_meaning(
            item.spoken_text or segment_by_id[item.segment_id].translated_text,
            new_text,
            target_language,
            protected_terms=[*profile.protected_terms, *profile.glossary.values()],
        )
        for v in violations:
            meaning_violations.append({
                **v,
                "segment_id": item.segment_id,
                "chunk_id": chunk.id,
                "before": item.spoken_text,
                "after": new_text,
            })
    # C7 must still satisfy source facts and the job's accepted name dictionary,
    # not merely preserve invariants relative to a potentially imperfect C6.
    if (state or {}).get("source_language"):
        selected = [segment_by_id[plan[i].segment_id] for i in chunk.item_indexes]
        source_checks = _translation_guard_checks(
            selected, [mapped[s.id] for s in selected], profile,
            state["source_language"], target_language,
            state.get("localized_terms") or {}, ordered_context,
        )
        for segment in selected:
            meaning_violations.extend({**issue, "segment_id": segment.id,
                "chunk_id": chunk.id, "before": segment.translated_text,
                "after": mapped[segment.id], "basis": "source"}
                for issue in source_checks[segment.id] if issue.get("severity") == "hard")
    if meaning_violations:
        if state is not None:
            state["last_meaning_guard"] = [
                {"segment_id": row["segment_id"], "kind": row.get("kind", "unknown")}
                for row in meaning_violations[:16]
            ]
        if issues is not None:
            issues.append({
                "chunk_id": chunk.id,
                "reason": "rewrite_meaning_guard",
                "violations": meaning_violations,
                "segment_ids": expected,
            })
        logger.warning(
            "Dubbing rewrite for chunk %s rejected: meaning guard blocked %s",
            chunk.id,
            [v["kind"] for v in meaning_violations],
        )
        return False
    if state is not None:
        state.pop("last_meaning_guard", None)
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


def _audio_peak_db_sync(path: Path) -> float:
    """Decode the actual speech artifact, not its planned interval or sidecar."""
    result = subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-i", str(path),
        "-af", "astats=metadata=0:reset=0", "-vn", "-f", "null", os.devnull,
    ], capture_output=True, text=True, timeout=60)
    matches = re.findall(r"Peak level dB:\s*(-?inf|[-\d.]+)", result.stderr)
    if result.returncode != 0 or not matches:
        raise RuntimeError(f"Cannot decode fitted speech: {path.name}")
    return float(matches[-1])


async def _measure_fitted_audio(path: Path, raw_duration_ms: int, speed_factor: float) -> tuple[int, float]:
    # Natural speed does not imply unchanged bytes: cadence still trims,
    # delays and mixes PCM. Every transformed artifact must be probed.
    # Probe raw artifacts too rather than infer byte identity from a rate.
    duration = await _audio_duration_ms(path)
    peak = await asyncio.to_thread(_audio_peak_db_sync, path)
    return duration, peak


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


async def _prepare_dubbing_speech(transcript, **kwargs):
    from backend.services.dubbing_speech import prepare_speech
    return await prepare_speech(
        transcript, **kwargs, voice_for_chunk=_voice_for_chunk,
        call_ai=_call_ai_chat_resilient, strip_fence=_strip_json_fence,
    )


def _speech_onset_ms(seg: LocalizationSegment) -> int:
    """Anchor speech to the earliest valid word, not padded ASR silence.

    Keep the original end as the budget so short replies can borrow silence.
    These are source word anchors, NOT forced alignment of synthesized audio.
    """
    words = seg.source_words or []
    if not words:
        return seg.source_start_ms
    onset = None

    for word in words:
        if not isinstance(word, dict) or not str(word.get("word") or "").strip():
            continue
        try:
            start, end = float(word["start"]), float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(start) and math.isfinite(end)) or end <= start:
            continue
        # A word's tail can cross an ASR cue boundary (overlapping turns).
        # Only its START determines onset; never extend the cue's end from it.
        if not seg.source_start_ms <= start * 1000 < seg.source_end_ms:
            continue
        # ASR may return overlapping turns/words out of list order. The minimum
        # is still a safe source onset; sorting or assuming words[0] is earliest
        # would hide a real pause or discard an otherwise usable word envelope.
        onset = min(onset, round(start * 1000)) if onset is not None else round(start * 1000)
    # Integer rounding must not turn a valid sub-ms word into a zero window.
    return onset if onset is not None and onset < seg.source_end_ms else seg.source_start_ms


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
    _cadence_retry: bool = False,
    _rewrite_budget: list[float] | None = None,
    job_state_path: Path | None = None,
    _job_scope: str | None = None,
    _planning_text: dict[str, str] | None = None,
    cancel_check=None,
) -> TimedDubResult:
    """C7 donor-derived chunk planner with measured TTS and bounded retiming."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = transcript.model_copy(deep=True)
    out.metadata["target_language"] = target_language
    from backend.services.localization_omissions import validate_translation_omissions
    omitted_ids = validate_translation_omissions(out, profile)
    original_positions = {seg.id: i for i, seg in enumerate(out.segments)}
    omission_followers = set()
    draft_ids = {seg.id for seg in out.segments if seg.translation_status == "draft"}
    if draft_ids:
        if profile.translation_automation != "auto":
            raise RuntimeError("Strict dubbing cannot synthesize unapproved translation drafts.")
        draft_audit = out.metadata.get("translation_auto_drafts") or {}
        if not isinstance(draft_audit, dict) or any(
            not str(seg.translated_text or "").strip()
            or not isinstance(draft_audit.get(seg.id), dict)
            or draft_audit[seg.id].get("source_text") != seg.source_text
            or draft_audit[seg.id].get("target_text") != seg.translated_text
            or not draft_audit[seg.id].get("issues")
            for seg in out.segments if seg.id in draft_ids
        ):
            raise RuntimeError("Translation draft is missing its current source/target issue audit.")
    profile = profile.model_copy(update={"protected_terms": list(dict.fromkeys([
        *profile.protected_terms, *(out.metadata.get("localized_terms") or {}).values()]))})
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

    runtime = _tts_config(user_settings, profile, target_language)
    state_path = Path(job_state_path or work_dir / "dubbing_state.json")
    job_scope = _job_scope or str(state_path.resolve())
    audit_path = work_dir / "rewrite_audit.json"
    job_state = DubbingJobState(state_path, scope=job_scope, binding=tts_binding(runtime, target_language),
                               audit_path=audit_path)
    previous_voice_warnings = job_state.data.get("voice_warnings", [])
    speaker_voices = await resolve_speaker_voices(
        out, profile=profile, runtime=runtime, state=job_state,
        language_voice=_default_speech_voice(target_language),
    )
    if _cadence_retry:
        job_state.data["voice_warnings"] = [warning for warning in previous_voice_warnings
            if warning["code"] not in {"speaker_gender_mismatch", "speaker_gender_unverified"}]
    profile = profile.model_copy(update={"speaker_voices": speaker_voices})
    def save_audit():
        job_state.save()
        write_json_atomic(audit_path, job_state.audit_payload())
    save_audit()

    # Resume accepted C7 text independently of the immutable, reviewed C6
    # artifact. Changed input, voice, reference, or policy invalidates replay.
    approved_text = {seg.id: seg.translated_text for seg in out.segments}
    planning_text = _planning_text if _planning_text is not None else approved_text
    acceptance_input_key = hashlib.sha256(json.dumps({
        "rewrite_policy_version": _C7_REWRITE_POLICY_VERSION,
        "binding": tts_binding(runtime, target_language), "profile": profile.model_dump(),
        "source": str(source_media.resolve()),
        "segments": [{"id": seg.id, "text": seg.translated_text, "source_text": seg.source_text,
                      "start": seg.source_start_ms, "end": seg.source_end_ms,
                      "speaker": seg.speaker_id, "words": seg.source_words, "overrides": seg.overrides,
                      "translation_status": seg.translation_status}
                     for seg in out.segments],
        "references": [reference_identity(path) for path in
                       [profile.tts_reference_audio, *profile.speaker_reference_audio.values()] if path],
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    acceptance_key = (job_state.data["acceptance_key"] if _cadence_retry and job_state.data.get("acceptance_key")
                      else job_state.activate_revision(acceptance_input_key))
    save_audit()
    speech_issues = await _prepare_dubbing_speech(
        out, profile=profile, runtime=runtime, ai=ai, fallback_ai=fallback_ai,
        route_state=route_state, work_dir=work_dir, progress_cb=progress_cb,
    )
    from backend.services.dubbing_rewrite_guard import check_rewrite_meaning
    rewrite_budget = _rewrite_budget if _rewrite_budget is not None else [float(profile.rewrite_total_timeout_s)]

    async def adapt_with_budget(**kwargs):
        # Only AI time spends the shared budget, not TTS/FFmpeg wall time.
        started = asyncio.get_running_loop().time()
        try:
            changed = await _adapt_chunk_items(
                ai, fallback_ai=fallback_ai, route_state=route_state,
                deadline_at=started + max(0.0, rewrite_budget[0]),
                progress_cb=progress_cb, **kwargs,
            )
            return changed
        finally:
            rewrite_budget[0] = max(0.0, rewrite_budget[0] - (asyncio.get_running_loop().time() - started))
    valid_segments = [
        seg for seg in out.segments
        if (seg.translation_status == "success" or seg.id in draft_ids)
        and (seg.translated_text or "").strip()
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
        # Emergency ceiling is NOT a quality limit: it only decides whether an
        # already-exhausted chunk may still be rendered for human review.
        emergency_speed_max=profile.dub_emergency_speed_max,
    )
    cues = []
    for i, seg in enumerate(valid_segments):
        override = seg.overrides or {}
        start_locked = "target_start_ms" in override
        end_locked = "target_end_ms" in override
        pos = original_positions[seg.id]
        # Omitted source windows are real gaps, not newly available borrowable
        # silence. Keep adjacent speech on its own side of each omission.
        if pos > 0 and out.segments[pos-1].id in omitted_ids:
            start_locked = True
            omission_followers.add(seg.id)
        if pos+1 < len(out.segments) and out.segments[pos+1].id in omitted_ids:
            end_locked = True
        timing_locked = start_locked or end_locked
        onset_ms = seg.source_start_ms if start_locked else _speech_onset_ms(seg)
        start_ms = int(override.get("target_start_ms", onset_ms))
        end_ms = int(override.get("target_end_ms", seg.source_end_ms))
        if end_ms <= start_ms:
            raise ValueError(f"Invalid expert timing override for {seg.id}")
        cues.append(DubbingCue(
            index=i + 1,
            segment_id=seg.id,
            start_ms=start_ms,
            end_ms=end_ms,
            text=planning_text.get(seg.id) or "",
            source_text=seg.source_text or "",
            speaker_id=seg.speaker_id,
            voice_id=(str(override.get("voice_id")).strip() or None)
                if override.get("voice_id") is not None else speaker_voices.get(seg.speaker_id),
            timing_locked=timing_locked, start_locked=start_locked, end_locked=end_locked,
        ))
    segment_by_id = {seg.id: seg for seg in out.segments}

    # Probe before planning: trailing silence must be available before an orphan
    # rescue can merge text. Reuse the same duration when composing the track.
    from backend.services.video_utils import probe_duration
    source_duration_s = await asyncio.to_thread(probe_duration, source_media, 0.0)
    source_duration_ms = (
        round(source_duration_s * 1000)
        if source_duration_s > 0
        else None
    )
    # Persist adaptive boundaries independently of raw-audio identities. Text
    # edits may reuse grouping, but timing/voice/semantic changes may not.
    layout_key = hashlib.sha256(json.dumps({
        "rules": 1, "config": asdict(config),
        "cues": [(c.segment_id, c.start_ms, c.end_ms, c.speaker_id, c.voice_id, c.timing_locked) for c in cues],
    }, sort_keys=True).encode()).hexdigest()
    layout_path = work_dir / "cadence_layout.json"
    forced_splits: set[str] = set(omission_followers)
    try:
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        ids = layout.get("split_before", [])
        if (layout.get("key") == layout_key and isinstance(ids, list)
                and all(isinstance(s, str) for s in ids)
                and set(ids) <= {c.segment_id for c in cues}):
            forced_splits = set(ids) | omission_followers
        elif regenerate_chunk_ids:
            raise RuntimeError("Cadence layout changed; rerun the full dubbing stage before targeted regeneration")
    except (OSError, ValueError, AttributeError):
        if regenerate_chunk_ids and layout_path.exists():
            raise RuntimeError("Invalid cadence layout; rerun the full dubbing stage")
    plan, chunks = build_plan(
        cues, config, language=target_language, timeline_end_ms=source_duration_ms,
        split_before_segment_ids=forced_splits,
    )

    # Group the immutable C6 script first. Replayed C7 punctuation/length must
    # not renumber chunks or change which audio a targeted retry owns.
    for item in plan:
        seg = segment_by_id[item.segment_id]
        accepted = job_state.data.get("accepted_text", {}).get(seg.id)
        if accepted:
            if check_rewrite_meaning(seg.translated_text or "", accepted, target_language,
                    protected_terms=[*profile.protected_terms, *profile.glossary.values()]):
                raise ValueError(f"Invalid accepted rewrite for {seg.id}")
            seg.translated_text = accepted
            # Keep the Auto draft audit in sync with the accepted C7 rewrite
            # so a resumed run never reports a stale target_text.
            draft_audit = out.metadata.get("translation_auto_drafts") or {}
            if seg.id in draft_audit:
                draft_audit[seg.id]["target_text"] = accepted
        item.spoken_text = clean_text_for_speech(seg.translated_text or "")
        item.estimated_duration_ms = estimate_spoken_ms(seg.translated_text or "", target_language)
    save_audit()

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
    issues: list[dict] = list(speech_issues)
    rewrite_calls = 0
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

    rewrite_context: dict[int, tuple[int, dict]] = {}
    allocation_ids = regenerate_chunk_ids if targeted_regeneration else None
    previous_durations: dict[int, int] = {}
    synthesis_identities: dict[int, str] = {}
    async def generate_chunk_audio(chunk_id, text, **kwargs):
        events = []
        duration = await _generate_tts_chunk_atomic(text, recovery_events=events, **kwargs)
        recoveries = job_state.data.setdefault("tts_recoveries", {})
        if events:
            recoveries[str(chunk_id)] = {**events[-1], "identity": kwargs["identity"]}
        else:
            recoveries.pop(str(chunk_id), None)
        save_audit()
        return duration

    previous_allocation = job_state.data.get("measured_allocation")
    speaker_resolutions: dict[str, set[str]] = {}
    resolved_voice_genders: dict[str, set[str]] = {}
    resolved_voices: dict[str, dict[str, str]] = {}

    def effective_reference(speaker_id):
        ref = auto_references.get(speaker_id or "__default__") or auto_references.get("__default__") or {}
        return {
            "audio": ref.get("audio") or _reference_for_chunk(profile, speaker_id),
            "text": ref.get("text") or profile.tts_reference_text,
            "language": ref.get("language") or profile.tts_reference_language,
        }

    def observe_chunk(voice, speaker_id, ref, speech_text, synthesis_identity, duration_ms):
        voice_key = _chunk_synthesis_identity(
            text="", runtime=runtime,
            voice="" if provider_value == "gpt_sovits" else voice,
            target_language=target_language, speaker_id=None,
            reference_audio=ref.get("audio") if provider_value == "gpt_sovits" else None,
            reference_text=ref.get("text") if provider_value == "gpt_sovits" else None,
            reference_language=ref.get("language") if provider_value == "gpt_sovits" else None,
        )
        acoustic_identity = voice_key
        if provider_value == "gpt_sovits":
            reference = reference_identity(ref.get("audio")) or {}
            # Identical reference audio at two paths or with different prompt
            # text does not create two voices, even though calibration is split.
            acoustic_identity = "reference:" + reference["sha256"] if reference.get("sha256") else None
        if speaker_id and acoustic_identity:
            speaker_resolutions.setdefault(speaker_id, set()).add(acoustic_identity)
            gender = job_state.data.get("voice_genders", {}).get(voice, "unknown") if provider_value != "gpt_sovits" else "unknown"
            resolved_voice_genders.setdefault(speaker_id, set()).add(gender)
            resolved_voices.setdefault(speaker_id, {})[voice] = gender
        job_state.observe(synthesis_identity, voice_key, speech_text,
                          estimate_spoken_ms(speech_text, target_language), duration_ms)

    for chunk in chunks:
        speaker_id = _chunk_speaker(plan, chunk)
        await _cancel_checkpoint(cancel_check, "TTS chunk")
        explicit_voice = chunk.voice_id
        voice = _voice_for_chunk(profile, runtime, speaker_id, explicit_voice)
        path = work_dir / f"chunk_{chunk.id:04d}.wav"
        raw_paths[chunk.id] = path
        block_paths.append(path)

        ref = effective_reference(speaker_id)
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

        # Capture old measurements BEFORE a target's atomic regeneration
        # replaces its sidecar. Legacy jobs have no saved measured windows.
        if targeted_regeneration and path.exists() and _chunk_identity_matches(path, initial_identity):
            previous_ms = _chunk_persisted_duration_ms(path, initial_identity)
            if previous_ms:
                previous_durations[chunk.id] = previous_ms

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
        transport_recovery = job_state.data.get("tts_recoveries", {}).get(str(chunk.id), {})
        verified_transport_recovery = (
            transport_recovery.get("reason") == "tts_transport_split"
            and transport_recovery.get("identity") == initial_identity
        )
        if (reuse_raw and not verified_transport_recovery
                and provider_value == "edge_tts" and len(chunk.item_indexes) > 1
                and not targeted_regeneration and read_cue_spans(
                    path, [plan[i].spoken_text for i in chunk.item_indexes],
                    _chunk_persisted_duration_ms(path, initial_identity) or 0) is None):
            # Legacy caches contain no measured cue boundaries. Upgrade on a full
            # stage rerun, never regenerate a non-target neighbour on a retry.
            # A verified split intentionally has no measured word boundaries;
            # keep its valid raw audio and replay the human-review receipt.
            reuse_raw = False
        if targeted_regeneration and not is_target_chunk and not reuse_raw:
            raise RuntimeError(
                f"Cannot safely targeted-regenerate chunk(s) {sorted(regenerate_chunk_ids)} "
                f"because non-target chunk {chunk.id} has no valid reusable raw artifact."
            )

        attempts = 0
        rewrite_state: dict = {}

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
            duration_ms = await generate_chunk_audio(chunk.id,
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
                cancel_check=cancel_check,
            )

        chunk.actual_duration_ms = duration_ms
        synthesis_identities[chunk.id] = synthesis_identity
        observe_chunk(voice, speaker_id, ref, speech_text, synthesis_identity, duration_ms)
        rewrite_context[chunk.id] = (attempts, rewrite_state)

    # Measure all neighbours before reclaiming provisional estimated slack.
    # A shared gap must not be reserved by a later cue's outdated estimate and
    # force an unnecessary measured rewrite of the current cue.
    if targeted_regeneration:
        if previous_allocation and previous_allocation.get("key") == layout_key:
            windows = previous_allocation.get("windows", {})
            for chunk in chunks:
                if chunk.id in regenerate_chunk_ids:
                    continue
                saved = windows.get(str(chunk.id), {})
                start, end = saved.get("start_ms"), saved.get("end_ms")
                if (saved.get("identity") != synthesis_identities[chunk.id]
                        or type(start) is not int or type(end) is not int
                        or start < 0 or end <= start):
                    raise RuntimeError("Invalid measured allocation; rerun the full dubbing stage")
                chunk.start_ms, chunk.end_ms = start, end
        else:
            # Upgrade legacy/partial jobs using ALL available real measurements,
            # not build_plan's estimate-derived slots. Freeze only after this
            # reconstruction, so a retry cannot manufacture a neighbour overflow.
            measured = {c.id: c.actual_duration_ms for c in chunks}
            for chunk in chunks:
                chunk.actual_duration_ms = previous_durations.get(chunk.id, measured[chunk.id])
            allocate_silence(plan, chunks, config, timeline_end_ms=source_duration_ms)
            for chunk in chunks:
                chunk.actual_duration_ms = measured[chunk.id]
    allocate_silence(
        plan, chunks, config, timeline_end_ms=source_duration_ms,
        only_chunk_ids=allocation_ids,
    )
    for chunk in chunks:
        is_target_chunk = not targeted_regeneration or chunk.id in regenerate_chunk_ids
        await _cancel_checkpoint(cancel_check, "timing rewrite")
        attempts, rewrite_state = rewrite_context[chunk.id]
        duration_ms = chunk.actual_duration_ms
        speaker_id = _chunk_speaker(plan, chunk)
        voice = _voice_for_chunk(profile, runtime, speaker_id, chunk.voice_id)
        path = raw_paths[chunk.id]
        ref = effective_reference(speaker_id)
        # Y4: only a MEASURED overflow may rewrite text. A chunk that already
        # fits is never touched, and no estimate-derived branch can reach here.
        while (
            is_target_chunk
            and not rewrite_disabled
            and required_speed(duration_ms, chunk.available_ms) > config.speed_max
            and attempts < config.rewrite_max_attempts
        ):
            rewrite_state.update(measured_duration_ms=duration_ms, attempt=attempts + 1,
                                 source_language=out.language,
                                 localized_terms=out.metadata.get("localized_terms") or {})
            # Snapshot the approved text so the audit can prove what changed.
            before_snapshot = {
                plan[i].segment_id: plan[i].spoken_text for i in chunk.item_indexes
            }
            caption_before = {plan[i].segment_id: segment_by_id[plan[i].segment_id].translated_text
                              for i in chunk.item_indexes}
            duration_before_ms = duration_ms
            changed = await adapt_with_budget(
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
            if rewrite_state.get("budget_exhausted"):
                rewrite_disabled = True
                rewrite_unavailable_recorded = True
            if rewrite_state.get("no_safe_shortening") or rewrite_state.get("retry_scope") in {"chunk", "admission", "stage_budget"}:
                break
            if not changed:
                continue
            # The meaning guard proves only semantic acceptability. It does NOT
            # prove duration. Keep the last-good raw audio and script intact
            # until a separately synthesized candidate has actually improved.
            speech_text = chunk_text(plan, chunk)
            candidate_identity = _chunk_synthesis_identity(
                text=speech_text,
                runtime=runtime,
                voice=voice,
                target_language=target_language,
                speaker_id=speaker_id,
                reference_audio=ref.get("audio"),
                reference_text=ref.get("text"),
                reference_language=ref.get("language"),
            )
            candidate_path = path.with_name(
                f"{path.stem}.rewrite-{uuid.uuid4().hex[:8]}{path.suffix}"
            )
            candidate_events: list[dict] = []
            candidate_duration_ms = 0
            accepted_candidate = False
            try:
                try:
                    candidate_duration_ms = await _generate_tts_chunk_atomic(
                        speech_text,
                        output_path=candidate_path,
                        identity=candidate_identity,
                        runtime=runtime,
                        voice=voice,
                        profile=profile,
                        speaker_id=speaker_id,
                        target_language=target_language,
                        reference_audio=ref.get("audio"),
                        reference_text=ref.get("text"),
                        reference_language=ref.get("language"),
                        cancel_check=cancel_check,
                        recovery_events=candidate_events,
                    )
                except Exception as exc:
                    from backend.core.exceptions import JobCancelledError
                    from backend.services.tts_service import retryable_tts_error
                    cause, seen = exc, set()
                    fatal = False
                    while cause is not None and id(cause) not in seen:
                        seen.add(id(cause))
                        if (isinstance(cause, JobCancelledError)
                                or isinstance(cause, OSError)
                                and not isinstance(cause, (ConnectionError, TimeoutError))):
                            fatal = True
                            break
                        cause = cause.__cause__
                    if fatal or not retryable_tts_error(exc):
                        raise
                    issues.append({
                        "reason": "rewrite_tts_unavailable", "chunk_id": chunk.id,
                        "segment_ids": [plan[i].segment_id for i in chunk.item_indexes],
                        "error_type": type(exc).__name__, "requires_human_review": True,
                    })
                    # Optional improvement failed. finally restores text; keep
                    # last-good measured audio and try bounded fit/retime instead.
                    break
                if candidate_duration_ms >= duration_before_ms:
                    # The AI wrote different words, but REAL TTS got no faster.
                    # Reject without touching original audio, sidecars, accepted
                    # C7 text or the current revision's rewrite_count.
                    issues.append({
                        "chunk_id": chunk.id,
                        "reason": "rewrite_measured_not_faster",
                        "duration_before_ms": duration_before_ms,
                        "duration_after_ms": candidate_duration_ms,
                        "segment_ids": [plan[i].segment_id for i in chunk.item_indexes],
                    })
                    for i in chunk.item_indexes:
                        item = plan[i]
                        job_state.data["rewrite_audit"].append({
                            "segment_id": item.segment_id, "chunk_id": chunk.id,
                            "before": before_snapshot[item.segment_id], "after": item.spoken_text,
                            "duration_before_ms": duration_before_ms,
                            "duration_after_ms": candidate_duration_ms,
                            "status": "rejected_measured",
                            "reason": "rewrite_measured_not_faster",
                            "timestamp": _utc_now_iso(),
                            "acceptance_key": acceptance_key,
                        })
                    continue

                # Publish the candidate only AFTER positive measurement. Sidecars
                # remain identity-bound; an interrupted promotion cannot falsely
                # mark wrong audio reusable at the next resume.
                _promote_chunk_bundle(candidate_path, path)
                accepted_candidate = True
                duration_ms = candidate_duration_ms
                synthesis_identities[chunk.id] = candidate_identity
                chunk.actual_duration_ms = duration_ms
                observe_chunk(voice, speaker_id, ref, speech_text, candidate_identity, duration_ms)
                recoveries = job_state.data.setdefault("tts_recoveries", {})
                if candidate_events:
                    recoveries[str(chunk.id)] = {
                        **candidate_events[-1], "identity": candidate_identity,
                    }
                else:
                    recoveries.pop(str(chunk.id), None)

                rewrite_calls += 1
                for i in chunk.item_indexes:
                    item = plan[i]
                    if (item.spoken_text == before_snapshot[item.segment_id]
                            and segment_by_id[item.segment_id].translated_text == caption_before[item.segment_id]):
                        continue
                    entry = {
                        "segment_id": item.segment_id, "chunk_id": chunk.id,
                        "before": before_snapshot[item.segment_id], "after": item.spoken_text,
                        "caption_before": caption_before[item.segment_id],
                        "caption_after": segment_by_id[item.segment_id].translated_text,
                        "reason": "measured_overflow", "duration_before_ms": duration_before_ms,
                        "duration_after_ms": duration_ms, "available_ms": chunk.available_ms,
                        "required_speed_before": required_speed(duration_before_ms, chunk.available_ms),
                        "dub_speed_max": config.speed_max, "timestamp": _utc_now_iso(),
                        "status": "accepted", "acceptance_key": acceptance_key,
                    }
                    job_state.data["rewrite_audit"].append(entry)
                    job_state.data.setdefault("accepted_text", {})[item.segment_id] = item.spoken_text
                    draft_audit = out.metadata.get("translation_auto_drafts") or {}
                    if item.segment_id in draft_audit:
                        draft_audit[item.segment_id]["target_text"] = item.spoken_text
                        draft_audit[item.segment_id].setdefault("dubbing_rewrites", []).append({
                            "before_target_text": before_snapshot[item.segment_id],
                            "target_text": item.spoken_text,
                            "reason": "measured_overflow", "chunk_id": chunk.id,
                            "timestamp": _utc_now_iso(),
                        })
                save_audit()
            finally:
                if not accepted_candidate:
                    for i in chunk.item_indexes:
                        item = plan[i]
                        item.spoken_text = before_snapshot[item.segment_id]
                        item.estimated_duration_ms = estimate_spoken_ms(
                            item.spoken_text, target_language,
                        )
                        # Approved/replayed C6 text remains the authority on
                        # every rejected or failed candidate TTS attempt.
                        segment_by_id[item.segment_id].translated_text = caption_before[item.segment_id]
                    save_audit()
                candidate_path.unlink(missing_ok=True)
                for sidecar in (".words.json", ".input.json"):
                    candidate_path.with_name(candidate_path.name + sidecar).unlink(missing_ok=True)
            allocate_silence(
                plan, chunks, config, timeline_end_ms=source_duration_ms,
                only_chunk_ids=allocation_ids,
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

    for chunk in chunks:
        recovery = job_state.data.get("tts_recoveries", {}).get(str(chunk.id), {})
        if recovery.get("identity") == synthesis_identities.get(chunk.id):
            issues.append({**recovery, "chunk_id": chunk.id,
                "segment_ids": [plan[i].segment_id for i in chunk.item_indexes]})
    # Save SOURCE-coordinate windows even when the downstream hard gate fails.
    # Output-coordinate retime must never be restored and scaled a second time.
    job_state.data["measured_allocation"] = {
        "key": layout_key,
        "windows": {str(c.id): {"start_ms": c.start_ms, "end_ms": c.end_ms,
                                "identity": synthesis_identities[c.id]} for c in chunks},
    }
    save_audit()

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
    soft_overflow: list[dict] = []
    for chunk in chunks:
        speed = required_speed(chunk.actual_duration_ms, chunk.available_ms)
        if speed <= config.speed_max + 1e-6:
            continue
        issue = {
            "chunk_id": chunk.id,
            "reason": "timing_overflow",
            "required_speed": round(speed, 4),
            "speed_max": config.speed_max,
            "available_ms": chunk.available_ms,
            "tts_duration_ms": chunk.actual_duration_ms,
            "segment_ids": [plan[i].segment_id for i in chunk.item_indexes],
        }
        if speed <= config.emergency_speed_max + 1e-6:
            # Bounded soft overflow: every fitting strategy is already spent,
            # the chunk can still be rendered inside its window, and the
            # result is NOT publishable until a human acknowledges this
            # exact QC revision. Both ceilings are persisted so Studio/QC
            # can explain why review is required.
            soft_overflow.append({
                **issue,
                "emergency_speed_max": round(config.emergency_speed_max, 4),
                "soft_overflow": True,
                "requires_human_review": True,
            })
        else:
            issue["emergency_speed_max"] = round(config.emergency_speed_max, 4)
            unresolved.append(issue)
    if unresolved and fail_on_overflow:
        raise LocalizationTimingError(
            unresolved,
            rewrite_count=job_state.rewrite_count,
            all_issues=[*issues, *unresolved],
            rewrite_ai={
                "selected_connection_id": _selected_ai_connection_id(route_state),
                "failure_chain": (
                    list(route_state.failure_chain)
                    if route_state is not None else []
                ),
                "sticky_route_index": (
                    route_state.sticky_index if route_state is not None else None
                ),
                "attempted": (
                    route_state.attempt_count > 0 if route_state is not None else False
                ),
                "attempt_count": (
                    route_state.attempt_count if route_state is not None else 0
                ),
                "success_count": (
                    route_state.success_count if route_state is not None else 0
                ),
            },
        )
    issues.extend(unresolved)
    issues.extend(soft_overflow)

    fitted_plan, fitted_chunks, report = fit_timeline(
        plan,
        chunks,
        config,
        soft_overflow_chunk_ids={issue["chunk_id"] for issue in soft_overflow},
    )
    # Y5: rewrite_count is the number of segments whose text actually changed,
    # not the number of AI round-trips. The two differed before (AI could be
    # called and change nothing), which made the metric unverifiable.
    report.rewrite_count = job_state.rewrite_count

    # Cue offsets inside a single synthesized chunk are heuristic, not forced
    # alignment. Do not let a chunk that merely fits its outer window silently
    # certify a cue emitted seconds before/after its source speech anchor.
    cadence_review_segments: set[str] = set()
    for chunk in fitted_chunks:
        for idx in chunk.item_indexes:
            item = fitted_plan[idx]
            source_onset_ms = round(item.original_start_ms * video_slowdown)
            drift_ms = item.target_start_ms - source_onset_ms
            if abs(drift_ms) > 750:
                cadence_review_segments.add(item.segment_id)
                issues.append({
                    "chunk_id": chunk.id,
                    "reason": "estimated_onset_drift",
                    "segment_ids": [item.segment_id],
                    "source_onset_ms": source_onset_ms,
                    "target_onset_ms": item.target_start_ms,
                    "drift_ms": drift_ms,
                    "tolerance_ms": 750,
                    "timing_basis": (
                        "estimated_chunk_weights" if len(chunk.item_indexes) > 1
                        else "measured_chunk_placement"
                    ),
                    "requires_human_review": True,
                })

    # One measured repair pass, never an unbounded synth/rewrite loop. Do not
    # change numeric chunk ownership during a targeted retry. Only split units
    # with source-word anchors and viable individual windows, never micro-cues.
    # Word-boundary metadata is only useful if it can actually be placed inside
    # the source-anchored cue windows. Previously mere metadata presence skipped
    # adaptive split repair; a later place_cue_spans() failure then stranded the
    # chunk in NEEDS_REVIEW even though the bounded split path could repair it.
    native_cadence_chunks: set[int] = set()
    for cadence_chunk in fitted_chunks:
        if len(cadence_chunk.item_indexes) <= 1:
            continue
        cadence_spans = read_cue_spans(
            raw_paths[cadence_chunk.id],
            [fitted_plan[i].spoken_text for i in cadence_chunk.item_indexes],
            cadence_chunk.actual_duration_ms,
        )
        if not cadence_spans:
            continue
        cadence_ceiling = (
            config.emergency_speed_max
            if any(x.get("chunk_id") == cadence_chunk.id for x in soft_overflow)
            else config.speed_max
        )
        try:
            place_cue_spans(
                [fitted_plan[i] for i in cadence_chunk.item_indexes],
                cadence_chunk,
                cadence_spans,
                speed_ceiling=cadence_ceiling,
                anchor_scale=video_slowdown,
            )
        except ValueError:
            continue
        native_cadence_chunks.add(cadence_chunk.id)
    if cadence_review_segments and not targeted_regeneration and not _cadence_retry:
        new_splits: set[str] = set()
        for chunk in fitted_chunks:
            items = [fitted_plan[i] for i in chunk.item_indexes]
            if chunk.id in native_cadence_chunks:
                continue
            if len(items) < 2 or not any(p.segment_id in cadence_review_segments for p in items):
                continue
            # Judge each boundary on its own viability. A short leading cue must
            # not veto splitting the substantial cues that follow it.
            for previous, item in zip(items, items[1:]):
                if (not item.timing_locked and not previous.timing_locked
                        and segment_by_id[item.segment_id].source_words
                        and segment_by_id[previous.segment_id].source_words
                        and item.original_end_ms - item.original_start_ms >= 700
                        and previous.original_end_ms - previous.original_start_ms >= 700):
                    new_splits.add(item.segment_id)
        if new_splits - forced_splits:
            import shutil
            import tempfile
            # Speculate in an owned directory: a provider failure or newly
            # impossible cue must not destroy the renderable original layout.
            with tempfile.TemporaryDirectory(prefix="cadence-", dir=work_dir) as temp:
                trial = Path(temp)
                for path in work_dir.glob("chunk_*.wav*"):
                    if path.is_file():
                        shutil.copy2(path, trial / path.name)
                write_json_atomic(trial / "dubbing_state.json", job_state.data)
                trial_audit_start = len(job_state.data["rewrite_audit"])
                (trial / layout_path.name).write_text(json.dumps({
                    "key": layout_key, "split_before": sorted(forced_splits | new_splits),
                }, indent=2), encoding="utf-8")
                try:
                    repaired: TimedDubResult | None = None
                    repaired = await synthesize_timed_dub(
                        out, source_media=source_media, target_language=target_language,
                        profile=profile, user_settings=user_settings, work_dir=trial,
                        fail_on_overflow=True, reuse_existing_chunks=True,
                        reference_media=reference_media, progress_cb=progress_cb, _cadence_retry=True,
                        _rewrite_budget=rewrite_budget, _job_scope=job_scope,
                        _planning_text=planning_text,
                    )
                except LocalizationTimingError as exc:
                    # Splitting traded a cadence warning for a hard overflow.
                    # Keep the original layout; the cadence warning still stands.
                    issues.append({"reason": "cadence_repair_rejected_overflow",
                                   "segment_ids": sorted(cadence_review_segments),
                                   "message": str(exc)[:300], "requires_human_review": True})
                except Exception as exc:
                    # Optional repair also tolerates provider/network-specific
                    # exceptions; cancellation (BaseException) still propagates.
                    issues.append({"reason": "cadence_repair_unavailable", "message": str(exc)[:300],
                                   "segment_ids": sorted(cadence_review_segments), "requires_human_review": True})
                else:
                    overflowed = [issue for issue in repaired.issues
                                  if issue.get("reason") == "timing_overflow"]
                    if overflowed:
                        issues.append({"reason": "cadence_repair_rejected_overflow",
                                       "segment_ids": sorted(cadence_review_segments),
                                       "chunks": sorted({issue["chunk_id"] for issue in overflowed
                                                         if issue.get("chunk_id") is not None}),
                                       "requires_human_review": True})
                        repaired = None
                if repaired is not None:
                    repaired.dub_track = work_dir / repaired.dub_track.relative_to(trial)
                    repaired.block_paths = [work_dir / path.relative_to(trial) for path in repaired.block_paths]
                    # Publish layout last; sidecars still reject partial/stale
                    # audio if an I/O interruption occurs during promotion.
                    files = sorted((p for p in trial.rglob("*") if p.is_file()),
                                   key=lambda p: p.name == layout_path.name)
                    for path in files:
                        destination = work_dir / path.relative_to(trial)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        path.replace(destination)
                    job_state.data = json.loads((work_dir / "dubbing_state.json").read_text(encoding="utf-8"))
                    job_state.data.update(acceptance_key=acceptance_key, acceptance_input_key=acceptance_input_key, accepted_text={
                        seg.id: seg.translated_text for seg in repaired.transcript.segments
                        if seg.translated_text != approved_text.get(seg.id)})
                    save_audit()
                    repaired.rewrite_count = job_state.rewrite_count
                    repaired.transcript.metadata["dubbing"]["rewrite_count"] = repaired.rewrite_count
                    repaired.transcript.metadata["dubbing"]["cadence_repair"] = "measured_split"
                    return repaired
                # A rejected layout never changes the chosen script, but its
                # paid/measured rewrite trials retain durable before/after proof.
                trial_state = json.loads((trial / "dubbing_state.json").read_text(encoding="utf-8"))
                job_state.data["rewrite_audit"].extend(
                    {**entry, "status": "discarded_cadence_trial"}
                    for entry in trial_state.get("rewrite_audit", [])[trial_audit_start:])
                save_audit()

    timed_chunks: list[tuple[int, Path]] = []
    fitted_audio: list[dict] = []
    aligned_cue_ids: set[str] = set()
    cadence_placements: dict[int, list[dict]] = {}
    fitted_dir = work_dir / "fitted"
    for position, chunk in enumerate(fitted_chunks):
        await _cancel_checkpoint(cancel_check, "audio fit")
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
        measured_ms, peak_db = await _measure_fitted_audio(
            timed_path, chunk.actual_duration_ms, chunk.speed_factor)
        # Keep the planner-owned cue timing so a later post-fit fallback can
        # invalidate measured cadence without leaving measured placements in the
        # transcript after replacing cadence audio with a whole-chunk fit.
        pre_cadence_timing = {
            fitted_plan[i].segment_id: (
                fitted_plan[i].target_start_ms,
                fitted_plan[i].target_end_ms,
                fitted_plan[i].actual_duration_ms,
                fitted_plan[i].speed_factor,
            )
            for i in chunk.item_indexes
        }
        spans = (read_cue_spans(raw_paths[chunk.id], [fitted_plan[i].spoken_text for i in chunk.item_indexes],
                               chunk.actual_duration_ms) if len(chunk.item_indexes) > 1 else None)
        if spans:
            ceiling = (config.emergency_speed_max if any(x.get("chunk_id") == chunk.id for x in soft_overflow)
                       else config.speed_max)
            try:
                placements = place_cue_spans([fitted_plan[i] for i in chunk.item_indexes], chunk, spans,
                                            speed_ceiling=ceiling, anchor_scale=video_slowdown)
            except ValueError as exc:
                issues.append({"reason": "measured_cadence_overflow", "chunk_id": chunk.id,
                               "message": str(exc), "requires_human_review": True,
                               "segment_ids": [fitted_plan[i].segment_id for i in chunk.item_indexes]})
            else:
                aligned_path = fitted_dir / f"cadence_{chunk.id:04d}.wav"
                await asyncio.to_thread(_fit_cadence_chunk_sync, raw_paths[chunk.id], aligned_path, placements, chunk.start_ms)
                aligned_ms, aligned_peak = await _measure_fitted_audio(aligned_path, chunk.actual_duration_ms, placements[0]["speed_factor"])
                if aligned_ms > chunk.available_ms + _POSTFIT_ROUNDING_TOLERANCE_MS or not math.isfinite(aligned_peak):
                    issues.append({"reason": "measured_cadence_postfit_invalid", "chunk_id": chunk.id,
                                   "measured_ms": aligned_ms, "requires_human_review": True,
                                   "segment_ids": [fitted_plan[i].segment_id for i in chunk.item_indexes]})
                else:
                    timed_path, measured_ms, peak_db = aligned_path, aligned_ms, aligned_peak
                    chunk.speed_factor = placements[0]["speed_factor"]
                    cadence_placements[chunk.id] = placements
                    block_paths.append(aligned_path)
                    for idx, unit in zip(chunk.item_indexes, placements):
                        item = fitted_plan[idx]
                        item.target_start_ms, item.target_end_ms = unit["target_start_ms"], unit["target_end_ms"]
                        item.actual_duration_ms = round(unit["raw_end_ms"]-unit["raw_start_ms"])
                        item.speed_factor = unit["speed_factor"]
                        aligned_cue_ids.add(item.segment_id)
        measured_end = chunk.start_ms + measured_ms
        next_start = (fitted_chunks[position + 1].start_ms
                      if position + 1 < len(fitted_chunks) else round(source_duration_s * 1000 * video_slowdown))
        # Small processing deltas may use REAL idle space, never another
        # speaker's slot or speech that final atrim/-shortest would remove.
        allowed_end = min(chunk.end_ms + _POSTFIT_ROUNDING_TOLERANCE_MS, next_start)
        omission_boundaries = [round(seg.source_start_ms * video_slowdown)
            for seg in out.segments if seg.id in omitted_ids
            and round(seg.source_start_ms * video_slowdown) >= chunk.start_ms]
        if omission_boundaries:
            allowed_end = min(allowed_end, min(omission_boundaries))
        # atempo may differ by a few samples/ms from raw_duration/speed. If
        # actual speech reaches EOF/a neighbour, correct from RAW audio; never
        # trim it or extend the output past the contract. Larger errors fail.
        for _ in range(2):
            excess = measured_end - allowed_end
            if not (math.isfinite(peak_db) and 0 < excess <= _POSTFIT_ROUNDING_TOLERANCE_MS):
                break
            budget_ms = allowed_end - chunk.start_ms
            if budget_ms <= 0:
                break
            regular_cap = (config.emergency_speed_max if any(x.get("chunk_id") == chunk.id for x in soft_overflow)
                           else config.speed_max)
            # Auto may rescue <=30ms processing error with <=1% extra speed,
            # always under the existing emergency ceiling. This is not a
            # global ceiling increase, truncation or overlap allowance.
            cap = (min(config.emergency_speed_max, config.speed_max * 1.01)
                   if profile.translation_automation == "auto" else regular_cap)
            cap = max(regular_cap, cap)
            corrected_speed = chunk.speed_factor * measured_ms / max(1, budget_ms - 2)
            if corrected_speed > cap:
                break
            fitted = fitted_dir / f"chunk_{chunk.id:04d}.wav"
            await asyncio.to_thread(_fit_chunk_audio_sync, raw_paths[chunk.id], fitted, corrected_speed)
            # This rescue fits the WHOLE raw chunk, so provider word-boundary
            # placements no longer describe the bytes we are about to render.
            # Invalidate cue-local measured provenance and restore planner timing;
            # the proportional measured-chunk allocation below will recompute it.
            if chunk.id in cadence_placements:
                cadence_placements.pop(chunk.id, None)
                for index in chunk.item_indexes:
                    item = fitted_plan[index]
                    aligned_cue_ids.discard(item.segment_id)
                    (
                        item.target_start_ms,
                        item.target_end_ms,
                        item.actual_duration_ms,
                        item.speed_factor,
                    ) = pre_cadence_timing[item.segment_id]
                issues.append({
                    "reason": "measured_cadence_postfit_fallback",
                    "chunk_id": chunk.id,
                    "segment_ids": [fitted_plan[i].segment_id for i in chunk.item_indexes],
                    "requires_human_review": True,
                })
            chunk.speed_factor = corrected_speed
            if corrected_speed > regular_cap:
                issues.append({"reason": "postfit_micro_rescue", "chunk_id": chunk.id,
                    "processing_excess_ms": excess, "required_speed": corrected_speed,
                    "speed_max": config.speed_max, "emergency_speed_max": config.emergency_speed_max,
                    "segment_ids": [fitted_plan[i].segment_id for i in chunk.item_indexes],
                    "requires_human_review": True})
            for index in chunk.item_indexes:
                fitted_plan[index].speed_factor = corrected_speed
            timed_path = fitted
            measured_ms, peak_db = await _measure_fitted_audio(timed_path, chunk.actual_duration_ms, corrected_speed)
            measured_end = chunk.start_ms + measured_ms
        if measured_ms <= 0 or not math.isfinite(peak_db) or measured_end > allowed_end:
            failure = {
                "chunk_id": chunk.id, "reason": "postfit_audio_invalid",
                "measured_fitted_duration_ms": measured_ms,
                "measured_end_ms": measured_end, "allowed_end_ms": allowed_end,
                "allocated_end_ms": chunk.end_ms, "silent": not math.isfinite(peak_db),
                # A reviewer must be able to see HOW MUCH the fitted audio
                # overran, not just that it did. Without this the shared
                # error preview renders "speed ?x".
                "required_speed": round(measured_ms / chunk.available_ms, 4)
                if chunk.available_ms > 0 else None,
                "overflow_ms": max(0, measured_end - allowed_end),
                "speed_factor": chunk.speed_factor,
                "segment_ids": [fitted_plan[i].segment_id for i in chunk.item_indexes],
            }
            raise LocalizationTimingError([failure], rewrite_count=job_state.rewrite_count,
                all_issues=[*issues, failure])
        # Cue offsets remain proportional estimates within a measured chunk.
        original_end = fitted_plan[chunk.item_indexes[-1]].target_end_ms
        allocated_ms = original_end - chunk.start_ms
        if allocated_ms > 0 and chunk.id not in cadence_placements:
            for index in chunk.item_indexes:
                item = fitted_plan[index]
                item.target_start_ms = chunk.start_ms + round(
                    (item.target_start_ms - chunk.start_ms) * measured_ms / allocated_ms)
                item.target_end_ms = chunk.start_ms + round(
                    (item.target_end_ms - chunk.start_ms) * measured_ms / allocated_ms)
        if measured_end > chunk.end_ms:
            issues.append({"chunk_id": chunk.id, "reason": "postfit_rounding_extension",
                "extension_ms": measured_end - chunk.end_ms,
                "segment_ids": [fitted_plan[i].segment_id for i in chunk.item_indexes]})
        fitted_audio.append({"chunk_id": chunk.id, "path": str(timed_path),
            "raw_duration_ms": chunk.actual_duration_ms,
            "measured_fitted_duration_ms": measured_ms, "start_ms": chunk.start_ms,
            "end_ms": measured_end, "allocated_end_ms": chunk.end_ms,
            "peak_db": peak_db, "duration_basis": "audio_probe",
            "segment_ids": [fitted_plan[i].segment_id for i in chunk.item_indexes],
            # The chunk audio is always measured; each cue's target interval is
            # measured only when word-boundary placement produced it, otherwise
            # it is a proportional estimate inside this measured chunk.
            "segment_duration_basis": ("measured" if chunk.id in cadence_placements else "allocated")})
        timed_chunks.append((chunk.start_ms, timed_path))
        if chunk.speed_factor > config.speed_accept:
            issues.append({
                "chunk_id": chunk.id,
                "reason": "speed_warning",
                "required_speed": round(chunk.speed_factor, 4),
                "speed_accept": config.speed_accept,
                "segment_ids": [fitted_plan[i].segment_id for i in chunk.item_indexes],
            })

    issues = [issue for issue in issues if not (issue.get("reason") == "estimated_onset_drift"
              and set(issue.get("segment_ids") or []).issubset(aligned_cue_ids))]
    cadence_review_segments.difference_update(aligned_cue_ids)
    measured_cadence_review = {sid for issue in issues if issue.get("reason", "").startswith("measured_cadence_")
                              for sid in issue.get("segment_ids", [])}
    for chunk in fitted_chunks:
        if len(chunk.item_indexes) > 1 and chunk.id not in cadence_placements:
            segment_ids = [fitted_plan[i].segment_id for i in chunk.item_indexes]
            cadence_review_segments.update(segment_ids)
            issues.append({
                "reason": "multi_cue_unverified_timing", "chunk_id": chunk.id,
                "segment_ids": segment_ids, "timing_basis": "allocated",
                "requires_human_review": True,
            })

    soft_overflow_segments = {
        segment_id
        for issue in soft_overflow
        for segment_id in (issue.get("segment_ids") or [])
    }
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
        seg.tts_duration_basis = "measured" if seg.id in aligned_cue_ids else "allocated"
        seg.timing_status = "adjusted" if (
            item.speed_factor > 1.001 or video_slowdown > 1.001
        ) else "fit"
        seg.qc_status = "review" if item.speed_factor > config.speed_accept else "pass"
        if seg.id in draft_ids:
            # Measured timing success does not certify the draft's semantics.
            seg.qc_status = "review"
        if seg.id in cadence_review_segments or seg.id in measured_cadence_review or any(seg.id in issue["segment_ids"] for issue in speech_issues):
            seg.qc_status = "review"
        if seg.id in soft_overflow_segments:
            # A soft-overflow segment may never look clean to QC.
            seg.qc_status = "review"
            seg.timing_status = "overflow"

    total_duration_ms = max(
        round(source_duration_s * 1000 * video_slowdown),
        max((seg.target_end_ms or 0) for seg in out.segments),
    )
    dub_track = work_dir / "dub_track.wav"
    await _cancel_checkpoint(cancel_check, "dub track composition")
    await asyncio.to_thread(
        _compose_chunk_track_sync,
        timed_chunks,
        total_duration_ms=total_duration_ms,
        output_path=dub_track,
    )
    # Persist the actual per-chunk voices too, including expert overrides.
    voice_warnings = [warning for warning in job_state.data.get("voice_warnings", [])
                      if warning["code"] not in {"speaker_gender_mismatch", "speaker_gender_unverified"}
                      and warning["selected_voice"] in resolved_voices.get(warning["speaker_id"], {})]
    for speaker, voices in resolved_voices.items():
        for voice, gender in voices.items():
            voice_warnings.extend(speaker_gender_warnings(speaker, job_state.data.get("speaker_genders", {}).get(speaker, {}),
                                                          voice, gender, provider_value))
    job_state.data["voice_warnings"] = voice_warnings
    save_audit()
    out.metadata["speakers"] = {
        "detected_speakers": job_state.data.get("detected_speakers", []),
        "speaker_voices": speaker_voices,
        "speaker_voice_identities": {speaker: sorted(keys) for speaker, keys in speaker_resolutions.items()},
        "shared_voice": profile.speaker_voice_mode == "shared",
        "available_voices": job_state.data.get("available_voices", []),
        "voice_catalogue": job_state.data.get("voice_catalogue", []),
        "speaker_genders": job_state.data.get("speaker_genders", {}),
        "voice_warnings": voice_warnings, "provider": provider_value, "resolved_voices": resolved_voices,
        "resolved_voice_genders": {speaker: sorted(values) for speaker, values in resolved_voice_genders.items()},
    }
    max_fitted_speed = max([1.0, *[c.speed_factor for c in fitted_chunks]])
    placed_segment_ids = {item.segment_id for item in fitted_plan}
    measured_segment_ids = sorted(aligned_cue_ids)
    allocated_segment_ids = sorted(placed_segment_ids - aligned_cue_ids)
    if measured_segment_ids and allocated_segment_ids:
        segment_tts_duration_basis = "mixed"
    elif measured_segment_ids:
        segment_tts_duration_basis = "measured"
    else:
        segment_tts_duration_basis = "allocated"
    cue_timing_basis = {
        "measured": "measured_word_boundaries_with_source_anchor_placement",
        "allocated": "source_word_anchors_with_estimated_chunk_internal_offsets",
        "mixed": "mixed_measured_word_boundaries_and_estimated_chunk_internal_offsets",
    }[segment_tts_duration_basis]
    segment_duration_basis = {
        segment_id: ("measured" if segment_id in aligned_cue_ids else "allocated")
        for segment_id in placed_segment_ids
    }
    out.metadata["dubbing"] = {
        "chunk_count": len(fitted_chunks),
        "max_raw_required_speed": max_raw_required_speed,
        "max_fitted_speed": max_fitted_speed,
        "rewrite_count": job_state.rewrite_count,
        "rewrite_calls": rewrite_calls,
        "rate_calibration": job_state.calibration(),
        "rewrite_audit": job_state.data["rewrite_audit"], "acceptance_key": acceptance_key,
        "video_slowdown": video_slowdown,
        "cue_timing_basis": cue_timing_basis,
        "segment_tts_duration_basis": segment_tts_duration_basis,
        "segment_duration_basis": segment_duration_basis,
        "measured_segment_ids": measured_segment_ids,
        "allocated_segment_ids": allocated_segment_ids,
        "aligned_segment_ids": sorted(aligned_cue_ids),
        "cadence_placements": cadence_placements,
        "fitted_audio": fitted_audio,
        "cadence_split_before": sorted(forced_splits),
        "issues": issues,
        "rewrite_ai": {
            "selected_connection_id": _selected_ai_connection_id(route_state),
            "failure_chain": (
                list(route_state.failure_chain)
                if route_state is not None else []
            ),
            "sticky_route_index": (
                route_state.sticky_index if route_state is not None else None
            ),
            "attempted": (
                route_state.attempt_count > 0 if route_state is not None else False
            ),
            "attempt_count": (
                route_state.attempt_count if route_state is not None else 0
            ),
            "success_count": (
                route_state.success_count if route_state is not None else 0
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
        rewrite_count=job_state.rewrite_count,
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
    # C7 may have rewritten an Auto draft's target text (timing overflow). The
    # dubbed transcript carries the updated audit; merge it so the persisted C6
    # audit never goes stale relative to the final spoken wording.
    dubbed_audits = dubbed.metadata.get("translation_auto_drafts") or {}
    if dubbed_audits:
        out_audits = out.metadata.setdefault("translation_auto_drafts", {})
        for seg_id, audit in dubbed_audits.items():
            if isinstance(audit, dict):
                out_audits[seg_id] = audit
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
        if seg.translation_status == "omitted":
            continue
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



# Bounds of the bounded source re-check that lives in
# localization_translation. The in-flight registry is owned HERE because it is
# observable module state: tests reset and await it through this module's
# attribute, so the moved code reads it back through the facade instead of
# keeping a private set that a patch on this name would not reach.
_AUTO_RECHECK_MAX_CUES = 3
_AUTO_RECHECK_TIMEOUT_S = 15.0
_AUTO_RECHECK_TASKS: set[asyncio.Task] = set()

# ── C6 translation stage ───────────────────────────────────────────────
# Moved to backend.services.localization_translation for a responsibility
# boundary, and re-exported here rather than left behind: callers (including
# tool_runners) import these names from this module, and several tests
# monkeypatch them as ATTRIBUTES of it.
#
# This import sits at the END of the module deliberately. The moved module
# imports its collaborators FROM here, so this line must run after every one
# of them is defined - importing at the top raises ImportError on names that
# are declared further down.
#
# The reverse direction matters just as much. Inside localization_translation
# the collaborators that tests patch resolve through `_facade` at call time,
# so `localization_service._call_ai_chat_resilient = <fake>` still reaches the
# code the runner executes. A plain `from ... import name` in the moved module
# would bind the original object and silently stop the patch from applying -
# a seam that fails GREEN, which is why the proxies are pinned by tests.
from backend.services.localization_translation import (  # noqa: E402,F401
    _TranslationDispatchBudget,
    _attach_ai_failure_context,
    _call_ai_chat_resilient,
    _emit_ai_progress,
    _recheck_pending_sources,
    _safe_ai_error_text,
    _translate_batch_resilient,
    translate_transcript,
)


# ── TTS / C7 stage ─────────────────────────────────────────────────────
# Moved to backend.services.localization_tts for a responsibility boundary.
# Imported at the END of this module on purpose: the moved module imports
# its collaborators back from here, so this line must run after they exist.
from backend.services.localization_tts import (  # noqa: E402,F401
    _tts_config,
    validate_localization_tts_preflight,
    _default_speech_voice,
    _chunk_speaker,
    _voice_for_chunk,
    _reference_for_chunk,
    _extract_reference_clip_sync,
    _build_auto_voice_references,
    _audio_duration_ms,
    _trim_edge_tts_boundary_silence_sync,
    _fit_cadence_chunk_sync,
    _concat_tts_parts_sync,
    _edge_zero_audio_transport_failure,
    _generate_tts_chunk,
)
