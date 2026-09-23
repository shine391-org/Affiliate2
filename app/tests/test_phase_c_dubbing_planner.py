# SPDX-License-Identifier: AGPL-3.0-only
"""Donor-behavior regressions for the OpenCreator-derived dubbing planner."""
import pytest

from backend.services.dubbing_planner import (
    DubbingConfig,
    DubbingCue,
    atempo_filter,
    borrow_following_silence,
    build_plan,
    fit_timeline,
)


def _cue(index, start, end, text, speaker=None, voice=None):
    return DubbingCue(
        index=index,
        segment_id=f"s{index}",
        start_ms=start,
        end_ms=end,
        text=text,
        speaker_id=speaker,
        voice_id=voice,
    )


def test_planner_merges_short_adjacent_cues_like_opencreator():
    cfg = DubbingConfig()
    plan, chunks = build_plan([
        _cue(1, 0, 800, "Yes."),
        _cue(2, 1000, 2200, "we start here"),
        _cue(3, 5000, 6000, "next section"),
    ], cfg)
    assert len(plan) == 3
    assert len(chunks) == 2
    assert plan[0].chunk_id == plan[1].chunk_id
    assert plan[2].chunk_id != plan[1].chunk_id


def test_planner_does_not_merge_long_cues_only_because_gap_is_small():
    cfg = DubbingConfig()
    plan, chunks = build_plan([
        _cue(1, 0, 3000, "first long sentence"),
        _cue(2, 3200, 6200, "second long sentence"),
    ], cfg)
    assert len(chunks) == 2


def test_planner_respects_max_chunk_size():
    cfg = DubbingConfig(max_chunk_size=2)
    _, chunks = build_plan([
        _cue(1, 0, 500, "one"),
        _cue(2, 600, 1100, "two"),
        _cue(3, 1200, 1700, "three"),
    ], cfg)
    assert [len(c.item_indexes) for c in chunks] == [2, 1]


def test_speaker_change_is_hard_chunk_boundary():
    cfg = DubbingConfig()
    plan, chunks = build_plan([
        _cue(1, 0, 500, "one", speaker="A"),
        _cue(2, 520, 1000, "two", speaker="B"),
    ], cfg)
    assert len(chunks) == 2
    assert plan[0].chunk_id != plan[1].chunk_id
    assert chunks[0].speaker_id == "A"
    assert chunks[1].speaker_id == "B"


def test_explicit_voice_change_is_hard_chunk_boundary():
    cfg = DubbingConfig()
    _, chunks = build_plan([
        _cue(1, 0, 500, "one", speaker="A", voice="v1"),
        _cue(2, 520, 1000, "two", speaker="A", voice="v2"),
    ], cfg)
    assert len(chunks) == 2
    assert [c.voice_id for c in chunks] == ["v1", "v2"]


def test_explicit_voice_to_default_is_hard_chunk_boundary():
    cfg = DubbingConfig()
    _, chunks = build_plan([
        _cue(1, 0, 500, "one", speaker="A", voice="v1"),
        _cue(2, 520, 1000, "two", speaker="A", voice=None),
        _cue(3, 1020, 1500, "three", speaker="A", voice="v2"),
    ], cfg)
    assert len(chunks) == 3
    assert [c.voice_id for c in chunks] == ["v1", None, "v2"]


def test_borrow_following_silence_never_crosses_next_speech():
    chunks = [
        type("C", (), {"end_ms": 500, "start_ms": 0})(),
        type("C", (), {"end_ms": 1500, "start_ms": 1200})(),
    ]
    borrow_following_silence(chunks, max_borrow_ms=400)
    assert chunks[0].end_ms == 900


def test_fit_timeline_is_monotonic_and_uses_measured_chunk_duration():
    cfg = DubbingConfig()
    plan, chunks = build_plan([
        _cue(1, 0, 1000, "one"),
        _cue(2, 1100, 2000, "two"),
    ], cfg)
    assert len(chunks) == 1
    chunks[0].actual_duration_ms = 1800
    fitted, fitted_chunks, report = fit_timeline(plan, chunks, cfg)
    assert fitted[0].target_start_ms == 0
    assert fitted[1].target_start_ms >= fitted[0].target_end_ms
    assert fitted[-1].target_end_ms <= chunks[0].end_ms
    assert fitted_chunks[0].speed_factor == 1.0
    assert report.failed_chunk_ids == []


def test_fit_reports_over_max_instead_of_forcing_extreme_speed():
    cfg = DubbingConfig(speed_accept=1.15, speed_max=1.30)
    plan, chunks = build_plan([_cue(1, 0, 1000, "short")], cfg)
    chunks[0].actual_duration_ms = 2000
    fitted, fitted_chunks, report = fit_timeline(plan, chunks, cfg)
    assert report.failed_chunk_ids == [1]
    assert report.max_speed_factor == 2.0
    assert fitted_chunks[0].speed_factor == 1.30
    assert fitted[0].speed_factor == 1.30


@pytest.mark.parametrize("speed", [1.0, 1.2, 2.5, 0.4])
def test_atempo_filter_accepts_valid_positive_speed(speed):
    assert "atempo=" in atempo_filter(speed)


@pytest.mark.parametrize("speed", [0, -1, float("inf"), float("nan")])
def test_atempo_filter_rejects_invalid_speed(speed):
    with pytest.raises(ValueError):
        atempo_filter(speed)
