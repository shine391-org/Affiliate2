# SPDX-License-Identifier: AGPL-3.0-only
import pytest

from backend.core.localization import (
    LocalizationSegment,
    localization_segments_from_source,
    localization_transcript_from_source,
)
from backend.services.localization_service import LocalizationTimingError


def test_whisper_segments_become_explicit_source_coordinates():
    raw = [{
        "start": 1.25,
        "end": 2.75,
        "text": "hello",
        "words": [{"word": "hello", "start": 1.25, "end": 1.8}],
    }]
    seg = localization_segments_from_source(raw)[0]
    assert seg.source_start_ms == 1250
    assert seg.source_end_ms == 2750
    assert seg.target_start_ms is None
    assert seg.target_end_ms is None
    assert seg.source_text == "hello"
    assert seg.source_words == raw[0]["words"]


def test_target_timeline_is_independent_from_source_timeline():
    seg = LocalizationSegment(
        id="s1",
        source_text="source",
        translated_text="dịch",
        source_start_ms=1000,
        source_end_ms=2000,
        target_start_ms=1100,
        target_end_ms=2300,
        tts_duration_ms=1200,
        translation_status="success",
        timing_status="adjusted",
    )
    assert (seg.source_start_ms, seg.source_end_ms) == (1000, 2000)
    assert (seg.target_start_ms, seg.target_end_ms) == (1100, 2300)


def test_half_defined_target_timeline_is_rejected():
    with pytest.raises(ValueError):
        LocalizationSegment(
            id="s1",
            source_text="x",
            source_start_ms=0,
            source_end_ms=1000,
            target_start_ms=0,
        )


def test_source_end_before_start_is_rejected():
    with pytest.raises(ValueError):
        localization_segments_from_source([
            {"start": 2.0, "end": 1.0, "text": "bad"}
        ])


def test_already_canonical_segment_round_trips():
    raw = {
        "id": "s1",
        "source_text": "x",
        "source_start_ms": 0,
        "source_end_ms": 1000,
        "translation_status": "pending",
        "timing_status": "pending",
        "qc_status": "pending",
    }
    got = localization_segments_from_source([raw])[0]
    assert got.id == "s1"
    assert got.source_end_ms == 1000


def test_transcript_wraps_same_source_segments_without_retranscribing():
    tx = localization_transcript_from_source(
        text="hello world",
        language="en",
        source="whisper",
        raw_segments=[
            {"start": 0, "end": 1, "text": "hello"},
            {"start": 1, "end": 2, "text": "world"},
        ],
    )
    assert tx.text == "hello world"
    assert tx.language == "en"
    assert [s.id for s in tx.segments] == ["seg-0001", "seg-0002"]


@pytest.mark.parametrize("bad", [None, "bad", -0.1, float("nan"), float("inf")])
def test_invalid_source_timestamp_fails_instead_of_becoming_zero(bad):
    with pytest.raises(ValueError, match="Invalid source timestamp"):
        localization_segments_from_source([
            {"start": bad, "end": 1.0, "text": "bad timestamp"}
        ])



def test_timing_error_preserves_c7_rewrite_audit():
    unresolved = [{
        "chunk_id": 7,
        "reason": "timing_overflow",
        "required_speed": 1.49,
        "speed_max": 1.30,
    }]
    audit = [{
        "chunk_id": 7,
        "reason": "rewrite_not_shorter",
    }, *unresolved]

    error = LocalizationTimingError(
        unresolved,
        rewrite_count=2,
        all_issues=audit,
    )

    assert error.rewrite_count == 2
    assert error.issues == unresolved
    assert error.all_issues == audit
    assert "chunk 7: speed 1.49x" in str(error)
