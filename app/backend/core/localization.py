# SPDX-License-Identifier: AGPL-3.0-only
"""Canonical Phase-C localization configuration and timeline contracts.

Keep this module boring: validated data shapes only. Runtime orchestration stays
in the existing job/tool pipeline.
"""
from __future__ import annotations

import json
import math
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

_PROFILE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
MAX_LOCALIZATION_PROFILES = 20

DEFAULT_TRANSLATION_GUARDRAILS = """- Translate only what the source says; fidelity is more important than fluency.
- Preserve intent, entities, relationships, quantities, units, steps, negation and factual claims.
- Do not infer missing context or invent commentary, CTAs, claims, prices, names, examples or benefits.
- If a phrase is ambiguous, choose the least-assumptive faithful rendering.
- Apply glossary terms consistently."""

DEFAULT_REWRITE_GUARDRAILS = """- Preserve intent, entities, relationships, quantities, units, negation, steps, settings, product features, comparisons, causal meaning and factual claims.
- Never change a number, unit, model/brand/name, positive/negative meaning or who/what a claim refers to merely to fit duration.
- Remove filler, repetition and redundant connective phrases before removing factual content.
- Do not invent CTAs, claims, examples, prices or benefits.
- If essential facts cannot fit, preserve them rather than inventing, deleting or reversing facts."""


