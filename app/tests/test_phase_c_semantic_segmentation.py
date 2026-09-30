# SPDX-License-Identifier: AGPL-3.0-only
import pytest
from types import SimpleNamespace

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
    # Invalid word-tier grouping must not degrade translation into one-word
    # cues. Fall back to the already-normalized semantic cue tier instead.
    assert [s.source_text for s in got.segments] == ["one two"]
    assert [(s.source_start_ms, s.source_end_ms) for s in got.segments] == [
        (0, 1000)
    ]



@pytest.mark.asyncio
async def test_word_level_speaker_id_survives_semantic_segmentation():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    words = [
        {"word": "hello", "start": 0.0, "end": 0.4, "speaker_id": "A"},
        {"word": "there", "start": 0.4, "end": 0.8, "speaker_id": "A"},
        {"word": "yes", "start": 0.9, "end": 1.2, "speaker_id": "B"},
        {"word": "indeed", "start": 1.2, "end": 1.6, "speaker_id": "B"},
    ]
    tx = LocalizationTranscript(
        text="hello there yes indeed",
        segments=[
            LocalizationSegment(
                id="raw1",
                speaker_id="A",
                source_text="hello there yes indeed",
                source_start_ms=0,
                source_end_ms=1600,
                source_words=words,
            )
        ],
    )
    ai = _GroupingAI(
        '[{"ids":["w00001","w00002"]},{"ids":["w00003","w00004"]}]'
    )
    got = await refine_semantic_cues_with_ai(tx, ai)
    assert [s.speaker_id for s in got.segments] == ["A", "B"]
    assert [s.source_text for s in got.segments] == ["hello there", "yes indeed"]



@pytest.mark.asyncio
async def test_word_tier_preserves_whisper_spacing_for_english_and_cjk():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    en = LocalizationTranscript(
        text="Hello world",
        segments=[LocalizationSegment(
            id="en", source_text="Hello world",
            source_start_ms=0, source_end_ms=800,
            source_words=[
                {"word": "Hello", "start": 0.0, "end": 0.3},
                {"word": " world", "start": 0.3, "end": 0.8},
            ],
        )],
    )
    got_en = await refine_semantic_cues_with_ai(
        en, _GroupingAI('[{"ids":["w00001","w00002"]}]')
    )
    assert got_en.segments[0].source_text == "Hello world"

    zh = LocalizationTranscript(
        text="你好世界",
        language="zh",
        segments=[LocalizationSegment(
            id="zh", source_text="你好世界",
            source_start_ms=0, source_end_ms=800,
            source_words=[
                {"word": "你", "start": 0.0, "end": 0.2},
                {"word": "好", "start": 0.2, "end": 0.4},
                {"word": "世", "start": 0.4, "end": 0.6},
                {"word": "界", "start": 0.6, "end": 0.8},
            ],
        )],
    )
    got_zh = await refine_semantic_cues_with_ai(
        zh, _GroupingAI('[{"ids":["w00001","w00002","w00003","w00004"]}]')
    )
    assert got_zh.segments[0].source_text == "你好世界"



@pytest.mark.asyncio
async def test_invalid_ai_grouping_still_preserves_word_speaker_boundary():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    tx = LocalizationTranscript(
        text="hello there yes indeed",
        segments=[LocalizationSegment(
            id="raw1",
            speaker_id="A",
            source_text="hello there yes indeed",
            source_start_ms=0,
            source_end_ms=1600,
            source_words=[
                {"word": "hello", "start": 0.0, "end": 0.4, "speaker_id": "A"},
                {"word": " there", "start": 0.4, "end": 0.8, "speaker_id": "A"},
                {"word": " yes", "start": 0.9, "end": 1.2, "speaker_id": "B"},
                {"word": " indeed", "start": 1.2, "end": 1.6, "speaker_id": "B"},
            ],
        )],
    )
    # Invalid order forces deterministic fallback.
    got = await refine_semantic_cues_with_ai(
        tx,
        _GroupingAI('[{"ids":["w00003","w00001","w00002","w00004"]}]'),
    )
    assert [s.speaker_id for s in got.segments] == ["A", "B"]
    assert [s.source_text for s in got.segments] == ["hello there", "yes indeed"]

@pytest.mark.asyncio
async def test_word_tier_provider_failure_opens_stage_circuit_breaker():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    words = [
        {
            "word": f" w{i}",
            "start": i * 0.1,
            "end": (i + 1) * 0.1,
        }
        for i in range(121)
    ]
    text = " ".join(f"w{i}" for i in range(121))
    tx = LocalizationTranscript(
        text=text,
        segments=[LocalizationSegment(
            id="raw1",
            source_text=text,
            source_start_ms=0,
            source_end_ms=12100,
            source_words=words,
        )],
    )

    class FailingAI:
        def __init__(self):
            self.calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError("semantic provider unavailable")

    ai = FailingAI()
    got = await refine_semantic_cues_with_ai(tx, ai)

    assert ai.calls == 1
    assert got.segments
    assert "w0" in got.segments[0].source_text
    assert "w120" in got.segments[-1].source_text


