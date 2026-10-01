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


def _cue(index, start, end, text, speaker=None, voice=None, source=None):
    return DubbingCue(
        index=index,
        segment_id=f"s{index}",
        start_ms=start,
        end_ms=end,
        text=text,
        source_text=source or "",
        speaker_id=speaker,
        voice_id=voice,
    )


def test_planner_keeps_viable_short_sentences_separate():
    cfg = DubbingConfig()
    plan, chunks = build_plan([
        _cue(1, 0, 800, "Yes."),
        _cue(2, 1000, 2200, "we start here"),
        _cue(3, 5000, 6000, "next section"),
    ], cfg)
    assert len(plan) == 3
    assert len(chunks) == 3
    assert plan[0].chunk_id != plan[1].chunk_id
    assert plan[2].chunk_id != plan[1].chunk_id


def test_planner_merges_long_cues_when_the_first_is_not_a_sentence_boundary():
    cfg = DubbingConfig()
    plan, chunks = build_plan([
        _cue(1, 0, 3000, "first half of the same thought"),
        _cue(2, 3200, 6200, "continues here without a sentence break"),
    ], cfg)
    assert len(chunks) == 1
    assert chunks[0].item_indexes == [0, 1]
    assert plan[0].chunk_id == plan[1].chunk_id


def test_planner_keeps_long_complete_sentences_as_separate_tts_chunks():
    cfg = DubbingConfig()
    _, chunks = build_plan([
        _cue(1, 0, 3000, "first long sentence."),
        _cue(2, 3200, 6200, "second long sentence"),
    ], cfg)
    assert len(chunks) == 2


def test_planner_does_not_merge_long_continuation_across_a_real_pause():
    cfg = DubbingConfig(gap_tolerance_ms=1500)
    _, chunks = build_plan([
        _cue(1, 0, 3000, "first half of the same thought"),
        _cue(2, 3501, 6501, "continues after a real pause"),
    ], cfg)
    assert len(chunks) == 2


def test_planner_does_not_merge_short_cue_across_a_real_pause():
    cfg = DubbingConfig(gap_tolerance_ms=1500)
    _, chunks = build_plan([
        _cue(1, 0, 400, "yes"),
        _cue(2, 1200, 2600, "continue after the pause"),
    ], cfg)
    assert len(chunks) == 2


def test_planner_respects_source_sentence_boundary_when_translation_loses_punctuation():
    cfg = DubbingConfig()
    _, chunks = build_plan([
        _cue(1, 0, 3000, "cau dau", source="第一句。"),
        _cue(2, 3150, 6000, "cau tiep theo", source="第二句"),
    ], cfg)
    assert len(chunks) == 2


def test_planner_natural_continuation_still_stops_at_span_limit():
    cfg = DubbingConfig(max_chunk_size=5)
    _, chunks = build_plan([
        _cue(1, 0, 4000, "first half"),
        _cue(2, 4200, 8100, "second half"),
    ], cfg)
    assert len(chunks) == 2


def test_short_cue_merge_cannot_bypass_span_limit_real_tail():
    _, chunks = build_plan([
        _cue(31, 49792, 55930, "thể nè, ồ cái này đẹp lắm, qua tấm tiếp theo, quỳ xuống, tôi nhận được ảnh thần rồi, nào"),
        _cue(32, 55930, 58312, "bên trái nâng lên, cảm ơn Chanh接下来"),
        _cue(33, 58312, 62939, "nối mắt cô cao lên một chút, đẹp"),
    ], DubbingConfig())
    assert [ch.item_indexes for ch in chunks] == [[0], [1, 2]]
    assert [ch.available_ms for ch in chunks] == [6138, 7009]


def test_span_split_still_rescues_impossible_micro_cue():
    _, chunks = build_plan([
        _cue(1, 0, 8000, "a healthy previous cue"),
        _cue(2, 8000, 8021, "Ừ"),
    ], DubbingConfig())
    assert len(chunks) == 1
    assert chunks[0].item_indexes == [0, 1]
    assert chunks[0].available_ms == 8021


