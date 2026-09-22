# SPDX-License-Identifier: AGPL-3.0-only
import json

import pytest

from backend.core.localization import (
    LocalizationProfile,
    default_profile_store,
    delete_profile,
    load_profile_store,
    set_default_profile,
    upsert_profile,
)


def test_default_profile_is_hq_and_quality_first():
    store = default_profile_store()
    profile = store.profiles[0]
    assert profile.id == store.default_profile_id
    assert profile.download_quality == "best"
    assert profile.asr_provider == "faster_whisper"
    assert profile.asr_model is None
    assert profile.asr_quality == "best"
    assert profile.timing_policy == "safe_fit"
    assert profile.render_profile == "hq"
    assert profile.qc_profile == "strict"


def test_profile_round_trips_as_one_json_payload():
    store = default_profile_store()
    raw = store.model_dump_json()
    restored = load_profile_store(raw)
    assert restored == store


def test_profile_upsert_replaces_by_id_without_duplicates():
    store = default_profile_store()
    edited = LocalizationProfile(
        **{**store.profiles[0].model_dump(), "name": "HQ Custom", "timing_tolerance_ms": 400}
    )
    store = upsert_profile(store, edited)
    assert len(store.profiles) == 1
    assert store.profiles[0].name == "HQ Custom"
    assert store.profiles[0].timing_tolerance_ms == 400


def test_delete_default_moves_default_to_remaining_profile():
    store = default_profile_store()
    second = LocalizationProfile(id="literal-hq", name="Literal HQ", translation_preset="literal")
    store = upsert_profile(store, second)
    store = delete_profile(store, store.default_profile_id)
    assert store.default_profile_id == "literal-hq"
    assert [p.id for p in store.profiles] == ["literal-hq"]


def test_delete_last_profile_restores_safe_default():
    store = default_profile_store()
    store = delete_profile(store, store.default_profile_id)
    assert store.profiles and store.default_profile_id == store.profiles[0].id


def test_set_default_rejects_unknown_profile():
    with pytest.raises(KeyError):
        set_default_profile(default_profile_store(), "missing")


def test_profile_rejects_unbounded_glossary():
    with pytest.raises(ValueError):
        LocalizationProfile(
            id="too-many",
            name="Too many",
            glossary={f"k{i}": f"v{i}" for i in range(101)},
        )


def test_corrupt_store_falls_back_instead_of_bricking_settings():
    assert load_profile_store("{not json").profiles
    assert load_profile_store(json.dumps({"profiles": []})).profiles


def test_profile_accepts_explicit_asr_model_and_strict_timing():
    profile = LocalizationProfile(
        id="accurate-local",
        name="Accurate Local",
        asr_provider="faster_whisper",
        asr_model="large-v3",
        asr_quality="best",
        timing_policy="strict",
    )
    assert profile.asr_model == "large-v3"
    assert profile.timing_policy == "strict"
