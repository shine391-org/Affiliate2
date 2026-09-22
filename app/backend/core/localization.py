# SPDX-License-Identifier: AGPL-3.0-only
"""Canonical Phase-C localization configuration and timeline contracts.

Keep this module boring: validated data shapes only. Runtime orchestration stays
in the existing job/tool pipeline.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_PROFILE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
MAX_LOCALIZATION_PROFILES = 20


class LocalizationProfile(BaseModel):
    id: str
    name: str

    download_quality: Literal["best", "2160p", "1440p", "1080p", "720p", "480p"] = "best"
    asr_quality: Literal["fast", "balanced", "accurate", "best"] = "best"

    # None = inherit the global AI/TTS setting. This avoids duplicating keys or
    # provider connection data inside every profile.
    translation_provider: str | None = None
    translation_model: str | None = None
    translation_preset: Literal[
        "natural_vietnamese", "affiliate_conversational", "literal", "custom"
    ] = "affiliate_conversational"
    translation_instructions: str = ""
    glossary: dict[str, str] = Field(default_factory=dict)

    tts_provider: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None
    tts_rate_policy: Literal["auto", "natural", "strict"] = "auto"

    timing_tolerance_ms: int = Field(default=250, ge=0, le=2000)
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

    @field_validator("translation_instructions")
    @classmethod
    def cap_instructions(cls, value: str) -> str:
        value = value.strip()
        if len(value) > 8000:
            raise ValueError("Translation instructions are too long (max 8000 chars).")
        return value

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
    """Parse persisted JSON; corrupt/old data falls back to one safe profile."""
    if not raw:
        return default_profile_store()
    try:
        store = LocalizationProfileStore.model_validate(json.loads(raw))
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