def test_orphan_rescue_cannot_keep_growing_an_oversized_chunk():
    _, chunks = build_plan([
        _cue(1, 0, 7900, "a healthy previous cue"),
        _cue(2, 8786, 8807, "Ừ"),
        _cue(3, 9693, 9714, "Ừ"),
    ], DubbingConfig())
    assert [ch.item_indexes for ch in chunks] == [[0], [1], [2]]
    assert all(ch.available_ms <= 8000 for ch in chunks)
    assert all(ch.available_ms * 1.30 >= 278 for ch in chunks[1:])


def test_orphan_rescue_cannot_create_an_unbounded_span():
    _, chunks = build_plan([
        _cue(1, 0, 8000, "first"),
        _cue(2, 9500, 12500, " ".join(["word"] * 20)),
    ], DubbingConfig())
    assert len(chunks) == 2


@pytest.mark.parametrize("speaker_pair", [("a", None), (None, "a")])
def test_unknown_speaker_transition_remains_a_hard_boundary(speaker_pair):
    _, chunks = build_plan([
        _cue(1, 0, 3000, "first half", speaker=speaker_pair[0]),
        _cue(2, 3150, 6000, "second half", speaker=speaker_pair[1]),
    ], DubbingConfig())
    assert len(chunks) == 2


def test_planner_nested_overlap_never_shrinks_chunk_end():
    cfg = DubbingConfig()
    _, chunks = build_plan([
        _cue(1, 0, 3000, "continuing thought"),
        _cue(2, 2950, 2990, "tail"),
    ], cfg)
    assert len(chunks) == 1
    assert chunks[0].end_ms == 3000


@pytest.mark.parametrize("ending", [
    "first long sentence.", "Tốn tiền không?", "Done.)", "第一句。",
    "Done!", "Done…", "Done.\"]}", "Done!  ",
])
def test_compiled_strong_sentence_endings(ending):
    from backend.services.dubbing_planner import _STRONG_END

    assert _STRONG_END.search(ending)
    assert not _STRONG_END.search("first half of the same thought")


def test_orphan_real_pause_is_not_erased_by_text_rescue():
    _, chunks = build_plan([
        _cue(1, 245, 1019, "Cắt phần môi xuống cho tôi"),
        _cue(2, 1905, 1926, "Ừ"),
    ], DubbingConfig(), language="Vietnamese")
    assert [c.item_indexes for c in chunks] == [[0], [1]]
    assert chunks[1].start_ms >= chunks[0].end_ms + 350
    assert chunks[1].available_ms * 1.30 >= 278


@pytest.mark.parametrize("gap,limit", [
    (1000, 1500), (1500, 1500), (1501, 1500), (886, 800), (800, 800),
])
def test_orphan_allocation_respects_budget_without_erasing_pause(gap, limit):
    cfg = DubbingConfig(max_chunk_size=1, gap_tolerance_ms=limit)
    _, chunks = build_plan([
        _cue(1, 0, 1000, "first"),
        _cue(2, 1000 + gap, 1021 + gap, "Ừ"),
    ], cfg)
    assert len(chunks) == 2
    assert chunks[1].available_ms * cfg.speed_max >= 278
    assert 1021 + gap - chunks[1].start_ms <= 21 + limit
    assert chunks[1].start_ms - chunks[0].end_ms >= 350


@pytest.mark.parametrize("speaker,voice", [("b", "v1"), ("a", "v2"), ("a", None), (None, "v1")])
def test_orphan_rescue_preserves_speaker_and_voice_boundaries(speaker, voice):
    _, chunks = build_plan([
        _cue(1, 245, 1019, "first", speaker="a", voice="v1"),
        _cue(2, 1905, 1926, "Ừ", speaker=speaker, voice=voice),
    ], DubbingConfig())
    assert len(chunks) == 2


@pytest.mark.parametrize("gap", [800, 886, 1000])
def test_viable_cues_still_preserve_real_pause(gap):
    _, chunks = build_plan([
        _cue(1, 0, 3000, "first half of the same thought"),
        _cue(2, 3000 + gap, 4000 + gap, "continues here"),
    ], DubbingConfig())
    assert len(chunks) == 2


