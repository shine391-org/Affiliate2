# SPDX-License-Identifier: AGPL-3.0-only
from backend.core.localization import LocalizationSegment, LocalizationTranscript
from backend.services.semantic_segmentation import normalize_semantic_cues


def test_semantic_normalizer_merges_tiny_nonterminal_cues():
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(id="s1", source_text="and", source_start_ms=0, source_end_ms=300),
        LocalizationSegment(id="s2", source_text="then we continue", source_start_ms=340, source_end_ms=1600),
    ])
    got = normalize_semantic_cues(tx)
    assert len(got.segments) == 1
    assert got.segments[0].source_start_ms == 0
    assert got.segments[0].source_end_ms == 1600
    assert "and" in got.segments[0].source_text


def test_semantic_normalizer_keeps_short_complete_sentence_for_caption_semantics():
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(id="s1", source_text="Yes.", source_start_ms=0, source_end_ms=480),
        LocalizationSegment(id="s2", source_text="Then we continue.", source_start_ms=500, source_end_ms=1800),
    ])
    got = normalize_semantic_cues(tx)
    # Display/translation cue can remain a complete short sentence; C7 dubbing
    # planner will still merge it into a longer audio chunk.
    assert len(got.segments) == 2


def test_semantic_normalizer_splits_long_segment_from_real_word_times():
    words = [
        {"word": " one", "start": 0.0, "end": 1.0},
        {"word": " two,", "start": 1.0, "end": 2.0},
        {"word": " three", "start": 2.0, "end": 3.0},
        {"word": " four.", "start": 3.0, "end": 4.0},
        {"word": " five", "start": 4.0, "end": 5.0},
        {"word": " six.", "start": 5.0, "end": 6.0},
        {"word": " seven", "start": 6.0, "end": 7.0},
    ]
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(
            id="s1", source_text="one two, three four. five six. seven",
            source_start_ms=0, source_end_ms=7000, source_words=words,
        )
    ])
    got = normalize_semantic_cues(tx, max_cue_duration_ms=4000)
    assert len(got.segments) >= 2
    assert got.segments[0].source_start_ms == 0
    assert got.segments[-1].source_end_ms == 7000