class AIStageRoute(BaseModel):
    """One ordered AI route used by a localization stage.

    connection_id is stable user-owned identity. Provider/base/key live in the
    Settings connection registry, so multiple compatible endpoints/accounts may
    coexist without overloading the provider name as identity.
    """
    connection_id: str
    model: str | None = None
    base_url: str | None = None

    @field_validator("connection_id")
    @classmethod
    def validate_connection_id(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 96:
            raise ValueError("connection_id must be 1-96 characters")
        return value


class LocalizationProfile(BaseModel):
    id: str
    name: str

    download_quality: Literal["best", "2160p", "1440p", "1080p", "720p", "480p"] = "best"
    asr_provider: Literal["faster_whisper"] = "faster_whisper"
    asr_model: str | None = None
    asr_quality: Literal["fast", "balanced", "accurate", "best"] = "best"
    alignment_mode: Literal["off", "whisperx"] = "whisperx"
    diarization_mode: Literal["off", "auto", "whisperx"] = "auto"
    diarization_speakers: int | None = Field(default=None, ge=1, le=16)

    # None = inherit the global AI/TTS setting. This avoids duplicating keys or
    # provider connection data inside every profile.
    translation_provider: str | None = None
    translation_model: str | None = None
    translation_base_url: str | None = None
    translation_fallback_provider: str | None = None
    translation_fallback_model: str | None = None
    translation_fallback_base_url: str | None = None
    translation_routes: list[AIStageRoute] = Field(default_factory=list, max_length=8)
    translation_retry_attempts: int = Field(default=3, ge=1, le=5)
    translation_attempt_timeout_s: int = Field(default=75, ge=10, le=300)
    translation_total_timeout_s: int = Field(default=180, ge=20, le=900)
    translation_preset: Literal[
        "natural_vietnamese", "affiliate_conversational", "literal", "custom"
    ] = "natural_vietnamese"
    translation_instructions: str = ""
    # Semantic fidelity policy is editable per profile from Settings. Structural
    # protocol (stable IDs/order + JSON schema) stays app-owned because runtime
    # parsing depends on it.
    translation_guardrails: str = DEFAULT_TRANSLATION_GUARDRAILS
    glossary: dict[str, str] = Field(default_factory=dict)

    # C6 translation and C7 duration rewrite are different jobs. Translation
    # may pin a larger/high-quality model while rewrite can inherit a smaller
    # low-latency model from Settings (or pin its own model) without coupling
    # the two stages.
    rewrite_provider: str | None = None
    rewrite_model: str | None = None
    rewrite_base_url: str | None = None
    rewrite_fallback_provider: str | None = None
    rewrite_fallback_model: str | None = None
    rewrite_fallback_base_url: str | None = None
    rewrite_routes: list[AIStageRoute] = Field(default_factory=list, max_length=8)
    rewrite_retry_attempts: int = Field(default=3, ge=1, le=4)
    rewrite_attempt_timeout_s: int = Field(default=45, ge=10, le=180)
    rewrite_total_timeout_s: int = Field(default=90, ge=20, le=360)
    rewrite_guardrails: str = DEFAULT_REWRITE_GUARDRAILS
    # Appended after the editable semantic guardrails. Users can tune
    # compression/style per profile while the stable-ID / structured-output
    # protocol remains application-owned.
    rewrite_instructions: str = ""

    tts_connection_id: str | None = None
    tts_provider: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None
    tts_base_url: str | None = None
    tts_region: str | None = None
    speaker_voices: dict[str, str] = Field(default_factory=dict)
    speaker_reference_audio: dict[str, str] = Field(default_factory=dict)
    tts_reference_audio: str | None = None
    tts_reference_text: str | None = None
    tts_reference_language: str | None = None
    voice_clone_reference_mode: Literal["manual", "global_auto", "per_speaker_auto"] = "per_speaker_auto"

    # Donor-derived semantic/dubbing policy. These values mirror the proven
    # OpenCreator smart-dubbing defaults unless Affiliate2 has a product reason
    # to differ. Provider-level TTS speed is no longer relied on for fitting;
    # measured chunk audio is fitted after synthesis.
    semantic_segmentation: bool = True
    semantic_ai_retry_attempts: int = Field(default=2, ge=1, le=3)
    semantic_ai_attempt_timeout_s: int = Field(default=25, ge=5, le=120)
    semantic_ai_total_timeout_s: int = Field(default=60, ge=10, le=240)
    dub_min_cue_duration_ms: int = Field(default=2500, ge=250, le=10000)
    dub_max_chunk_size: int = Field(default=5, ge=1, le=12)
    dub_gap_tolerance_ms: int = Field(default=1500, ge=0, le=5000)
    dub_speed_accept: float = Field(default=1.15, ge=1.0, le=2.0)
    dub_speed_max: float = Field(default=1.30, ge=1.0, le=2.0)
    # Emergency hard ceiling, applied ONLY after rewrite, silence borrowing and
    # bounded video retime are exhausted. Between dub_speed_max and this value a
    # chunk is still rendered, but the overflow is persisted and QC is forced to
    # NEEDS_REVIEW so a human must acknowledge the exact revision before
    # Publish. It is never a silent quality downgrade and never a READY result.
    dub_emergency_speed_max: float = Field(default=1.35, ge=1.0, le=1.5)
    dub_rewrite_attempts: int = Field(default=2, ge=0, le=5)

    audio_mode: Literal["hq_clean", "fast_duck"] = "hq_clean"
    video_retime_mode: Literal["off", "fallback"] = "fallback"
    video_retime_max_slowdown: float = Field(default=1.12, ge=1.0, le=1.35)
    preserve_background: bool = True
    background_gain_db: float = Field(default=-6.0, ge=-40.0, le=6.0)
    dub_gain_db: float = Field(default=0.0, ge=-20.0, le=12.0)

    caption_style: str = "viral"
    target_aspect: Literal["source", "9:16", "16:9", "1:1"] = "9:16"
    render_profile: Literal["draft", "balanced", "hq"] = "hq"
    qc_profile: Literal["standard", "strict"] = "strict"

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        value = value.strip()
        if not _PROFILE_ID_RE.fullmatch(value):
            raise ValueError("Profile id must be 1-64 letters, numbers, '_' or '-'.")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value or len(value) > 80:
            raise ValueError("Profile name must be 1-80 characters.")
        return value

    @field_validator("translation_instructions", "rewrite_instructions")
    @classmethod
    def cap_instructions(cls, value: str, info) -> str:
        value = value.strip()
        if len(value) > 8000:
            label = (
                "Rewrite instructions"
                if info.field_name == "rewrite_instructions"
                else "Translation instructions"
            )
            raise ValueError(f"{label} are too long (max 8000 chars).")
        return value

    @field_validator("translation_guardrails", "rewrite_guardrails")
    @classmethod
    def validate_guardrails(cls, value: str, info) -> str:
        value = value.strip()
        label = (
            "Rewrite guardrails"
            if info.field_name == "rewrite_guardrails"
            else "Translation guardrails"
        )
        if not value:
            raise ValueError(f"{label} cannot be empty.")
        if len(value) > 12000:
            raise ValueError(f"{label} are too long (max 12000 chars).")
        return value

    @model_validator(mode="after")
    def validate_dubbing_policy(self):
        if self.dub_speed_accept > self.dub_speed_max:
            raise ValueError("dub_speed_accept must be <= dub_speed_max")
        if self.dub_emergency_speed_max < self.dub_speed_max:
            raise ValueError(
                "dub_emergency_speed_max must be >= dub_speed_max"
            )
        if self.video_retime_max_slowdown < 1.0:
            raise ValueError("video_retime_max_slowdown must be >= 1.0")
        if self.translation_total_timeout_s < self.translation_attempt_timeout_s:
            raise ValueError("translation_total_timeout_s must be >= translation_attempt_timeout_s")
        if self.rewrite_total_timeout_s < self.rewrite_attempt_timeout_s:
            raise ValueError("rewrite_total_timeout_s must be >= rewrite_attempt_timeout_s")
        if self.semantic_ai_total_timeout_s < self.semantic_ai_attempt_timeout_s:
            raise ValueError("semantic_ai_total_timeout_s must be >= semantic_ai_attempt_timeout_s")
        for label, routes in (
            ("translation_routes", self.translation_routes),
            ("rewrite_routes", self.rewrite_routes),
        ):
            seen = set()
            for route in routes:
                identity = (
                    route.connection_id.strip().lower(),
                    (route.model or "").strip(),
                    (route.base_url or "").strip().rstrip("/"),
                )
                if identity in seen:
                    raise ValueError(f"{label} contains a duplicate route: {route.connection_id}")
                seen.add(identity)
        return self

    @field_validator("glossary")
    @classmethod
    def validate_glossary(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 100:
            raise ValueError("Glossary is limited to 100 entries.")
        out: dict[str, str] = {}
        for raw_src, raw_dst in value.items():
            src, dst = str(raw_src).strip(), str(raw_dst).strip()
            if not src or not dst:
                continue
            if len(src) > 120 or len(dst) > 240:
                raise ValueError("Glossary entries are too long.")
            out[src] = dst
        return out


class LocalizationProfileStore(BaseModel):
    default_profile_id: str
    profiles: list[LocalizationProfile]


def default_localization_profile() -> LocalizationProfile:
    return LocalizationProfile(
        id="vi-affiliate-hq",
        name="Vietnam Affiliate HQ",
    )


def default_profile_store() -> LocalizationProfileStore:
    profile = default_localization_profile()
    return LocalizationProfileStore(
        default_profile_id=profile.id,
        profiles=[profile],
    )


def load_profile_store(raw: str | None) -> LocalizationProfileStore:
    """Parse persisted JSON; corrupt/old data falls back to one safe profile.

    Schema-less built-in profiles created before translation-quality hardening
    used affiliate_conversational as the default for every video. Upgrade only
    that untouched legacy built-in shape; explicit/newer affiliate profiles are
    preserved exactly.
    """
    if not raw:
        return default_profile_store()
    try:
        payload = json.loads(raw)
        profiles = payload.get("profiles") if isinstance(payload, dict) else None
        if isinstance(profiles, list):
            for item in profiles:
                if not isinstance(item, dict):
                    continue
                if "rewrite_instructions" not in item:
                    item["rewrite_instructions"] = str(
                        item.get("translation_instructions") or ""
                    )
                is_legacy_builtin = (
                    item.get("id") == "vi-affiliate-hq"
                    and item.get("name") == "Vietnam Affiliate HQ"
                    and "rewrite_model" not in item
                    and "rewrite_provider" not in item
                    and "rewrite_base_url" not in item
                    and item.get("translation_preset", "affiliate_conversational")
                        == "affiliate_conversational"
                    and not item.get("translation_instructions")
                    and not item.get("glossary")
                )
                if is_legacy_builtin:
                    item["translation_preset"] = "natural_vietnamese"
        store = LocalizationProfileStore.model_validate(payload)
    except Exception:
        return default_profile_store()
    if not store.profiles:
        return default_profile_store()

    ids = {p.id for p in store.profiles}
    if store.default_profile_id not in ids:
        store.default_profile_id = store.profiles[0].id
    return store


def upsert_profile(
    store: LocalizationProfileStore,
    profile: LocalizationProfile,
) -> LocalizationProfileStore:
    profiles = list(store.profiles)
    for i, existing in enumerate(profiles):
        if existing.id == profile.id:
            profiles[i] = profile
            break
    else:
        if len(profiles) >= MAX_LOCALIZATION_PROFILES:
            raise ValueError(f"Maximum {MAX_LOCALIZATION_PROFILES} localization profiles.")
        profiles.append(profile)
    return LocalizationProfileStore(
        default_profile_id=store.default_profile_id,
        profiles=profiles,
    )


def delete_profile(
    store: LocalizationProfileStore,
    profile_id: str,
) -> LocalizationProfileStore:
    profiles = [p for p in store.profiles if p.id != profile_id]
    if not profiles:
        return default_profile_store()
    default_id = store.default_profile_id
    if default_id == profile_id or default_id not in {p.id for p in profiles}:
        default_id = profiles[0].id
    return LocalizationProfileStore(default_profile_id=default_id, profiles=profiles)


def set_default_profile(
    store: LocalizationProfileStore,
    profile_id: str,
) -> LocalizationProfileStore:
    if profile_id not in {p.id for p in store.profiles}:
        raise KeyError(profile_id)
    return LocalizationProfileStore(default_profile_id=profile_id, profiles=store.profiles)



class LocalizationSegment(BaseModel):
    """One source speech unit plus its independently resolved output timing."""

    id: str
    speaker_id: str | None = None

    source_text: str
    translated_text: str | None = None

    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(ge=0)
    target_start_ms: int | None = Field(default=None, ge=0)
    target_end_ms: int | None = Field(default=None, ge=0)

    tts_duration_ms: int | None = Field(default=None, ge=0)

    translation_status: Literal["pending", "success", "failed", "untranslated"] = "pending"
    timing_status: Literal["pending", "fit", "adjusted", "overflow"] = "pending"
    qc_status: Literal["pending", "pass", "review", "fail"] = "pending"

    # Source-language word timings remain useful for alignment/inspection, but
    # they are never reused as target-language caption words.
    source_words: list[dict] = Field(default_factory=list)
    overrides: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_timeline(self):
        if self.source_end_ms < self.source_start_ms:
            raise ValueError("source_end_ms must be >= source_start_ms")
        if (self.target_start_ms is None) != (self.target_end_ms is None):
            raise ValueError("target start/end must be both set or both unset")
        if (
            self.target_start_ms is not None
            and self.target_end_ms is not None
            and self.target_end_ms < self.target_start_ms
        ):
            raise ValueError("target_end_ms must be >= target_start_ms")
        return self


class LocalizationTranscript(BaseModel):
    text: str = ""
    language: str | None = None
    source: str | None = None
    segments: list[LocalizationSegment] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


def _seconds_to_ms(value) -> int:
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid source timestamp: {value!r}") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"Invalid source timestamp: {value!r}")
    return round(seconds * 1000)


def localization_segments_from_source(raw_segments: list[dict] | None) -> list[LocalizationSegment]:
    """Normalize Whisper/subtitle source segments without inventing target time.

    Existing storage stays source-native for backward compatibility. C6+ calls
    this once and works only with the explicit source/target coordinate model.
    """
    out: list[LocalizationSegment] = []
    for index, raw in enumerate(raw_segments or []):
        if not isinstance(raw, dict):
            continue

        # Already canonical: validate and keep it.
        if "source_start_ms" in raw or "source_text" in raw:
            out.append(LocalizationSegment.model_validate(raw))
            continue

        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        start_ms = _seconds_to_ms(raw.get("start"))
        end_ms = _seconds_to_ms(raw.get("end"))
        if end_ms < start_ms:
            raise ValueError(f"Segment {index} has end before start")

        out.append(LocalizationSegment(
            id=str(raw.get("id") or f"seg-{index + 1:04d}"),
            speaker_id=(str(raw["speaker_id"]) if raw.get("speaker_id") is not None else None),
            source_text=text,
            source_start_ms=start_ms,
            source_end_ms=end_ms,
            source_words=list(raw.get("words") or []),
        ))
    return out


def localization_transcript_from_source(
    *,
    text: str | None,
    language: str | None,
    source: str | None,
    raw_segments: list[dict] | None,
) -> LocalizationTranscript:
    return LocalizationTranscript(
        text=(text or "").strip(),
        language=language,
        source=source,
        segments=localization_segments_from_source(raw_segments),
    )