@pytest.mark.asyncio
async def test_cue_tier_provider_failure_skips_remaining_ai_windows():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    tx = LocalizationTranscript(
        text=" ".join(f"Cue {i}." for i in range(6)),
        segments=[
            LocalizationSegment(
                id=f"s{i}",
                source_text=f"Cue {i}.",
                source_start_ms=i * 1200,
                source_end_ms=i * 1200 + 1000,
            )
            for i in range(6)
        ],
    )

    class FailingAI:
        def __init__(self):
            self.calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            raise ConnectionError("semantic provider unavailable")

    ai = FailingAI()
    got = await refine_semantic_cues_with_ai(tx, ai, window_size=2)

    assert ai.calls == 1
    assert [seg.source_text for seg in got.segments] == [
        f"Cue {i}." for i in range(6)
    ]

@pytest.mark.asyncio
async def test_invalid_semantic_response_does_not_trip_provider_breaker():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    tx = LocalizationTranscript(
        text="One. Two. Three. Four.",
        segments=[
            LocalizationSegment(
                id=f"s{i}",
                source_text=f"Cue {i}.",
                source_start_ms=i * 1200,
                source_end_ms=i * 1200 + 1000,
            )
            for i in range(4)
        ],
    )

    class RecoveringAI:
        def __init__(self):
            self.calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return '[{"ids":["s1","s0"]}]'
            return '[{"ids":["s2","s3"]}]'

    ai = RecoveringAI()
    got = await refine_semantic_cues_with_ai(tx, ai, window_size=2)

    assert ai.calls == 2
    assert [seg.source_text for seg in got.segments] == [
        "Cue 0.",
        "Cue 1.",
        "Cue 2. Cue 3.",
    ]




@pytest.mark.asyncio
async def test_semantic_primary_failure_uses_fallback_before_deterministic():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    tx = LocalizationTranscript(
        text="hello there yes indeed",
        segments=[LocalizationSegment(
            id="raw1",
            source_text="hello there yes indeed",
            source_start_ms=0,
            source_end_ms=1600,
            source_words=[
                {"word": "hello", "start": 0.0, "end": 0.4},
                {"word": " there", "start": 0.4, "end": 0.8},
                {"word": " yes", "start": 0.9, "end": 1.2},
                {"word": " indeed", "start": 1.2, "end": 1.6},
            ],
        )],
    )

    class Primary:
        connection_name = "hermes"
        calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError("proxy timeout")

    class Fallback:
        connection_name = "lmstudio"
        calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            return '[{"ids":["w00001","w00002","w00003","w00004"]}]'

    primary = Primary()
    fallback = Fallback()
    events = []

    got = await refine_semantic_cues_with_ai(
        tx,
        primary,
        fallback_ai=fallback,
        max_attempts=1,
        attempt_timeout_s=0.1,
        total_timeout_s=1,
        progress_cb=lambda event: events.append(dict(event)),
    )

    assert primary.calls == 1
    assert fallback.calls == 1
    assert got.segments[0].source_text == "hello there yes indeed"
    assert any(e.get("role") == "fallback" and e.get("event") == "attempt" for e in events)
    assert not any(e.get("event") == "deterministic_fallback" for e in events)


@pytest.mark.asyncio
async def test_semantic_hanging_provider_hits_budget_and_finishes_deterministically():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    tx = LocalizationTranscript(
        text="one two three",
        segments=[LocalizationSegment(
            id="raw1",
            source_text="one two three",
            source_start_ms=0,
            source_end_ms=1200,
            source_words=[
                {"word": "one", "start": 0.0, "end": 0.3},
                {"word": " two", "start": 0.3, "end": 0.7},
                {"word": " three", "start": 0.7, "end": 1.2},
            ],
        )],
    )

    class Hanging:
        connection_name = "hermes"

        async def chat(self, *args, **kwargs):
            await __import__("asyncio").Event().wait()

    events = []
    got = await refine_semantic_cues_with_ai(
        tx,
        Hanging(),
        max_attempts=1,
        attempt_timeout_s=0.01,
        total_timeout_s=0.02,
        progress_cb=lambda event: events.append(dict(event)),
    )

    assert got.segments
    assert "one" in got.segments[0].source_text
    assert any(e.get("event") == "deterministic_fallback" for e in events)



