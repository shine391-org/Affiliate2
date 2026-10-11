# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for Fast preset, ASR schema, and profile settings UI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.localization import (
    LocalizationProfile,
    LocalizationProfileStore,
    default_localization_profile,
    default_profile_store,
    delete_profile,
    fast_localization_profile,
    load_profile_store,
    set_default_profile,
    upsert_profile,
)


# ── Fast preset factory ──────────────────────────────────────────────────────

class TestFastLocalizationProfile:
    def test_materialized_fast_does_not_inherit_paid_connection(self):
        from types import SimpleNamespace
        from backend.services.localization_service import materialize_localization_profile
        row = SimpleNamespace(ai_provider="compatible", ai_model="saved-model", ai_connection_id="saved-ai", ai_base_url="http://127.0.0.1:1234/v1", tts_provider="elevenlabs", tts_connection_id="paid-tts")
        actual = materialize_localization_profile(row, fast_localization_profile(), target_language="Vietnamese")
        assert actual.tts_provider == "edge_tts"
        assert actual.tts_connection_id is None
        assert actual.tts_voice == "vi-VN-HoaiMyNeural"
    def test_profile_api_exposes_preset_without_saving_or_replacing_profiles(self):
        from backend.api.settings import _localization_profile_payload
        store = default_profile_store()
        payload = _localization_profile_payload(store)
        assert payload["presets"]["fast"] == fast_localization_profile().model_dump()
        assert payload["profiles"] == store.model_dump()["profiles"]

    def test_groq_rejects_chat_model_or_unconfigured_local_fallback(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="whisper-large-v3-turbo"):
            LocalizationProfile(id="p", name="P", asr_provider="groq", asr_model="qwen/chat")
        with pytest.raises(ValidationError, match="local"):
            LocalizationProfile(id="p", name="P", asr_provider="groq", asr_fallback_to_local=True)
    def test_fast_profile_has_correct_id_and_name(self):
        profile = fast_localization_profile()
        assert profile.id == "vi-affiliate-fast"
        assert profile.name == "Vietnam Affiliate Fast"

    def test_fast_profile_asr_balanced_not_best(self):
        profile = fast_localization_profile()
        assert profile.asr_quality == "balanced"

    def test_fast_profile_alignment_off(self):
        profile = fast_localization_profile()
        assert profile.alignment_mode == "off"

    def test_fast_profile_diarization_only(self):
        profile = fast_localization_profile()
        assert profile.diarization_mode == "auto"
        assert profile.alignment_mode == "off"

    def test_fast_profile_audio_fast_duck(self):
        profile = fast_localization_profile()
        assert profile.audio_mode == "fast_duck"

    def test_fast_profile_semantic_segmentation_off(self):
        profile = fast_localization_profile()
        assert profile.semantic_segmentation is False

    def test_fast_profile_render_balanced(self):
        profile = fast_localization_profile()
        assert profile.render_profile == "balanced"

    def test_fast_profile_preserves_qc(self):
        profile = fast_localization_profile()
        assert profile.qc_profile == default_localization_profile().qc_profile

    def test_fast_profile_preserves_speed_limits_and_dry_background(self):
        profile = fast_localization_profile()
        hq = default_localization_profile()
        assert profile.dub_speed_accept == hq.dub_speed_accept
        assert profile.dub_speed_max == hq.dub_speed_max
        assert profile.dub_emergency_speed_max == hq.dub_emergency_speed_max
        assert profile.preserve_background is False
        assert profile.asr_model == "small"

    def test_fast_profile_tts_edge_hoaimy(self):
        profile = fast_localization_profile()
        assert profile.tts_provider == "edge_tts"
        assert profile.tts_voice == "vi-VN-HoaiMyNeural"

    def test_fast_profile_rewrite_attempts_1(self):
        profile = fast_localization_profile()
        assert profile.dub_rewrite_attempts == 1

    def test_fast_profile_not_elevenlabs(self):
        profile = fast_localization_profile()
        assert profile.tts_provider != "elevenlabs"

    def test_fast_profile_is_valid_pydantic_model(self):
        profile = fast_localization_profile()
        assert isinstance(profile, LocalizationProfile)
        # Should not raise
        profile.model_dump()


# ── ASR schema: groq/faster_whisper model, asr_connection_id, fallback ──────

