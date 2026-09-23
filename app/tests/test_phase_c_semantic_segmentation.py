# SPDX-License-Identifier: AGPL-3.0-only
import pytest

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


class _GroupingAI:
    def __init__(self, response):
        self.response = response

    async def chat(self, messages, **kwargs):
        return self.response


@pytest.mark.asyncio
async def test_ai_refine_only_groups_ids_and_preserves_source_text():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    tx = LocalizationTranscript(segments=[
        LocalizationSegment(id="s1", source_text="Yes.", source_start_ms=0, source_end_ms=480),
        LocalizationSegment(id="s2", source_text="Then we continue.", source_start_ms=500, source_end_ms=1800),
        LocalizationSegment(id="s3", source_text="Next topic.", source_start_ms=3000, source_end_ms=4200),
    ])
    ai = _GroupingAI('[{"ids":["s1","s2"]},{"ids":["s3"]}]')
    got = await refine_semantic_cues_with_ai(tx, ai)
    assert len(got.segments) == 2
    assert got.segments[0].source_text == "Yes. Then we continue."
    assert got.segments[0].source_start_ms == 0
    assert got.segments[0].source_end_ms == 1800


@pytest.mark.asyncio
async def test_ai_refine_invalid_rewrite_shape_falls_back_without_data_loss():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    tx = LocalizationTranscript(segments=[
        LocalizationSegment(id="s1", source_text="one", source_start_ms=0, source_end_ms=1000),
        LocalizationSegment(id="s2", source_text="two", source_start_ms=1100, source_end_ms=2100),
    ])
    ai = _GroupingAI('[{"ids":["s2","s1"]}]')
    got = await refine_semantic_cues_with_ai(tx, ai)
    assert [s.source_text for s in got.segments] == ["one", "two"]


@pytest.mark.asyncio
async def test_ai_word_tier_can_split_inside_raw_asr_segment_without_rewriting():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    words = [
        {"word": "Hello", "start": 0.0, "end": 0.4},
        {"word": "there.", "start": 0.4, "end": 0.9},
        {"word": "Next", "start": 1.1, "end": 1.5},
        {"word": "idea.", "start": 1.5, "end": 2.0},
    ]
    tx = LocalizationTranscript(
        text="Hello there. Next idea.",
        segments=[
            LocalizationSegment(
                id="raw1",
                source_text="Hello there. Next idea.",
                source_start_ms=0,
                source_end_ms=2000,
                source_words=words,
            )
        ],
    )
    ai = _GroupingAI(
        '[{"ids":["w00001","w00002"]},{"ids":["w00003","w00004"]}]'
    )
    got = await refine_semantic_cues_with_ai(tx, ai)
    assert len(got.segments) == 2
    assert got.segments[0].source_text == "Hello there."
    assert got.segments[0].source_start_ms == 0
    assert got.segments[0].source_end_ms == 900
    assert got.segments[1].source_text == "Next idea."
    assert got.segments[1].source_start_ms == 1100
    assert got.segments[1].source_end_ms == 2000


@pytest.mark.asyncio
async def test_ai_word_tier_invalid_ids_falls_back_without_losing_words():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    words = [
        {"word": "one", "start": 0.0, "end": 0.5},
        {"word": "two", "start": 0.5, "end": 1.0},
    ]
    tx = LocalizationTranscript(
        text="one two",
        segments=[
            LocalizationSegment(
                id="raw1", source_text="one two",
                source_start_ms=0, source_end_ms=1000,
                source_words=words,
            )
        ],
    )
    ai = _GroupingAI('[{"ids":["w00002","w00001"]}]')
    got = await refine_semantic_cues_with_ai(tx, ai)
    assert [s.source_text for s in got.segments] == ["one", "two"]
    assert [(s.source_start_ms, s.source_end_ms) for s in got.segments] == [
        (0, 500), (500, 1000)
    ]