@pytest.mark.asyncio
async def test_semantic_hanging_primary_reserves_time_for_fallback():
    import asyncio
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    tx = LocalizationTranscript(
        text="one two three",
        segments=[LocalizationSegment(
            id="raw1",
            source_text="one two three",
            source_start_ms=0,
            source_end_ms=1200,
            source_words=[
                {"word": "one", "start": 0.0, "end": 0.3},
                {"word": " two", "start": 0.3, "end": 0.7},
                {"word": " three", "start": 0.7, "end": 1.2},
            ],
        )],
    )

    class Hanging:
        connection_name = "hermes"
        async def chat(self, *args, **kwargs):
            await asyncio.Event().wait()

    class Fallback:
        connection_name = "lmstudio"
        calls = 0
        async def chat(self, *args, **kwargs):
            self.calls += 1
            return '[{"ids":["w00001","w00002","w00003"]}]'

    fallback = Fallback()
    events = []
    got = await refine_semantic_cues_with_ai(
        tx,
        Hanging(),
        fallback_ai=fallback,
        max_attempts=2,
        attempt_timeout_s=0.05,
        total_timeout_s=0.08,
        progress_cb=lambda event: events.append(dict(event)),
    )

    assert fallback.calls == 1
    assert got.segments[0].source_text == "one two three"
    assert any(
        e.get("event") == "attempt" and e.get("role") == "fallback"
        for e in events
    )



@pytest.mark.asyncio
async def test_semantic_invalid_structured_primary_uses_next_route():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    tx = LocalizationTranscript(
        text="one two three",
        segments=[LocalizationSegment(
            id="raw1",
            source_text="one two three",
            source_start_ms=0,
            source_end_ms=1200,
            source_words=[
                {"word": "one", "start": 0.0, "end": 0.3},
                {"word": " two", "start": 0.3, "end": 0.7},
                {"word": " three", "start": 0.7, "end": 1.2},
            ],
        )],
    )

    class BadPrimary:
        connection_name = "hermes-main"
        provider = SimpleNamespace(value="compatible")
        model = "longcat"
        calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            return '[{"ids":["w00003","w00001","w00002"]}]'

    class GoodFallback:
        connection_name = "lmstudio-qwen"
        provider = SimpleNamespace(value="compatible")
        model = "qwen3:8b"
        calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            return '[{"ids":["w00001","w00002","w00003"]}]'

    primary = BadPrimary()
    fallback = GoodFallback()
    state = {"sticky_index": 0, "failure_chain": []}
    events = []

    got = await refine_semantic_cues_with_ai(
        tx,
        primary,
        fallback_ai=fallback,
        route_clients=[primary, fallback],
        route_state=state,
        max_attempts=1,
        attempt_timeout_s=0.5,
        total_timeout_s=2,
        progress_cb=lambda event: events.append(dict(event)),
    )

    assert primary.calls == 1
    assert fallback.calls == 1
    assert state["sticky_index"] == 1
    assert got.segments[0].source_text == "one two three"
    assert any(
        item.get("error_type") == "InvalidStructuredOutput"
        and item.get("connection_id") == "hermes-main"
        for item in state["failure_chain"]
    )
    assert any(
        event.get("event") == "switch_fallback"
        and event.get("provider") == "lmstudio-qwen"
        for event in events
    )


@pytest.mark.asyncio
async def test_semantic_invalid_structured_single_route_stays_deterministic():
    from backend.services.semantic_segmentation import refine_semantic_cues_with_ai

    tx = LocalizationTranscript(
        text="one two three",
        segments=[LocalizationSegment(
            id="raw1",
            source_text="one two three",
            source_start_ms=0,
            source_end_ms=1200,
            source_words=[
                {"word": "one", "start": 0.0, "end": 0.3},
                {"word": " two", "start": 0.3, "end": 0.7},
                {"word": " three", "start": 0.7, "end": 1.2},
            ],
        )],
    )

    class BadOnly:
        connection_name = "only"
        calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            return '{"bad":"shape"}'

    ai = BadOnly()
    state = {"sticky_index": 0, "failure_chain": []}
    got = await refine_semantic_cues_with_ai(
        tx,
        ai,
        route_clients=[ai],
        route_state=state,
        max_attempts=1,
        attempt_timeout_s=0.5,
        total_timeout_s=2,
    )

    assert ai.calls == 1
    assert got.segments
    assert "one" in got.segments[0].source_text


def test_semantic_failure_history_is_bounded_to_recent_events():
    from backend.services import semantic_segmentation as sem

    state = {"sticky_index": 0, "failure_chain": []}
    for index in range(100):
        sem._record_semantic_failure(state, {
            "connection_id": "semantic",
            "attempt": index,
            "error": f"failure-{index}",
        })

    assert len(state["failure_chain"]) == sem._MAX_SEMANTIC_FAILURE_HISTORY
    assert state["failure_chain"][0]["attempt"] == 36
    assert state["failure_chain"][-1]["attempt"] == 99