class TestASRSchema:
    def test_asr_provider_accepts_groq(self):
        profile = LocalizationProfile(
            id="test",
            name="Test",
            asr_provider="groq",
        )
        assert profile.asr_provider == "groq"

    def test_asr_provider_accepts_faster_whisper(self):
        profile = LocalizationProfile(
            id="test",
            name="Test",
            asr_provider="faster_whisper",
        )
        assert profile.asr_provider == "faster_whisper"

    def test_asr_connection_id_optional(self):
        profile = LocalizationProfile(
            id="test",
            name="Test",
            asr_connection_id="groq-conn-1",
        )
        assert profile.asr_connection_id == "groq-conn-1"

    def test_asr_connection_id_default_none(self):
        profile = LocalizationProfile(id="test", name="Test")
        assert profile.asr_connection_id is None

    def test_asr_fallback_to_local_default_false(self):
        profile = LocalizationProfile(id="test", name="Test")
        assert profile.asr_fallback_to_local is False

    def test_asr_fallback_to_local_can_be_enabled(self):
        profile = LocalizationProfile(
            id="test",
            name="Test",
            asr_fallback_to_local=True,
        )
        assert profile.asr_fallback_to_local is True

    def test_asr_local_model_optional(self):
        profile = LocalizationProfile(
            id="test",
            name="Test",
            asr_local_model="small",
        )
        assert profile.asr_local_model == "small"

    def test_asr_local_model_default_none(self):
        profile = LocalizationProfile(id="test", name="Test")
        assert profile.asr_local_model is None

    def test_asr_local_model_explicit_fallback(self):
        """When asr_fallback_to_local is True, asr_local_model should be set."""
        profile = LocalizationProfile(
            id="test",
            name="Test",
            asr_provider="groq",
            asr_connection_id="groq-conn",
            asr_fallback_to_local=True,
            asr_local_model="base",
        )
        assert profile.asr_fallback_to_local is True
        assert profile.asr_local_model == "base"


# ── User-triggered create preset: existing CRUD no overwrite ────────────────

class TestFastProfileCRUDNoOverwrite:
    def test_upsert_new_fast_profile_does_not_overwrite_existing(self):
        """Creating a fast profile should not overwrite existing profiles."""
        store = default_profile_store()
        fast = fast_localization_profile()
        new_store = upsert_profile(store, fast)
        assert len(new_store.profiles) == 2
        ids = [p.id for p in new_store.profiles]
        assert "vi-affiliate-hq" in ids
        assert "vi-affiliate-fast" in ids

    def test_upsert_fast_profile_twice_replaces_not_duplicates(self):
        store = default_profile_store()
        fast = fast_localization_profile()
        store = upsert_profile(store, fast)
        store = upsert_profile(store, fast)
        assert len(store.profiles) == 2

    def test_delete_fast_profile_keeps_hq(self):
        store = default_profile_store()
        fast = fast_localization_profile()
        store = upsert_profile(store, fast)
        store = delete_profile(store, "vi-affiliate-fast")
        assert len(store.profiles) == 1
        assert store.profiles[0].id == "vi-affiliate-hq"

    def test_set_default_to_fast_profile(self):
        store = default_profile_store()
        fast = fast_localization_profile()
        store = upsert_profile(store, fast)
        store = set_default_profile(store, "vi-affiliate-fast")
        assert store.default_profile_id == "vi-affiliate-fast"

    def test_fast_profile_survives_serialization_roundtrip(self):
        fast = fast_localization_profile()
        raw = fast.model_dump_json()
        restored = LocalizationProfile.model_validate(json.loads(raw))
        assert restored.id == "vi-affiliate-fast"
        assert restored.asr_quality == "balanced"
        assert restored.audio_mode == "fast_duck"
        assert restored.semantic_segmentation is False

    def test_fast_profile_in_store_roundtrip(self):
        store = default_profile_store()
        fast = fast_localization_profile()
        store = upsert_profile(store, fast)
        raw = store.model_dump_json()
        restored = load_profile_store(raw)
        assert len(restored.profiles) == 2
        fast_restored = next(p for p in restored.profiles if p.id == "vi-affiliate-fast")
        assert fast_restored.asr_quality == "balanced"
        assert fast_restored.audio_mode == "fast_duck"


