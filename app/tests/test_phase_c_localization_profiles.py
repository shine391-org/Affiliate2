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


def test_default_profile_is_full_hq_donor_stack():
    store = default_profile_store()
    profile = store.profiles[0]
    assert profile.id == store.default_profile_id == "vi-affiliate-hq"
    assert profile.download_quality == "best"
    assert profile.asr_provider == "faster_whisper"
    assert profile.asr_quality == "best"
    assert profile.alignment_mode == "whisperx"
    assert profile.diarization_mode == "auto"
    assert profile.semantic_segmentation is True
    assert profile.dub_min_cue_duration_ms == 2500
    assert profile.dub_max_chunk_size == 5
    assert profile.dub_gap_tolerance_ms == 1500
    assert profile.dub_speed_accept == 1.15
    assert profile.dub_speed_max == 1.30
    assert profile.video_retime_mode == "fallback"
    assert profile.video_retime_max_slowdown == 1.12
    assert profile.audio_mode == "hq_clean"
    assert profile.render_profile == "hq"
    assert profile.qc_profile == "strict"


def test_profile_round_trips_as_one_json_payload():
    store = default_profile_store()
    restored = load_profile_store(store.model_dump_json())
    assert restored == store


def test_profile_upsert_replaces_by_id_without_duplicates():
    store = default_profile_store()
    edited = LocalizationProfile.model_validate({
        **store.profiles[0].model_dump(),
        "name": "HQ Custom",
        "dub_speed_max": 1.25,
        "speaker_voices": {"SPEAKER_00": "voice-a"},
    })
    store = upsert_profile(store, edited)
    assert len(store.profiles) == 1
    assert store.profiles[0].name == "HQ Custom"
    assert store.profiles[0].speaker_voices == {"SPEAKER_00": "voice-a"}


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


def test_profile_accepts_multi_speaker_and_clone_reference_config():
    profile = LocalizationProfile(
        id="multi",
        name="Multi",
        diarization_mode="whisperx",
        diarization_speakers=2,
        speaker_voices={"SPEAKER_00": "voice-a", "SPEAKER_01": "voice-b"},
        speaker_reference_audio={"SPEAKER_00": "a.wav"},
        tts_provider="gpt_sovits",
        tts_reference_audio="fallback.wav",
        tts_reference_text="reference text",
        tts_reference_language="en",
    )
    assert profile.diarization_speakers == 2
    assert profile.speaker_voices["SPEAKER_01"] == "voice-b"
    assert profile.tts_provider == "gpt_sovits"


def test_profile_rejects_invalid_speed_policy():
    with pytest.raises(ValueError, match="dub_speed_accept"):
        LocalizationProfile(
            id="bad-speed",
            name="Bad Speed",
            dub_speed_accept=1.4,
            dub_speed_max=1.2,
        )


def test_profile_rejects_excessive_video_slowdown():
    with pytest.raises(ValueError):
        LocalizationProfile(
            id="bad-retime",
            name="Bad Retime",
            video_retime_max_slowdown=1.5,
        )
