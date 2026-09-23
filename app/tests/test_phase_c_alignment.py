# SPDX-License-Identifier: AGPL-3.0-only
from backend.core.localization import LocalizationSegment, LocalizationTranscript
from backend.services.alignment_service import _aligned_to_transcript, _assign_speaker


def test_assign_speaker_uses_overlap_duration_and_keeps_secondary_overlap():
    rows = [
        {"start": 0.0, "end": 1.0, "speaker": "A"},
        {"start": 0.65, "end": 1.0, "speaker": "B"},
    ]
    primary, speakers = _assign_speaker(0.0, 1.0, rows)
    assert primary == "A"
    assert speakers == ["A", "B"]


def test_assign_speaker_rejects_tiny_coverage():
    primary, speakers = _assign_speaker(
        0.0,
        10.0,
        [{"start": 9.95, "end": 10.0, "speaker": "A"}],
    )
    assert primary is None
    assert speakers == []


def test_aligned_transcript_preserves_word_times_and_primary_speaker():
    original = LocalizationTranscript(
        text="hello world",
        language="en",
        segments=[
            LocalizationSegment(
                id="raw",
                source_text="hello world",
                source_start_ms=0,
                source_end_ms=1000,
            )
        ],
    )
    got = _aligned_to_transcript(
        original,
        [{
            "start": 0.0,
            "end": 1.0,
            "text": "hello world",
            "words": [
                {"word": "hello", "start": 0.0, "end": 0.4},
                {"word": "world", "start": 0.5, "end": 1.0},
            ],
        }],
        [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
    )
    assert got.source == "whisperx_alignment"
    assert got.segments[0].speaker_id == "SPEAKER_00"
    assert got.segments[0].source_words[0]["speaker_id"] == "SPEAKER_00"
    assert got.segments[0].source_start_ms == 0
    assert got.segments[0].source_end_ms == 1000



def test_torch_checkpoint_compat_sets_weights_only_false_by_default(monkeypatch):
    import torch
    from backend.services import alignment_service as alignment

    seen = {}

    def fake_load(*args, **kwargs):
        seen["weights_only"] = kwargs.get("weights_only")
        return "ok"

    monkeypatch.setattr(torch, "load", fake_load)
    patched = alignment._ensure_torch_checkpoint_compat()
    assert patched.load("checkpoint.pt") == "ok"
    assert seen["weights_only"] is False

    patched.load("checkpoint.pt", weights_only=True)
    assert seen["weights_only"] is True