def test_orphan_rescue_overlap_never_shrinks_previous_chunk_end():
    cfg = DubbingConfig(max_chunk_size=1, gap_tolerance_ms=1500, speed_max=1.30)
    _, chunks = build_plan([
        _cue(1, 0, 3000, "continuing thought"),
        _cue(2, 2950, 2990, "sẽ"),
    ], cfg, language="Vietnamese")
    assert len(chunks) == 1
    assert chunks[0].end_ms == 3000


def test_source_sentence_boundary_accepts_ascii_closing_parenthesis():
    cfg = DubbingConfig()
    _, chunks = build_plan([
        _cue(1, 0, 3000, "cau dau", source="Done.)"),
        _cue(2, 3150, 6000, "cau tiep theo", source="Next"),
    ], cfg)
    assert len(chunks) == 2


def test_viable_short_cue_after_sentence_boundary_remains_separate():
    cfg = DubbingConfig()
    plan, chunks = build_plan([
        _cue(1, 0, 3000, "First long sentence."),
        _cue(2, 3200, 3500, "ok"),
    ], cfg)
    assert [c.item_indexes for c in chunks] == [[0], [1]]
    assert plan[0].chunk_id != plan[1].chunk_id


def test_planner_respects_max_chunk_size():
    cfg = DubbingConfig(max_chunk_size=2)
    _, chunks = build_plan([
        _cue(1, 0, 500, "one"),
        _cue(2, 600, 1100, "two"),
        _cue(3, 1200, 1700, "three"),
    ], cfg)
    assert [len(c.item_indexes) for c in chunks] == [2, 1]


def test_planner_rebalances_max_size_split_that_strands_impossible_micro_cue():
    """Real b8193843 shape: 16 cues at max size 5 left a 20ms word alone."""
    cfg = DubbingConfig(max_chunk_size=5, gap_tolerance_ms=1500, speed_max=1.30)
    cues = [
        _cue(i, (i - 1) * 1200, i * 1200 - 200, f"cue {i}")
        for i in range(1, 16)
    ]
    cues.append(_cue(16, cues[-1].end_ms, cues[-1].end_ms + 20, "sẽ"))

    plan, chunks = build_plan(cues, cfg, language="Vietnamese")

    assert [len(c.item_indexes) for c in chunks] == [5, 5, 6]
    assert chunks[-1].item_indexes == [10, 11, 12, 13, 14, 15]
    assert chunks[-1].start_ms == cues[10].start_ms
    assert plan[10].chunk_id == plan[15].chunk_id


def test_planner_keeps_stable_ids_after_allocating_an_intermediate_orphan():
    cfg = DubbingConfig(max_chunk_size=2, gap_tolerance_ms=1500, speed_max=1.30)
    cues = [
        _cue(1, 0, 1000, "one", speaker="a"),
        _cue(2, 1000, 2000, "two", speaker="a"),
        _cue(3, 2000, 2020, "sẽ", speaker="a"),
        _cue(4, 4000, 5000, "four", speaker="b"),
    ]

    plan, chunks = build_plan(cues, cfg, language="Vietnamese")

    assert [chunk.id for chunk in chunks] == [1, 2, 3]
    assert [item.chunk_id for item in plan] == [1, 1, 2, 3]
    assert chunks[1].available_ms * cfg.speed_max >= plan[2].estimated_duration_ms


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


def test_borrow_following_silence_uses_real_trailing_timeline_for_last_chunk():
    chunks = [
        type("C", (), {"end_ms": 500, "start_ms": 0})(),
    ]
    borrow_following_silence(
        chunks,
        max_borrow_ms=400,
        timeline_end_ms=1000,
    )
    assert chunks[0].end_ms == 900


def test_borrow_following_silence_does_not_extend_past_timeline_end():
    chunks = [
        type("C", (), {"end_ms": 1200, "start_ms": 1000})(),
    ]
    borrow_following_silence(
        chunks,
        max_borrow_ms=400,
        timeline_end_ms=1100,
    )
    assert chunks[0].end_ms == 1200


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
