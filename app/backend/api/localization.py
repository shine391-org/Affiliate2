# SPDX-License-Identifier: AGPL-3.0-only
"""Phase-C localization control plane: batch, resume, QC exceptions, expert overrides."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from backend.config import settings
from backend.core.localization import LocalizationProfile, LocalizationTranscript
from backend.services.localization_stage_service import (
    MANIFEST_VERSION,
    LocalizationRun,
    ManifestTransaction,
    STAGES,
)

router = APIRouter(prefix="/localization", tags=["localization"])


class SegmentOverride(BaseModel):
    id: str
    translated_text: str | None = None
    target_start_ms: int | None = Field(default=None, ge=0)
    target_end_ms: int | None = Field(default=None, ge=0)
    voice_id: str | None = None
    speaker_id: str | None = None
    clear_timing: bool = False
    clear_voice: bool = False


class ExpertOverrideRequest(BaseModel):
    segments: list[SegmentOverride] = Field(default_factory=list)
    profile_updates: dict[str, Any] = Field(default_factory=dict)
    revision: str | None = None


class RerunRequest(BaseModel):
    from_stage: Literal[
        "separation", "asr", "semantic", "translation", "dubbing",
        "audio_mix", "final_render", "qc",
    ] | None = None


class TTSRebindRequest(BaseModel):
    source: Literal["settings"] = "settings"
    voice_id: str | None = None
    model: str | None = None


class ProviderRebindRequest(BaseModel):
    """Adopt the CURRENT Settings provider binding onto the same persisted job."""

    source: Literal["settings"] = "settings"


class AutoRepairRequest(BaseModel):
    enabled: bool


class ReviewApprovalRequest(BaseModel):
    revision: str = Field(min_length=16)


class QCApprovalRequest(BaseModel):
    approved: bool
    revision: str = Field(min_length=16)


# Only GPT-SoVITS consumes reference audio in the canonical TTS interface; every
# other provider ignores it. A reference clip is therefore provider-scoped state
# once the provider changes to one that cannot use it.
_CLONE_CAPABLE_TTS_PROVIDERS = {"gpt_sovits"}


def _provider_scoped_tts_resets(
    effective_profile: dict,
    binding: dict,
) -> dict[str, Any]:
    """Drop TTS state that cannot be valid for the newly bound provider.

    ``speaker_voices`` holds provider voice IDs (an ElevenLabs voice_id is
    meaningless to Edge/Azure/OpenAI) and is preferred over the runtime default
    voice in ``_voice_for_chunk``, so a stale entry silently wins. Clone
    references are only honoured by clone-capable providers.
    """
    previous_provider = str(effective_profile.get("tts_provider") or "").strip().lower()
    next_provider = str(binding.get("tts_provider") or "").strip().lower()
    previous_connection = str(effective_profile.get("tts_connection_id") or "").strip().lower()
    next_connection = str(binding.get("tts_connection_id") or "").strip().lower()
    provider_changed = bool(
        previous_provider and next_provider and previous_provider != next_provider
    )
    connection_changed = bool(
        previous_connection and next_connection and previous_connection != next_connection
    )
    if not provider_changed and not connection_changed:
        return {}

    resets: dict[str, Any] = {
        "speaker_voices": {},
        "speaker_reference_audio": {},
    }
    if next_provider not in _CLONE_CAPABLE_TTS_PROVIDERS:
        resets.update({
            "tts_reference_audio": None,
            "tts_reference_text": None,
            "tts_reference_language": None,
        })
    return resets


class RenderRerunRequest(BaseModel):
    target_aspect: Literal["source", "9:16", "16:9", "1:1"] | None = None
    mask_box: dict[str, Any] | None = None
    caption_placement: dict[str, Any] | None = None


_ASR_KEYS = {
    "asr_provider", "asr_model", "asr_quality",
    "asr_connection_id", "asr_fallback_to_local", "asr_local_model",
    "alignment_mode", "diarization_mode", "diarization_speakers",
}
_AI_TRANSLATION_ROUTE_KEYS = {
    "translation_routes",
    "translation_provider", "translation_model", "translation_base_url",
    "translation_fallback_provider", "translation_fallback_model", "translation_fallback_base_url",
}
_SEMANTIC_KEYS = {
    "semantic_segmentation", "semantic_ai_retry_attempts",
    "semantic_ai_attempt_timeout_s", "semantic_ai_total_timeout_s",
    # Semantic refinement consumes the C6 route chain too, so route changes
    # invalidate semantic rather than reusing cues made by a different model.
    *_AI_TRANSLATION_ROUTE_KEYS,
}
_TRANSLATION_KEYS = {
    *_AI_TRANSLATION_ROUTE_KEYS,
    "translation_retry_attempts", "translation_attempt_timeout_s", "translation_total_timeout_s",
    "translation_preset", "translation_instructions", "translation_guardrails", "glossary",
}
_DUBBING_KEYS = {
    "rewrite_routes",
    "rewrite_provider", "rewrite_model", "rewrite_base_url",
    "rewrite_fallback_provider", "rewrite_fallback_model", "rewrite_fallback_base_url",
    "rewrite_retry_attempts", "rewrite_attempt_timeout_s", "rewrite_total_timeout_s",
    "rewrite_guardrails", "rewrite_instructions",
    "tts_connection_id", "tts_provider", "tts_model", "tts_voice", "tts_base_url", "tts_region",
    "speaker_voices", "speaker_reference_audio", "tts_reference_audio",
    "tts_reference_text", "tts_reference_language", "voice_clone_reference_mode",
    "dub_min_cue_duration_ms",
    "dub_max_chunk_size", "dub_gap_tolerance_ms", "dub_speed_accept",
    "dub_speed_max", "dub_rewrite_attempts", "video_retime_mode",
    "video_retime_max_slowdown",
}
_AUDIO_KEYS = {"preserve_background", "background_gain_db", "dub_gain_db"}
_RENDER_KEYS = {"caption_style", "target_aspect", "render_profile"}
_QC_KEYS = {"qc_profile"}


def _load_run(job_id: str) -> LocalizationRun:
    try:
        return LocalizationRun.load(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Localization run not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _job_status(job_id: str) -> str:
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job

    async with AsyncSessionLocal() as db:
        status = (await db.execute(
            select(Job.status).where(Job.id == job_id)
        )).scalar_one_or_none()
    if status is None:
        raise HTTPException(status_code=404, detail="Job row not found")
    return str(status)


async def _assert_job_inactive(job_id: str) -> None:
    status = await _job_status(job_id)
    if status in {"running", "pending"}:
        raise HTTPException(
            status_code=409,
            detail="Localization job is already active; wait for it to finish before editing or rerunning.",
        )


async def _claim_job_mutation(
    job_id: str,
    *,
    allowed_statuses: set[str] | tuple[str, ...] | list[str] | None = None,
) -> dict:
    """CAS-lock one inactive Job row before touching its localization manifest."""
    from backend.agents.job_helper import claim_job_for_restart

    token = await claim_job_for_restart(
        job_id, allowed_statuses=allowed_statuses,
    )
    if token is not None:
        return token

    status = await _job_status(job_id)
    if allowed_statuses and status not in set(allowed_statuses):
        raise HTTPException(
            status_code=409,
            detail=f"Localization job is in state {status!r}; this action is not allowed.",
        )
    raise HTTPException(
        status_code=409,
        detail="Localization job changed while this action was starting. Refresh and try again.",
    )


async def _restore_job_mutation(job_id: str, token: dict) -> None:
    """Release a mutation claim that did not dispatch background work."""
    from backend.agents.job_helper import restore_job_restart_claim

    restored = await restore_job_restart_claim(job_id, token)
    if not restored:
        raise HTTPException(
            status_code=409,
            detail="Localization job state changed while the action was being rolled back.",
        )


def _earliest_stage(stages: list[str]) -> str:
    return min(stages, key=STAGES.index)


def _profile_invalidation(keys: set[str]) -> str | None:
    stages: list[str] = []
    if "audio_mode" in keys:
        stages.append("separation")
    if keys & _ASR_KEYS:
        stages.append("asr")
    if keys & _SEMANTIC_KEYS:
        stages.append("semantic")
    if keys & _TRANSLATION_KEYS:
        stages.append("translation")
    if keys & _DUBBING_KEYS:
        stages.append("dubbing")
    if keys & _AUDIO_KEYS:
        stages.append("audio_mix")
    if keys & _RENDER_KEYS:
        stages.append("final_render")
    if keys & _QC_KEYS:
        stages.append("qc")
    return _earliest_stage(stages) if stages else None


def _optional_json(run: LocalizationRun, stage: str, key: str):
    try:
        return run.load_json(stage, key)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _verify_run_source_identity(run: LocalizationRun) -> None:
    """Preflight before destructive mutation.

    _load_run always returns LocalizationRun in production. The getattr keeps
    lightweight unit-test doubles focused on the behavior under test.
    """
    verifier = getattr(run, "verify_source_identity", None)
    if verifier is not None:
        verifier()


def _review_revision(run: LocalizationRun, stage: str, artifact: str) -> str | None:
    """Stable hash for the exact persisted artifact the user reviewed."""
    path = run.artifact(stage, artifact)
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _qc_review_revision(run: LocalizationRun) -> str | None:
    """Revision token for the exact QC report AND exact rendered video bytes."""
    report_revision = _review_revision(run, "qc", "report")
    video_revision = _review_revision(run, "final_render", "final_video")
    if not report_revision or not video_revision:
        return None
    return hashlib.sha256(
        f"{report_revision}|{video_revision}".encode("utf-8")
    ).hexdigest()


def _job_detail(run: LocalizationRun) -> dict:
    translation = _optional_json(run, "translation", "transcript")
    dubbing_tx = _optional_json(run, "dubbing", "transcript")
    plan = _optional_json(run, "dubbing", "plan")
    chunks = _optional_json(run, "dubbing", "chunks")
    qc = _optional_json(run, "qc", "report")
    request = run.request
    return {
        "manifest": run.snapshot(),
        "transcript": dubbing_tx or translation,
        "plan": plan or [],
        "chunks": chunks or [],
        "qc": qc,
        "qc_human_approved": bool(request.get("qc_human_approved")),
        "qc_review_revision": _qc_review_revision(run),
        "translation_review_revision": _review_revision(run, "translation", "transcript"),
        "translation_review_pending": bool(
            run.is_complete("translation", ("transcript",))
            and request.get("translation_review_pending")
            and not request.get("translation_review_approved")
        ),
    }


async def _dispatch_existing_run(run: LocalizationRun, *, already_claimed: bool = False):
    from backend.agents.job_helper import restart_job
    from backend.core.task_runner import dispatch
    from backend.core.tool_runners import run_tool_translate

    if not run.source_path.exists():
        raise HTTPException(status_code=409, detail="Persisted source media is missing")
    try:
        run.verify_source_identity()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if already_claimed:
        if await _job_status(run.job_id) != "pending":
            raise HTTPException(
                status_code=409,
                detail="Localization recovery claim was lost before dispatch.",
            )
    elif not await restart_job(run.job_id):
        raise HTTPException(
            status_code=409,
            detail="Localization job became active before the rerun could be claimed.",
        )

    req = run.request
    effective = run.effective_profile
    dispatch(run_tool_translate(
        run.job_id,
        run.source_path,
        str(req.get("target_language") or "Vietnamese"),
        str(req.get("mode") or "full_dub"),
        str(req.get("voice_id") or ""),
        str(effective.get("caption_style") or req.get("caption_style") or "viral"),
        str(req.get("emoji_style") or "moderate"),
        target_aspect=effective.get("target_aspect") or req.get("target_aspect"),
        profile_id=req.get("profile_id"),
        profile_override=effective,
        review_translation=bool(req.get("review_translation")),
        cleanup_input=False,
        mask_box=req.get("mask_box"),
        caption_placement=req.get("caption_placement"),
        downloaded_video_id=req.get("downloaded_video_id"),
    ))


async def _resolve_current_tts_binding(
    effective_profile: dict,
    target_language: str,
    *,
    voice_id: str | None = None,
    model: str | None = None,
    preserve_binding: bool = False,
    user_id: str = "local",
) -> dict:
    """Resolve and cheaply validate the current Settings-owned TTS connection."""
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.user_settings import UserSettings
    from backend.services.localization_service import (
        _tts_config,
        validate_localization_tts_preflight,
    )

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )).scalar_one_or_none()

    candidate = dict(effective_profile)
    if not preserve_binding:
        candidate.update({
            "tts_connection_id": None,
            "tts_provider": None,
            "tts_model": model.strip() if model and model.strip() else None,
            "tts_voice": voice_id.strip() if voice_id and voice_id.strip() else None,
            "tts_base_url": None,
            "tts_region": None,
        })
    from backend.core.exceptions import VoiceGenerationError
    import httpx

    try:
        profile = LocalizationProfile.model_validate(candidate)
        runtime = _tts_config(row, profile, target_language)
        # Reuse the same canonical preflight used by a fresh localization job.
        # This keeps deterministic provider checks (for example GPT-SoVITS
        # language constraints) and provider health semantics in one place.
        health = await validate_localization_tts_preflight(
            row, profile, target_language,
        )
    except (ValueError, VoiceGenerationError, httpx.HTTPError, OSError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Current TTS preflight failed. Check the selected connection, credentials, "
                   "endpoint, model and voice in Settings before trying again.",
        ) from exc
    if not health.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=health.get("message") or "Current TTS Settings are not usable.",
        )
    return {
        "tts_connection_id": runtime.connection_id,
        "tts_provider": getattr(runtime.provider, "value", str(runtime.provider)),
        "tts_model": runtime.model,
        "tts_voice": runtime.default_voice or None,
        # Keep connection-owned infrastructure live. Persisting the resolved
        # endpoint here would turn a rebind into a stale per-job override and
        # defeat later endpoint/region repairs on the same connection ID.
        "tts_base_url": None,
        "tts_region": None,
    }


async def _describe_tts_bindings(run: LocalizationRun) -> dict:
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.user_settings import UserSettings
    from backend.services.localization_service import _tts_config

    keys = ("tts_connection_id", "tts_provider", "tts_model", "tts_voice", "tts_base_url", "tts_region")
    job_binding = {key: run.effective_profile.get(key) for key in keys}
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                select(UserSettings).where(UserSettings.user_id == "local")
            )).scalar_one_or_none()
        candidate = dict(run.effective_profile)
        candidate.update({key: None for key in keys})
        profile = LocalizationProfile.model_validate(candidate)
        runtime = _tts_config(
            row, profile,
            str(run.request.get("target_language") or "Vietnamese"),
        )
        current_binding = {
            "tts_connection_id": runtime.connection_id,
            "tts_provider": getattr(runtime.provider, "value", str(runtime.provider)),
            "tts_model": runtime.model,
            "tts_voice": runtime.default_voice or None,
            "tts_base_url": runtime.base_url,
            "tts_region": runtime.region,
        }
        error = None
    except Exception as exc:
        current_binding = None
        error = str(exc)[:200]
    binding_mode = str(run.request.get("tts_binding_mode") or "legacy_snapshot")
    provider_mismatch = bool(
        current_binding
        and job_binding.get("tts_provider")
        and job_binding.get("tts_provider") != current_binding.get("tts_provider")
    )
    if binding_mode == "pinned":
        # A pinned connection is intentional and resolves its own live
        # credential by stable ID. Global active TTS drift is not a mismatch.
        binding_mismatch = False
    else:
        # Inherited jobs (and legacy snapshots where provenance was not stored)
        # may legitimately be offered an explicit rebind when Settings drift.
        binding_mismatch = bool(
            current_binding
            and any(
                (job_binding.get(key) or None) != (current_binding.get(key) or None)
                for key in keys
            )
        )
    return {
        "job": job_binding,
        "current": current_binding,
        "binding_mode": binding_mode,
        "provider_mismatch": provider_mismatch,
        "binding_mismatch": binding_mismatch,
        "current_error": error,
    }


# ── Provider binding (C6/C7/TTS identity) ───────────────────────────────────
#
# The read model above is TTS-only and job-scoped. The provider-binding UX also
# has to answer "did the translation/rewrite routes this job was created with
# still match Settings?", because a drifted route is just as expensive to
# discover late as a drifted voice. The rules below are deliberately narrow:
#
#   * compared identity is the STABLE route identity (connection id + model +
#     an explicit per-route base_url override), and for TTS the connection id,
#     provider, model, voice and explicit overrides;
#   * a saved connection's live endpoint and rotated key are infrastructure and
#     are never part of identity, and no secret ever enters the payload;
#   * rewrite/TTS are only meaningful for a full-dub job;
#   * the CURRENT side is the profile the job names (``request.profile_id`` or
#     the snapshot id), materialized against live Settings — not the job's own
#     stored snapshot.

_TRANSLATION_BINDING_KEYS = (
    "translation_routes",
    "translation_provider", "translation_model", "translation_base_url",
    "translation_fallback_provider", "translation_fallback_model",
    "translation_fallback_base_url",
)
_REWRITE_BINDING_KEYS = (
    "rewrite_routes",
    "rewrite_provider", "rewrite_model", "rewrite_base_url",
    "rewrite_fallback_provider", "rewrite_fallback_model",
    "rewrite_fallback_base_url",
)
_ASR_BINDING_KEYS = (
    "asr_provider", "asr_model", "asr_quality", "asr_connection_id",
    "asr_fallback_to_local", "asr_local_model",
)
_TTS_BINDING_KEYS = (
    "tts_connection_id", "tts_provider", "tts_model", "tts_voice",
    "tts_base_url", "tts_region",
)


def _requested_profile_id(run: LocalizationRun) -> str | None:
    value = run.request.get("profile_id") or run.effective_profile.get("id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError("Job không có profile gốc. Hãy kiểm tra Localization Profile trong Settings.")


def _is_dub_run(run: LocalizationRun) -> bool:
    return str(run.request.get("mode") or "full_dub").strip().lower() == "full_dub"


def _route_identity(routes) -> list[dict]:
    """Ordered stable route identity: connection + model + explicit override.

    Endpoint/key are intentionally absent: they belong to the saved connection
    and must stay live so an endpoint repair or key rotation recovers an
    in-flight job instead of flagging it as drift.
    """
    identity: list[dict] = []
    for route in routes or []:
        if isinstance(route, dict):
            connection_id = route.get("connection_id")
            model = route.get("model")
            base_url = route.get("base_url")
        else:
            connection_id = getattr(route, "connection_id", None)
            model = getattr(route, "model", None)
            base_url = getattr(route, "base_url", None)
        identity.append({
            "connection_id": connection_id,
            "model": model,
            "base_url": base_url,
        })
    return identity


def _tts_identity(profile: dict) -> dict | None:
    if not any(profile.get(key) for key in _TTS_BINDING_KEYS):
        return None
    return {key: profile.get(key) for key in _TTS_BINDING_KEYS}


def _binding_identity(profile: Any) -> dict:
    """Normalize either a stored snapshot dict or a materialized model dump."""
    if hasattr(profile, "model_dump"):
        profile = profile.model_dump()
    translation_routes = profile.get("translation_routes")
    if not translation_routes:
        # Legacy manifests may predate route lists; fall back to the single
        # primary + fallback pair so they still compare meaningfully.
        translation_routes = [
            {
                "connection_id": profile.get("translation_provider"),
                "model": profile.get("translation_model"),
                "base_url": profile.get("translation_base_url"),
            },
        ]
        if profile.get("translation_fallback_provider"):
            translation_routes.append({
                "connection_id": profile.get("translation_fallback_provider"),
                "model": profile.get("translation_fallback_model"),
                "base_url": profile.get("translation_fallback_base_url"),
            })
    rewrite_routes = profile.get("rewrite_routes")
    if not rewrite_routes:
        rewrite_routes = [
            {
                "connection_id": profile.get("rewrite_provider"),
                "model": profile.get("rewrite_model"),
                "base_url": profile.get("rewrite_base_url"),
            },
        ]
        if profile.get("rewrite_fallback_provider"):
            rewrite_routes.append({
                "connection_id": profile.get("rewrite_fallback_provider"),
                "model": profile.get("rewrite_fallback_model"),
                "base_url": profile.get("rewrite_fallback_base_url"),
            })
    return {
        "translation_routes": _route_identity(translation_routes),
        "rewrite_routes": _route_identity(rewrite_routes),
        "tts": _tts_identity(profile),
        "asr": {key: profile.get(key, LocalizationProfile.model_fields[key].default) for key in _ASR_BINDING_KEYS},
    }


def _binding_diff(job_profile: Any, current_profile: Any, *, is_dub: bool) -> dict:
    """Which stages drifted, and the earliest stage a rebind must rerun from."""
    job_identity = _binding_identity(job_profile)
    current_identity = _binding_identity(current_profile)

    changed: list[str] = []
    if job_identity["asr"] != current_identity["asr"]:
        changed.append("asr")
    if job_identity["translation_routes"] != current_identity["translation_routes"]:
        changed.append("translation")
    if is_dub:
        if job_identity["rewrite_routes"] != current_identity["rewrite_routes"]:
            changed.append("rewrite")
        if job_identity["tts"] != current_identity["tts"]:
            changed.append("tts")

    # Identity comparison selects changed fields; the canonical profile
    # dependency contract owns the earliest invalidated stage.
    changed_keys: set[str] = set()
    if "asr" in changed:
        changed_keys.update(_ASR_BINDING_KEYS)
    if "translation" in changed:
        changed_keys.update(_TRANSLATION_BINDING_KEYS)
    if "rewrite" in changed:
        changed_keys.update(_REWRITE_BINDING_KEYS)
    if "tts" in changed:
        changed_keys.update(_TTS_BINDING_KEYS)
    from_stage = _profile_invalidation(changed_keys)
    return {"changed_stages": changed, "from_stage": from_stage}


async def _load_current_profile_for_binding(run: LocalizationRun, *, user_id: str = "local"):
    """Resolve the profile this job names against the LIVE Settings.

    Read-only. Raises ValueError with an actionable message when Settings or
    the named profile is missing, so the caller can fail before mutating
    anything.
    """
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.user_settings import UserSettings
    from backend.services.localization_service import (
        materialize_localization_profile,
        resolve_localization_profile,
    )

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )).scalar_one_or_none()
    if row is None:
        raise ValueError(
            "Current Settings are unavailable for this workspace. Open Settings, "
            "save the AI/TTS configuration, then adopt it."
        )

    profile = resolve_localization_profile(row, _requested_profile_id(run))
    return materialize_localization_profile(
        row,
        profile,
        target_language=str(run.request.get("target_language") or "Vietnamese"),
        for_dub=_is_dub_run(run),
    )


async def _describe_provider_binding(run: LocalizationRun, *, user_id: str = "local") -> dict:
    """Non-mutating drift verdict for translation/rewrite/TTS binding."""
    is_dub = _is_dub_run(run)
    job_identity = _binding_identity(run.effective_profile)

    current_profile = None
    current_identity = None
    error = None
    try:
        current_profile = await _load_current_profile_for_binding(run, user_id=user_id)
        current_identity = _binding_identity(current_profile)
    except Exception as exc:
        error = str(exc)[:200]

    if current_identity is None:
        diff = {"changed_stages": [], "from_stage": None}
    else:
        diff = _binding_diff(run.effective_profile, current_profile, is_dub=is_dub)

    def _side(identity: dict | None) -> dict | None:
        if identity is None:
            return None
        return {
            "translation_routes": identity["translation_routes"],
            "rewrite_routes": identity["rewrite_routes"] if is_dub else None,
            "tts": identity["tts"] if is_dub else None,
            "asr": identity["asr"],
        }

    return {
        "job": _side(job_identity),
        "current": _side(current_identity),
        "changed_stages": diff["changed_stages"],
        "from_stage": diff["from_stage"],
        "binding_mismatch": bool(diff["changed_stages"]),
        "current_error": error,
    }


def _binding_profile_updates(current: Any, *, is_dub: bool) -> dict:
    """Copy ONLY binding fields off the current profile.

    Policy — presets, instructions, guardrails, glossary, timing, audio and
    render settings — is what the job was created with and is never adopted
    from Settings; a provider rebind must not silently change how the job is
    written or rendered.
    """
    if hasattr(current, "model_dump"):
        current = current.model_dump()
    keys = list(_ASR_BINDING_KEYS) + list(_TRANSLATION_BINDING_KEYS)
    if is_dub:
        keys += list(_REWRITE_BINDING_KEYS) + list(_TTS_BINDING_KEYS)
    return {key: current.get(key) for key in keys}


async def _job_user_id(job_id: str) -> str:
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job

    async with AsyncSessionLocal() as db:
        user_id = (await db.execute(
            select(Job.user_id).where(Job.id == job_id)
        )).scalar_one_or_none()
    return str(user_id or "local")


@router.get("/jobs/{job_id}/source")
async def localization_job_source(job_id: str, request: Request):
    """Stream the immutable source snapshot bound to a localization job."""
    run = _load_run(job_id)
    path = run.source_path.resolve()
    root = (settings.STORAGE_ROOT / "localization_jobs" / job_id).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="Persisted localization source is unavailable")
    from backend.api.videos import serve_media_with_range
    return serve_media_with_range(path, request, filename=path.name)


@router.get("/jobs/{job_id}")
async def localization_job_detail(job_id: str):
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job

    async with AsyncSessionLocal() as db:
        job = (await db.execute(
            select(Job).where(Job.id == job_id)
        )).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Localization job not found")

    try:
        job_output = json.loads(job.output_json or "{}")
    except (TypeError, json.JSONDecodeError):
        job_output = {}
    if not isinstance(job_output, dict):
        job_output = {}

    try:
        run = LocalizationRun.load(job_id)
    except ValueError:
        # A runner can fail before it creates a manifest (profile resolution,
        # source materialization, etc.). The canonical Job row still owns
        # lifecycle state, so return it instead of making Studio believe the
        # job is perpetually loading/active.
        return {
            "manifest": None,
            "transcript": None,
            "plan": [],
            "chunks": [],
            "qc": None,
            "translation_review_pending": False,
            "translation_review_revision": None,
            "qc_review_revision": None,
            "job_status": job.status,
            "job_current_step": job.current_step,
            "job_error_message": job.error_message,
            "generated_video_id": job_output.get("generated_video_id"),
            "publish_ready": bool(job_output.get("publish_ready")),
            "publish_registration_error": job_output.get("publish_registration_error"),
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    detail = _job_detail(run)
    detail["tts_binding"] = await _describe_tts_bindings(run)
    detail["provider_binding"] = await _describe_provider_binding(
        run, user_id=str(job.user_id or "local"),
    )
    detail["job_status"] = job.status
    detail["job_current_step"] = job.current_step
    detail["job_error_message"] = job.error_message
    detail["generated_video_id"] = job_output.get("generated_video_id")
    detail["publish_ready"] = bool(job_output.get("publish_ready"))
    detail["publish_registration_error"] = job_output.get("publish_registration_error")
    return detail


@router.get("/exceptions")
async def localization_exceptions(limit: int = 50):
    from sqlalchemy import select
    from backend.database import AsyncSessionLocal
    from backend.models.job import Job

    root = settings.STORAGE_ROOT / "localization_jobs"
    if not root.exists():
        return {"total": 0, "items": []}
    candidates = []
    for manifest in root.glob("*/manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            if int(data.get("version") or 0) != MANIFEST_VERSION:
                continue
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        job_id = data.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            continue
        stages = data.get("stages") or {}
        if not isinstance(stages, dict):
            continue
        qc = stages.get("qc") or {}
        if not isinstance(qc, dict):
            qc = {}
        qc_metadata = qc.get("metadata") or {}
        qc_status = qc_metadata.get("status") if isinstance(qc_metadata, dict) else None
        request = data.get("request") or {}
        qc_human_approved = bool(
            isinstance(request, dict) and request.get("qc_human_approved")
        )
        failed = [
            {"stage": name, "error": rec.get("error")}
            for name, rec in stages.items()
            if isinstance(rec, dict) and rec.get("status") == "failed"
        ]
        needs_qc_action = (
            qc_status == "FAILED"
            or (qc_status == "NEEDS_REVIEW" and not qc_human_approved)
        )
        if needs_qc_action or failed:
            candidates.append((job_id, data, qc_status, failed))

    if not candidates:
        return {"total": 0, "items": []}

    candidate_ids = {job_id for job_id, *_rest in candidates}
    async with AsyncSessionLocal() as db:
        existing_job_ids = set((await db.execute(
            select(Job.id).where(Job.id.in_(candidate_ids))
        )).scalars().all())

    items = []
    for job_id, data, qc_status, failed in candidates:
        if job_id not in existing_job_ids:
            continue
        qc_report = None
        try:
            qc_report = _optional_json(LocalizationRun.load(job_id), "qc", "report")
        except (ValueError, RuntimeError):
            pass
        items.append({
            "job_id": job_id,
            "updated_at": data.get("updated_at"),
            "qc_status": qc_status,
            "failed_stages": failed,
            "qc_failures": (qc_report or {}).get("failures") or [],
            "qc_warnings": (qc_report or {}).get("warnings") or [],
            "request": data.get("request") or {},
        })
    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    items = items[: max(1, min(limit, 200))]
    return {"total": len(items), "items": items}


@router.post("/jobs/{job_id}/qc-approval")
async def set_localization_qc_approval(job_id: str, body: QCApprovalRequest):
    """Acknowledge warning-only QC and synchronize Publish eligibility."""
    claim = await _claim_job_mutation(job_id, allowed_statuses={"success", "failed"})
    original_request = None
    original_output_json = None
    try:
        run = _load_run(job_id)
        current_revision = _qc_review_revision(run)
        if not current_revision or body.revision != current_revision:
            raise HTTPException(
                status_code=409,
                detail="QC đã thay đổi kể từ lúc bạn mở màn hình. Hãy tải lại và duyệt đúng phiên bản mới.",
            )
        original_request = dict(run.request)
        qc = _optional_json(run, "qc", "report")
        if not isinstance(qc, dict):
            raise HTTPException(status_code=409, detail="QC chưa hoàn tất.")
        qc_status = str(qc.get("status") or "").upper()
        if body.approved:
            if qc_status == "FAILED":
                raise HTTPException(
                    status_code=409,
                    detail="QC đang ở trạng thái Thất bại; cần sửa/chạy lại thay vì bỏ qua.",
                )
            if qc_status != "NEEDS_REVIEW":
                raise HTTPException(
                    status_code=409,
                    detail="QC không ở trạng thái cần duyệt.",
                )

        run.update_request({
            **run.request,
            "qc_human_approved": bool(body.approved),
        })

        from sqlalchemy import select
        from backend.database import AsyncSessionLocal
        from backend.models.job import Job
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                select(Job).where(Job.id == job_id)
            )).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Job row not found")
            original_output_json = row.output_json
            try:
                out = json.loads(row.output_json or "{}")
            except (TypeError, json.JSONDecodeError):
                out = {}
            if not isinstance(out, dict):
                out = {}
            out["qc_human_approved"] = bool(body.approved)
            out["publish_ready"] = False
            row.output_json = json.dumps(out)
            await db.commit()

        from backend.services.localization_publish_service import (
            set_localization_publish_eligibility,
        )
        try:
            publish = await set_localization_publish_eligibility(
                job_id,
                eligible=bool(body.approved),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        result = {
            "ok": True,
            "job_id": job_id,
            "qc_status": qc_status,
            "qc_human_approved": bool(body.approved),
            "generated_video_id": publish.get("generated_video_id"),
            "publish_ready": bool(publish.get("publish_ready")),
        }
    except Exception:
        # This endpoint changes both manifest and Job output. Put both back if
        # the Publish handoff or any other step fails before releasing the claim.
        try:
            if original_request is not None:
                _load_run(job_id).update_request(original_request)
            if original_output_json is not None:
                from backend.database import AsyncSessionLocal
                from backend.models.job import Job
                async with AsyncSessionLocal() as db:
                    row = await db.get(Job, job_id)
                    if row is not None:
                        row.output_json = original_output_json
                        await db.commit()
        finally:
            await _restore_job_mutation(job_id, claim)
        raise

    await _restore_job_mutation(job_id, claim)
    return result


@router.post("/jobs/{job_id}/publish-registration")
async def retry_localization_publish_registration(job_id: str):
    """Retry only the localization -> GeneratedVideo handoff, never media work."""
    claim = await _claim_job_mutation(
        job_id, allowed_statuses={"success", "failed"},
    )
    try:
        from backend.services.localization_publish_service import (
            promote_localization_output,
        )
        publish = await promote_localization_output(job_id)
        if not publish.get("publish_ready"):
            raise HTTPException(
                status_code=409,
                detail="Đầu ra đã được giữ trong Thư viện nhưng chưa đạt điều kiện Xuất bản; hãy xử lý QC trước.",
            )
        result = {
            "ok": True,
            "job_id": job_id,
            "generated_video_id": publish.get("generated_video_id"),
            "publish_ready": True,
        }
    except Exception:
        await _restore_job_mutation(job_id, claim)
        raise

    await _restore_job_mutation(job_id, claim)
    return result

@router.post("/jobs/{job_id}/override")
async def localization_expert_override(job_id: str, body: ExpertOverrideRequest):
    claim = await _claim_job_mutation(job_id)
    try:
        # Reload only AFTER winning the claim. A losing request may have started
        # from an older manifest snapshot; using it here would overwrite the
        # winner's edits even though the DB race itself was prevented.
        run = _load_run(job_id)
        _verify_run_source_identity(run)
        invalidations: list[str] = []

        if body.profile_updates:
            merged = {**run.effective_profile, **body.profile_updates}
            try:
                profile = LocalizationProfile.model_validate(merged)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid profile override: {exc}",
                ) from exc
            stage = _profile_invalidation(set(body.profile_updates))
            if body.segments and stage in {"separation", "asr", "semantic", "translation"}:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Cannot combine upstream profile changes with segment overrides "
                        "because the segment IDs may be regenerated. Apply the profile "
                        "change and rerun first, then edit the resulting segments."
                    ),
                )
            run.update_effective_profile(profile.model_dump())
            if stage:
                invalidations.append(stage)

        if body.segments:
            current_revision = _review_revision(run, "translation", "transcript")
            if (
                not current_revision
                or not body.revision
                or body.revision != current_revision
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Bản dịch đã thay đổi kể từ lúc bạn mở màn hình. Hãy tải lại trước khi chỉnh sửa.",
                )
            raw = _optional_json(run, "translation", "transcript")
            if not raw:
                raise HTTPException(
                    status_code=409,
                    detail="Translation artifact is unavailable; rerun through translation first",
                )
            transcript = LocalizationTranscript.model_validate(raw)
            by_id = {seg.id: seg for seg in transcript.segments}

            for change in body.segments:
                seg = by_id.get(change.id)
                if seg is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Segment not found: {change.id}",
                    )

                if change.translated_text is not None:
                    value = change.translated_text.strip()
                    if not value:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Empty translation: {change.id}",
                        )
                    seg.translated_text = value
                    seg.translation_status = "success"
                    seg.qc_status = "pending"

                if change.speaker_id is not None:
                    seg.speaker_id = change.speaker_id.strip() or None

                overrides = dict(seg.overrides or {})
                if change.clear_timing:
                    overrides.pop("target_start_ms", None)
                    overrides.pop("target_end_ms", None)
                elif change.target_start_ms is not None or change.target_end_ms is not None:
                    if change.target_start_ms is None or change.target_end_ms is None:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Both target_start_ms and target_end_ms are required: {change.id}",
                        )
                    if change.target_end_ms <= change.target_start_ms:
                        raise HTTPException(
                            status_code=400,
                            detail=f"target_end_ms must be > target_start_ms: {change.id}",
                        )
                    overrides["target_start_ms"] = change.target_start_ms
                    overrides["target_end_ms"] = change.target_end_ms

                if change.clear_voice:
                    overrides.pop("voice_id", None)
                elif change.voice_id is not None:
                    voice = change.voice_id.strip()
                    if voice:
                        overrides["voice_id"] = voice

                seg.overrides = overrides

            path = run.save_json(
                "translation", "transcript.json", transcript.model_dump()
            )
            old_meta = dict(run.record("translation").get("metadata") or {})
            old_meta["expert_override"] = True
            run.complete(
                "translation",
                artifacts={"transcript": path},
                metadata=old_meta,
            )
            invalidations.append("dubbing")

        if invalidations:
            stage = _earliest_stage(invalidations)
            next_request = {
                **run.request,
                "qc_human_approved": False,
            }
            if STAGES.index(stage) <= STAGES.index("translation"):
                next_request.update({
                    "translation_review_pending": False,
                    "translation_review_approved": False,
                })
            if STAGES.index(stage) <= STAGES.index("dubbing"):
                # Deleted raw chunks cannot satisfy an old targeted retry.
                next_request.pop("regenerate_chunk_ids", None)
            run.update_request(next_request)
            run.invalidate_from(stage, delete_artifacts=True)
        else:
            stage = None

        result = {
            "ok": True,
            "invalidated_from": stage,
            "effective_profile": run.effective_profile,
        }
    except Exception:
        await _restore_job_mutation(job_id, claim)
        raise

    await _restore_job_mutation(job_id, claim)
    return result

@router.post("/jobs/{job_id}/continue-after-translation")
async def continue_after_translation_review(job_id: str, body: ReviewApprovalRequest):
    """Approve persisted C6 translation and resume the SAME run at C7."""
    claim = await _claim_job_mutation(
        job_id,
        allowed_statuses={"awaiting_review", "success", "failed"},
    )
    dispatched = False
    original_request = None
    try:
        run = _load_run(job_id)
        current_revision = _review_revision(run, "translation", "transcript")
        if not current_revision or body.revision != current_revision:
            raise HTTPException(
                status_code=409,
                detail="Bản dịch đã thay đổi kể từ lúc bạn mở màn hình. Hãy tải lại trước khi duyệt.",
            )
        if not run.is_complete("translation", ("transcript",)):
            raise HTTPException(
                status_code=409,
                detail="Translation is not complete; there is nothing to approve yet.",
            )
        _verify_run_source_identity(run)
        req = run.request
        original_request = dict(req)
        if not req.get("review_translation"):
            raise HTTPException(
                status_code=409,
                detail="This localization job was not configured for translation review.",
            )
        if not req.get("translation_review_pending") or req.get("translation_review_approved"):
            raise HTTPException(
                status_code=409,
                detail="Bản dịch này không còn ở trạng thái chờ duyệt.",
            )

        run.update_request({
            **req,
            "translation_review_approved": True,
            "translation_review_pending": False,
        })
        await _dispatch_existing_run(run, already_claimed=True)
        dispatched = True
        return {"ok": True, "job_id": job_id, "from_stage": "dubbing"}
    except Exception:
        if original_request is not None and not dispatched:
            try:
                _load_run(job_id).update_request(original_request)
            except Exception:
                pass
        if not dispatched:
            await _restore_job_mutation(job_id, claim)
        raise

@router.post("/jobs/{job_id}/chunks/{chunk_id}/regenerate")
async def regenerate_localization_chunk(job_id: str, chunk_id: int):
    claim = await _claim_job_mutation(job_id)
    dispatched = False
    try:
        run = _load_run(job_id)
        _verify_run_source_identity(run)
        chunks = _optional_json(run, "dubbing", "chunks") or []
        ids = {
            int(row.get("id"))
            for row in chunks
            if isinstance(row, dict) and row.get("id") is not None
        }
        if chunk_id not in ids:
            raise HTTPException(status_code=404, detail="Dubbing chunk not found")

        run.update_request({
            **run.request,
            "regenerate_chunk_ids": [chunk_id],
            "qc_human_approved": False,
        })
        # Keep raw chunk files so every non-target chunk can be reused.
        run.invalidate_from("dubbing", delete_artifacts=False)
        await _dispatch_existing_run(run, already_claimed=True)
        dispatched = True
        return {"ok": True, "job_id": job_id, "regenerating_chunk": chunk_id}
    except Exception:
        if not dispatched:
            await _restore_job_mutation(job_id, claim)
        raise

@router.post("/jobs/{job_id}/rebind-tts")
async def rebind_localization_tts(job_id: str, body: TTSRebindRequest):
    """Adopt current Settings TTS and rerun only dubbing plus downstream stages."""
    claim = await _claim_job_mutation(job_id)
    dispatched = False
    try:
        run = _load_run(job_id)
        _verify_run_source_identity(run)
        if str(run.request.get("mode") or "full_dub").strip().lower() != "full_dub":
            raise HTTPException(
                status_code=409,
                detail="This localization job does not use TTS dubbing.",
            )

        binding = await _resolve_current_tts_binding(
            run.effective_profile,
            str(run.request.get("target_language") or "Vietnamese"),
            voice_id=body.voice_id,
            model=body.model,
        )
        previous = {
            key: run.effective_profile.get(key)
            for key in (
                "tts_connection_id", "tts_provider", "tts_model",
                "tts_voice", "tts_base_url", "tts_region",
            )
        }
        resets = _provider_scoped_tts_resets(run.effective_profile, binding)
        run.update_effective_profile({
            **run.effective_profile,
            **binding,
            **resets,
        })
        run.update_request({
            **run.request,
            "tts_binding_mode": "inherit",
            # The dispatcher forwards this manual override ahead of the
            # effective profile; don't let the previous voice undo the rebind.
            "voice_id": binding.get("tts_voice") or "",
            # Rebinding deletes every raw chunk, so a previous targeted retry
            # must not demand reusable non-target artifacts that no longer exist.
            "regenerate_chunk_ids": [],
            "qc_human_approved": False,
        })
        run.invalidate_from("dubbing", delete_artifacts=True)

        review_pending = bool(
            run.request.get("translation_review_pending")
            and not run.request.get("translation_review_approved")
        )
        if review_pending:
            # Rebinding while C6 is paused is configuration-only. Release the
            # claim back to awaiting_review instead of inventing an active job.
            await _restore_job_mutation(job_id, claim)
        else:
            await _dispatch_existing_run(run, already_claimed=True)
            dispatched = True

        return {
            "ok": True,
            "job_id": job_id,
            "from_stage": "dubbing",
            "awaiting_translation_review": review_pending,
            "previous_tts": previous,
            "current_tts": binding,
            "cleared_profile_fields": sorted(resets),
        }
    except Exception:
        if not dispatched:
            # If review_pending already restored the claim this is a no-op CAS;
            # only try when the row is still our pending claim.
            from backend.agents.job_helper import restore_job_restart_claim
            await restore_job_restart_claim(job_id, claim)
        raise

@router.post("/jobs/{job_id}/rebind-providers")
async def rebind_localization_providers(job_id: str, body: ProviderRebindRequest):
    """Adopt the CURRENT Settings provider binding and rerun only affected stages.

    This is the single explicit adoption path. The default rerun keeps the job's
    own snapshot; nothing here is triggered implicitly, and a job whose binding
    still matches is left completely untouched.
    """
    claim = await _claim_job_mutation(job_id)
    dispatched = False
    transaction: ManifestTransaction | None = None
    try:
        run = _load_run(job_id)
        _verify_run_source_identity(run)
        is_dub = _is_dub_run(run)

        # Resolve the CURRENT side first. Missing Settings/profile must fail
        # before a single byte of the job is mutated.
        try:
            current_profile = await _load_current_profile_for_binding(
                run, user_id=await _job_user_id(job_id),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Could not resolve the current provider configuration: {exc}",
            ) from exc

        diff = _binding_diff(run.effective_profile, current_profile, is_dub=is_dub)
        changed_stages = diff["changed_stages"]
        from_stage = diff["from_stage"]

        if not changed_stages:
            # Nothing to adopt. Release the claim and report the no-op rather
            # than rerunning a job that already matches Settings.
            await _restore_job_mutation(job_id, claim)
            return {
                "ok": True,
                "job_id": job_id,
                "from_stage": None,
                "changed_stages": [],
                "awaiting_translation_review": bool(
                    run.request.get("translation_review_pending")
                    and not run.request.get("translation_review_approved")
                ),
                "cleared_profile_fields": [],
            }

        if "tts" in changed_stages:
            # Validate the exact profile-selected binding, not the global TTS
            # default. No transaction, manifest write or deletion precedes this.
            await _resolve_current_tts_binding(
                current_profile.model_dump(),
                str(run.request.get("target_language") or "Vietnamese"),
                preserve_binding=True,
                user_id=await _job_user_id(job_id),
            )

        updates = _binding_profile_updates(current_profile, is_dub=is_dub)
        resets = _provider_scoped_tts_resets(run.effective_profile, updates) if is_dub else {}
        request_updates = {
            **run.request,
            "tts_binding_mode": "inherit" if is_dub else run.request.get("tts_binding_mode"),
            "regenerate_chunk_ids": [],
            "qc_human_approved": False,
        }
        if is_dub:
            # The dispatcher forwards this manual override ahead of the
            # effective profile; don't let the previous voice undo the rebind.
            request_updates["voice_id"] = updates.get("tts_voice") or ""
        if STAGES.index(from_stage) <= STAGES.index("translation"):
            # Translation is about to be recomputed, so a previous approval
            # cannot describe the new artifact.
            request_updates["translation_review_pending"] = False
            request_updates["translation_review_approved"] = False

        transaction = ManifestTransaction(
            run, STAGES[STAGES.index(from_stage):],
        )
        transaction.begin()
        run.update_effective_profile({
            **run.effective_profile,
            **updates,
            **resets,
        })
        run.update_request(request_updates)
        # Canonical invalidation — there is no second invalidation engine.
        run.invalidate_from(from_stage, delete_artifacts=True)
        # invalidate_from is intentionally best-effort for ordinary reruns.
        # Rebinding must not let old provider chunk caches survive silently.
        if any((run.root / stage).exists() for stage in transaction.stage_names):
            raise OSError("Provider artifacts could not be removed completely")

        review_pending = bool(
            request_updates.get("translation_review_pending")
            and not request_updates.get("translation_review_approved")
        )
        if review_pending:
            # Rebinding while C6 is paused is configuration-only. Release the
            # claim back to awaiting_review instead of inventing an active job.
            await _restore_job_mutation(job_id, claim)
        else:
            await _dispatch_existing_run(run, already_claimed=True)
            dispatched = True
        transaction.commit()
        transaction = None

        return {
            "ok": True,
            "job_id": job_id,
            "from_stage": from_stage,
            "changed_stages": changed_stages,
            "awaiting_translation_review": review_pending,
            "cleared_profile_fields": sorted(resets),
        }
    except (Exception, asyncio.CancelledError) as exc:
        if transaction is not None:
            try:
                transaction.rollback()
            except Exception as restore_error:
                # Do not release a runnable claim onto half-restored state.
                # The backup is retained for recovery after the disk is usable.
                raise HTTPException(
                    status_code=503,
                    detail="Không thể khôi phục rebind do lỗi lưu trữ. Bản sao cũ còn trong "
                    ".rollback của job; kiểm tra dung lượng/quyền ghi và khôi phục trước khi chạy lại.",
                ) from restore_error
        if not dispatched:
            from backend.agents.job_helper import restore_job_restart_claim
            await restore_job_restart_claim(job_id, claim)
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        raise HTTPException(
            status_code=503,
            detail="Không thể áp dụng cấu hình hiện tại. Cấu hình và artifact cũ đã được giữ/khôi phục. "
            "Kiểm tra dung lượng, quyền ghi, tệp đang bị khóa và trạng thái backend rồi thử lại.",
        ) from exc


@router.post("/jobs/{job_id}/rerun")
async def rerun_localization_job(job_id: str, body: RerunRequest):
    """Rerun the SAME persisted job binding from the exact failed stage."""
    claim = await _claim_job_mutation(job_id)
    dispatched = False
    try:
        run = _load_run(job_id)
        smart_retry = body.from_stage is None
        stage = body.from_stage or run.first_incomplete_stage() or "qc"
        if stage != "separation":
            _verify_run_source_identity(run)
        review_pending = bool(
            run.request.get("translation_review_pending")
            and not run.request.get("translation_review_approved")
        )
        if review_pending and STAGES.index(stage) > STAGES.index("translation"):
            raise HTTPException(
                status_code=409,
                detail="Bản dịch đang chờ duyệt. Hãy duyệt/tiếp tục C6 trước khi chạy lại bước phía sau.",
            )

        rerun_request = {
            **run.request,
            "qc_human_approved": False,
        }
        if (
            run.request.get("review_translation")
            and STAGES.index(stage) <= STAGES.index("translation")
        ):
            rerun_request.update({
                "translation_review_pending": False,
                "translation_review_approved": False,
            })
        run.update_request(rerun_request)

        preserve_files = (
            {"checkpoint.json"}
            if smart_retry and stage == "translation"
            else set()
        )
        delete_artifacts = True

        if smart_retry and stage == "dubbing":
            meta = dict(run.record("dubbing").get("metadata") or {})
            failed_chunk_ids = sorted({
                int(item.get("chunk_id"))
                for item in (meta.get("issues") or [])
                if isinstance(item, dict)
                and item.get("reason") == "timing_overflow"
                and item.get("chunk_id") is not None
            })
            if failed_chunk_ids:
                run.update_request({
                    **run.request,
                    "regenerate_chunk_ids": failed_chunk_ids,
                })
                delete_artifacts = False
        else:
            # Artifacts of this stage are about to be deleted, so a targeted
            # retry recorded by an earlier attempt can no longer be honored;
            # carrying it forward would demand reusable chunks that are gone.
            rerun_request.pop("regenerate_chunk_ids", None)
            run.update_request(rerun_request)

        run.invalidate_from(
            stage,
            delete_artifacts=delete_artifacts,
            preserve_files=preserve_files,
        )
        if stage == "separation":
            run.refresh_source_identity()

        await _dispatch_existing_run(run, already_claimed=True)
        dispatched = True
        return {
            "ok": True,
            "job_id": job_id,
            "from_stage": stage,
            "smart_retry": smart_retry,
            "preserved_upstream": list(STAGES[:STAGES.index(stage)]),
            "targeted_chunk_ids": run.request.get("regenerate_chunk_ids", [])
                if smart_retry and stage == "dubbing" else [],
        }
    except Exception:
        if not dispatched:
            await _restore_job_mutation(job_id, claim)
        raise

@router.post("/jobs/{job_id}/auto-repair")
async def set_localization_auto_repair(job_id: str, body: AutoRepairRequest):
    """Toggle bounded automatic repair for an inactive persisted job."""
    claim = await _claim_job_mutation(job_id)
    try:
        run = _load_run(job_id)
        request = {
            **run.request,
            "auto_repair": bool(body.enabled),
        }
        if not body.enabled:
            request.pop("last_auto_repair", None)
        run.update_request(request)
        result = {
            "ok": True,
            "job_id": job_id,
            "auto_repair": bool(body.enabled),
            "attempts": dict(run.request.get("auto_repair_attempts") or {}),
        }
    except Exception:
        await _restore_job_mutation(job_id, claim)
        raise

    await _restore_job_mutation(job_id, claim)
    return result

def _assert_rerender_ready(run: LocalizationRun) -> None:
    """Fail closed unless the canonical runner can skip every upstream stage."""
    request = run.request
    if not run.is_complete("translation", ("transcript",)):
        raise HTTPException(
            status_code=409,
            detail="Không thể kết xuất lại vì bản dịch chưa hoàn tất.",
        )
    if request.get("translation_review_pending"):
        raise HTTPException(
            status_code=409,
            detail="Không thể kết xuất lại vì đang chờ duyệt bản dịch.",
        )

    mode = str(request.get("mode") or "full_dub").strip().lower()
    if mode == "full_dub":
        if not run.is_complete("dubbing", ("transcript", "dub_track", "plan", "chunks")):
            raise HTTPException(
                status_code=409,
                detail="Không thể kết xuất lại vì phần lồng tiếng chưa hoàn tất.",
            )
        if not run.is_complete("audio_mix", ("mixed_audio",)):
            raise HTTPException(
                status_code=409,
                detail="Không thể kết xuất lại vì phần trộn âm thanh chưa hoàn tất.",
            )
        return

    if mode == "captions_only":
        if not run.is_complete("dubbing") or not run.is_complete("audio_mix"):
            raise HTTPException(
                status_code=409,
                detail="Không thể kết xuất lại vì trạng thái âm thanh/phụ đề chưa hoàn tất.",
            )
        return

    raise HTTPException(status_code=409, detail="Chế độ bản địa hóa không hỗ trợ kết xuất lại.")


@router.post("/jobs/{job_id}/rerender")
async def rerender_localization_job(job_id: str, body: RenderRerunRequest):
    # Validate request-local geometry before claiming the job; these checks do
    # not read or mutate persisted state and should not hold the recovery lock.
    provided = body.model_fields_set
    explicit_mask = body.mask_box if "mask_box" in provided else None
    if explicit_mask is not None:
        mask_mode = str(explicit_mask.get("mode") or "").strip().lower()
        if mask_mode not in {"blur", "solid"}:
            raise HTTPException(status_code=400, detail="Mask mode must be blur or solid")
        if explicit_mask.get("normalized") is not True:
            raise HTTPException(
                status_code=400,
                detail="Mask box must explicitly set normalized=true",
            )
        from backend.services.caption_service import parse_and_validate_mask_box

        if parse_and_validate_mask_box(explicit_mask, 1000, 1000) is None:
            raise HTTPException(status_code=400, detail="Mask box geometry is invalid")

    claim = await _claim_job_mutation(job_id)
    dispatched = False
    try:
        # Re-read every persisted field only after the CAS claim. This closes
        # the stale-snapshot race between two rapid rerender/rebind/rerun calls.
        run = _load_run(job_id)
        _verify_run_source_identity(run)
        _assert_rerender_ready(run)

        mask_box = (
            body.mask_box
            if "mask_box" in provided
            else run.request.get("mask_box")
        )
        if mask_box is not None and "mask_box" not in provided:
            if mask_box.get("normalized") is not True:
                raise HTTPException(
                    status_code=400,
                    detail="Mask box must explicitly set normalized=true",
                )
            from backend.services.caption_service import parse_and_validate_mask_box

            if parse_and_validate_mask_box(mask_box, 1000, 1000) is None:
                raise HTTPException(
                    status_code=400,
                    detail="Mask box geometry is invalid",
                )

        raw_placement = (
            body.caption_placement
            if "caption_placement" in provided
            else run.request.get("caption_placement")
        )
        placement = dict(raw_placement or {})
        mode = str(placement.get("mode") or "default").strip().lower()
        if mode not in {"default", "mask_region"}:
            raise HTTPException(
                status_code=400,
                detail="Caption placement mode is invalid",
            )
        try:
            vertical_offset = float(placement.get("vertical_offset", 0.0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="Caption vertical offset must be numeric",
            ) from exc
        if not -0.25 <= vertical_offset <= 0.25:
            raise HTTPException(
                status_code=400,
                detail="Caption vertical offset must be between -0.25 and 0.25",
            )
        if mode == "mask_region" and mask_box is None:
            raise HTTPException(
                status_code=400,
                detail="Mask-region caption placement requires an enabled mask",
            )
        placement = {"mode": mode, "vertical_offset": vertical_offset}

        request = {
            **run.request,
            "qc_human_approved": False,
        }
        if "mask_box" in provided:
            request["mask_box"] = mask_box
        if "caption_placement" in provided:
            request["caption_placement"] = placement
        if "target_aspect" in provided and body.target_aspect:
            request["target_aspect"] = body.target_aspect
        run.update_request(request)

        if "target_aspect" in provided and body.target_aspect:
            run.update_effective_profile({
                **run.effective_profile,
                "target_aspect": body.target_aspect,
            })

        run.invalidate_from(
            "final_render",
            delete_artifacts=True,
            preserve_files=set(),
        )
        await _dispatch_existing_run(run, already_claimed=True)
        dispatched = True
        return {"ok": True, "job_id": job_id, "from_stage": "final_render"}
    except Exception:
        if not dispatched:
            await _restore_job_mutation(job_id, claim)
        raise

async def _run_localization_batch_sequential(specs: list[dict]) -> None:
    """Run HQ localization jobs one-by-one to avoid loading Demucs/WhisperX N times.

    The normal per-job runner is reused verbatim; this is only a lightweight
    coordinator and intentionally bypasses the generic max-3 heavy-task fanout.
    """
    from backend.core.tool_runners import run_tool_translate

    for spec in specs:
        await run_tool_translate(**spec)


@router.post("/batch")
async def localization_batch(
    files: list[UploadFile] = File(...),
    target_language: str = Form("vi"),
    mode: Literal["captions_only", "full_dub"] = Form("full_dub"),
    voice_id: str = Form(""),
    caption_style: str = Form("viral"),
    emoji_style: str = Form("moderate"),
    target_aspect: str | None = Form(None),
    profile_id: str | None = Form(None),
):
    """C9 batch = one proven localization runner applied to N source files."""
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    if len(files) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 videos per batch")

    from backend.agents.job_helper import create_job
    from backend.api.tools import VIDEO_EXTS, VIDEO_MAX_BYTES, save_upload
    from backend.core.task_runner import dispatch

    jobs = []
    specs = []
    for file in files:
        job = await create_job("tool:translate", "local", {
            "target_language": target_language,
            "mode": mode,
            "voice_id": voice_id if mode == "full_dub" else None,
            "caption_style": caption_style,
            "target_aspect": target_aspect,
            "profile_id": profile_id,
            "batch": True,
            "filename": file.filename,
        })
        in_path = await save_upload(file, job.id, VIDEO_EXTS, VIDEO_MAX_BYTES)
        specs.append({
            "job_id": job.id,
            "in_path": in_path,
            "target_language": target_language,
            "mode": mode,
            "voice_id": voice_id,
            "caption_style": caption_style,
            "emoji_style": emoji_style,
            "target_aspect": target_aspect,
            "profile_id": profile_id,
        })
        jobs.append({"job_id": job.id, "filename": file.filename})

    # One coordinator consumes one global heavy-task slot; jobs inside it are
    # strictly sequential, so a 20-video batch cannot fan out 20 Demucs models.
    dispatch(_run_localization_batch_sequential(specs))
    return {"count": len(jobs), "jobs": jobs}