# ── HQ defaults backward compatible ─────────────────────────────────────────

class TestHQDefaultsBackwardCompatible:
    def test_hq_profile_unchanged(self):
        hq = default_localization_profile()
        assert hq.id == "vi-affiliate-hq"
        assert hq.asr_quality == "best"
        assert hq.alignment_mode == "whisperx"
        assert hq.diarization_mode == "auto"
        assert hq.audio_mode == "hq_clean"
        assert hq.semantic_segmentation is True
        assert hq.render_profile == "hq"
        assert hq.qc_profile == "strict"
        assert hq.dub_speed_accept == 1.15
        assert hq.dub_speed_max == 1.30
        assert hq.dub_rewrite_attempts == 2

    def test_hq_profile_asr_provider_default_faster_whisper(self):
        hq = default_localization_profile()
        assert hq.asr_provider == "faster_whisper"

    def test_hq_profile_asr_connection_id_default_none(self):
        hq = default_localization_profile()
        assert hq.asr_connection_id is None

    def test_hq_profile_asr_fallback_default_false(self):
        hq = default_localization_profile()
        assert hq.asr_fallback_to_local is False

    def test_hq_profile_asr_local_model_default_none(self):
        hq = default_localization_profile()
        assert hq.asr_local_model is None

    def test_default_profile_store_has_only_hq(self):
        store = default_profile_store()
        assert len(store.profiles) == 1
        assert store.profiles[0].id == "vi-affiliate-hq"
        assert store.default_profile_id == "vi-affiliate-hq"


# ── UI component tests (frontend) ───────────────────────────────────────────

def _read_localization_profiles_section() -> str:
    """Read the LocalizationProfilesSection.jsx from the repo root."""
    # pytest runs from app/, so frontend/ is a sibling of tests/
    app_root = Path(__file__).resolve().parent.parent
    return (app_root / "frontend" / "src" / "components" / "settings" / "LocalizationProfilesSection.jsx").read_text(encoding="utf-8")


class TestFrontendFastProfileUI:
    def test_localization_profiles_section_has_fast_preset_button(self):
        """UI should have a button to create a fast profile."""
        content = _read_localization_profiles_section()
        assert "addFastProfile" in content
        assert "Fast" in content

    def test_localization_profiles_section_has_asr_provider_select(self):
        """UI should have a select for ASR provider (faster_whisper/groq)."""
        content = _read_localization_profiles_section()
        assert "asr_provider" in content
        assert "groq" in content.lower()
        assert "faster_whisper" in content

    def test_localization_profiles_section_has_asr_connection_select(self):
        """UI should have a select for ASR connection (Groq)."""
        content = _read_localization_profiles_section()
        assert "asr_connection_id" in content

    def test_localization_profiles_section_has_asr_fallback_checkbox(self):
        """UI should have a checkbox for ASR fallback to local."""
        content = _read_localization_profiles_section()
        assert "asr_fallback_to_local" in content

    def test_localization_profiles_section_has_asr_local_model_input(self):
        """UI should have an input for ASR local model."""
        content = _read_localization_profiles_section()
        assert "asr_local_model" in content

    def test_localization_profiles_section_has_groq_provider_option(self):
        """UI should mention Groq as an ASR provider option."""
        content = _read_localization_profiles_section()
        assert "Groq" in content or "groq" in content

    def test_localization_profiles_section_has_edge_hoaimy_pinned(self):
        """UI should mention Edge TTS HoaiMy voice."""
        content = _read_localization_profiles_section()
        # UI consumes the backend canonical preset instead of duplicating it.
        assert "store.presets?.fast" in content
        assert 'tts_connection_id: activeTtsId' not in content[content.index("const addFastProfile"):content.index("const remove")]

    def test_localization_profiles_section_no_second_switch(self):
        """UI should not have a second switch for fast mode (only preset button)."""
        content = _read_localization_profiles_section()
        # Should not have a toggle/switch for fast mode
        assert "fast_mode" not in content.lower()
        assert "fastMode" not in content

    def test_localization_profiles_section_has_lost_background_explanation(self):
        """UI should explain lost background/source bleed."""
        content = _read_localization_profiles_section()
        assert "background" in content.lower() or "nền" in content
        assert "duck" in content.lower() or "hạ nền" in content
