# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Task B regressions: zero-timestamp grouping invariance + ASR executor provenance.

Two independent defects are pinned here.

1. ``_deterministic_word_groups`` treated a group start of 0 as "no group
   start yet" (``group_start or word["start_ms"]``). A transcript whose first
   word starts at exactly 0 ms therefore measured every word's elapsed time
   against ITS OWN start, so a 10-second single-speaker cue never reached the
   6 s boundary and collapsed into one group. Shift the same timeline by 1 ms
   and the same words split 6 + 4. The grouping must be invariant to a uniform
   timeline shift, including to 0.

2. Whisper could silently swap a CUDA model for a CPU/int8 one mid-flight
   (constructor failure or a lazy CTranslate2 failure during generator
   iteration). Nothing that travelled with the transcript recorded WHICH
   executor actually produced it, so a manifest could claim a device that was
   never used. ``transcribe()`` now returns explicit executor provenance with
   the model instance that really decoded the audio, and a fallback is only
   reported as executed after the retry actually ran.

These tests stub faster_whisper and never load a real model.
"""
import asyncio
import sys
import types

import pytest

from backend.core.localization import LocalizationSegment, LocalizationTranscript
from backend.services import whisper_service as ws_mod
from backend.services.semantic_segmentation import refine_semantic_cues_with_ai
from backend.services.whisper_service import WhisperService


# ── helpers ────────────────────────────────────────────────────────────────

def _word_timeline(offset_ms: int, count: int = 10, step_ms: int = 1000):
    """``count`` one-second words starting at ``offset_ms``."""
    words = []
    for index in range(count):
        start = (offset_ms + index * step_ms) / 1000.0
        end = (offset_ms + (index + 1) * step_ms) / 1000.0
        words.append({"word": f" w{index + 1}", "start": start, "end": end})
    return words


def _transcript(offset_ms: int, count: int = 10) -> LocalizationTranscript:
    words = _word_timeline(offset_ms, count=count)
    text = " ".join(f"w{i + 1}" for i in range(count))
    return LocalizationTranscript(
        text=text,
        segments=[
            LocalizationSegment(
                id="raw1",
                source_text=text,
                source_start_ms=offset_ms,
                source_end_ms=offset_ms + count * 1000,
                source_words=words,
            )
        ],
    )


async def _deterministic_segments(offset_ms: int, count: int = 10):
    """Run the word tier with no AI available so grouping is deterministic."""
    got = await refine_semantic_cues_with_ai(_transcript(offset_ms, count), None)
    return got.segments


# ── 1. zero-timestamp grouping invariance ──────────────────────────────────

@pytest.mark.asyncio
async def test_grouping_is_invariant_when_first_word_starts_at_zero():
    """The RED for the ``group_start or word["start_ms"]`` falsey bug.

    Identical 10 x 1 s timeline; the only difference is the absolute start.
    Grouping must not depend on it.
    """
    at_zero = await _deterministic_segments(0)
    at_one_ms = await _deterministic_segments(1)

    def shape(segments, offset):
        """Group sizes and offsets RELATIVE to the timeline start.

        Absolute timestamps legitimately carry the 1 ms shift; the grouping
        decision must not depend on it.
        """
        return [
            (
                s.source_text,
                s.source_start_ms - offset,
                s.source_end_ms - offset,
            )
            for s in segments
        ]

    assert shape(at_zero, 0) == shape(at_one_ms, 1)
    # And the split that already worked at +1 ms must also happen at 0.
    assert len(at_zero) > 1


@pytest.mark.asyncio
async def test_zero_start_grouping_splits_on_the_six_second_boundary():
    segments = await _deterministic_segments(0)
    assert segments[0].source_start_ms == 0
    assert segments[0].source_end_ms == 6000
    assert segments[-1].source_end_ms == 10000


@pytest.mark.asyncio
async def test_zero_start_grouping_keeps_every_word_exactly_once():
    segments = await _deterministic_segments(0)
    joined = " ".join(s.source_text for s in segments)
    for index in range(10):
        assert f"w{index + 1}" in joined
    assert joined.count("w") == 10
    # Monotonic, non-overlapping timeline.
    for prev, seg in zip(segments, segments[1:]):
        assert seg.source_start_ms >= prev.source_end_ms


@pytest.mark.asyncio
async def test_grouping_still_respects_strong_punctuation_at_zero_start():
    """A sentence end must break the group even well before the duration cap."""
    words = [
        {"word": " Hello", "start": 0.0, "end": 0.4},
        {"word": " there.", "start": 0.4, "end": 0.9},
        {"word": " Next", "start": 1.0, "end": 1.4},
        {"word": " idea.", "start": 1.4, "end": 1.9},
    ]
    tx = LocalizationTranscript(
        text="Hello there. Next idea.",
        segments=[
            LocalizationSegment(
                id="raw1",
                source_text="Hello there. Next idea.",
                source_start_ms=0,
                source_end_ms=1900,
                source_words=words,
            )
        ],
    )
    got = await refine_semantic_cues_with_ai(tx, None)
    assert [s.source_text for s in got.segments] == ["Hello there.", "Next idea."]


@pytest.mark.asyncio
async def test_grouping_keeps_speaker_boundary_at_zero_start():
    words = [
        {"word": " A", "start": 0.0, "end": 1.0, "speaker_id": "spk-1"},
        {"word": " one", "start": 1.0, "end": 2.0, "speaker_id": "spk-1"},
        {"word": " B", "start": 2.0, "end": 3.0, "speaker_id": "spk-2"},
        {"word": " two", "start": 3.0, "end": 4.0, "speaker_id": "spk-2"},
    ]
    tx = LocalizationTranscript(
        text="A one B two",
        segments=[
            LocalizationSegment(
                id="raw1",
                source_text="A one B two",
                source_start_ms=0,
                source_end_ms=4000,
                source_words=words,
            )
        ],
    )
    got = await refine_semantic_cues_with_ai(tx, None)
    speakers = [s.speaker_id for s in got.segments]
    assert speakers == ["spk-1", "spk-2"]


# ── 2. ASR executor provenance ─────────────────────────────────────────────

class _ExecutorModel:
    """Stands in for faster_whisper.WhisperModel and records its executor."""

    def __init__(self, name, device=None, compute_type=None):
        self.name = name
        self.device = device
        self.compute_type = compute_type
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append(kwargs)
        seg = types.SimpleNamespace(
            start=0.0, end=1.0, text=" ok ",
            words=[types.SimpleNamespace(word="ok", start=0.0, end=1.0, probability=0.9)],
        )
        info = types.SimpleNamespace(language="en", language_probability=0.99)
        return iter([seg]), info


class _LazyCudaFailureModel(_ExecutorModel):
    """CUDA model that fails while the segment generator is iterated."""

    def transcribe(self, path, **kwargs):
        if self.device == "cuda":
            def fail():
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                yield  # pragma: no cover
            return fail(), types.SimpleNamespace(language="en", language_probability=0.99)
        return super().transcribe(path, **kwargs)


@pytest.fixture(autouse=True)
def _isolated_whisper_singleton(monkeypatch):
    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = _ExecutorModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *_a, **_k: 1.0)

    async def _has_audio(_path):
        return True
    monkeypatch.setattr("backend.services.ffmpeg_service.has_audio_stream", _has_audio)

    def _reset():
        WhisperService._model = None
        WhisperService._loaded_quality = None
        WhisperService._loaded_model_name = None
        WhisperService._loaded_device = None
        WhisperService._loaded_compute_type = None
        WhisperService._evict_task = None
        WhisperService._active_transcriptions = 0

    _reset()
    yield
    _reset()


def _audio(tmp_path):
    path = tmp_path / "a.wav"
    path.write_bytes(b"x")
    return path


def test_transcribe_reports_the_actual_cpu_executor(tmp_path, monkeypatch):
    monkeypatch.setattr(WhisperService, "_select_device", classmethod(lambda cls: ("cpu", "int8")))

    result = asyncio.run(ws_mod.whisper_service.transcribe(_audio(tmp_path), quality="fast"))

    provenance = result["asr_provenance"]
    assert provenance["model"] == "base"
    assert provenance["device"] == "cpu"
    assert provenance["compute_type"] == "int8"
    assert provenance["fallback_used"] is False
    assert provenance["fallback_reason"] is None


def test_transcribe_reports_cuda_executor_when_it_really_ran(tmp_path, monkeypatch):
    monkeypatch.setattr(WhisperService, "_select_device", classmethod(lambda cls: ("cuda", "float16")))

    result = asyncio.run(ws_mod.whisper_service.transcribe(_audio(tmp_path), quality="fast"))

    provenance = result["asr_provenance"]
    assert provenance["device"] == "cuda"
    assert provenance["compute_type"] == "float16"
    assert provenance["fallback_used"] is False


def test_transcribe_reports_lazy_cuda_fallback_only_after_it_executed(tmp_path, monkeypatch):
    """The device that produced this transcript is CPU, not the requested CUDA."""
    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = _LazyCudaFailureModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(WhisperService, "_select_device", classmethod(lambda cls: ("cuda", "float16")))

    result = asyncio.run(ws_mod.whisper_service.transcribe(_audio(tmp_path), quality="fast"))

    assert result["text"] == "ok"
    provenance = result["asr_provenance"]
    assert provenance["requested_device"] == "cuda"
    assert provenance["device"] == "cpu"
    assert provenance["compute_type"] == "int8"
    assert provenance["fallback_used"] is True
    assert "cublas" in provenance["fallback_reason"]
    # The singleton now agrees with what was actually executed.
    assert WhisperService._loaded_device == "cpu"
    assert WhisperService._loaded_compute_type == "int8"


def test_transcribe_does_not_claim_a_fallback_that_never_ran(tmp_path, monkeypatch):
    """A non-CUDA failure must raise, with no provenance inventing CPU."""
    class BrokenModel(_ExecutorModel):
        def transcribe(self, path, **kwargs):
            def fail():
                raise ValueError("invalid audio frame")
                yield  # pragma: no cover
            return fail(), types.SimpleNamespace(language="en", language_probability=0.99)

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = BrokenModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(WhisperService, "_select_device", classmethod(lambda cls: ("cuda", "float16")))

    with pytest.raises(ValueError):
        asyncio.run(ws_mod.whisper_service.transcribe(_audio(tmp_path), quality="fast"))

    # Nothing was reported because nothing completed; singleton state is honest.
    assert WhisperService._loaded_device == "cuda"
    assert WhisperService._loaded_compute_type == "float16"


def test_transcribe_result_keeps_its_public_contract(tmp_path, monkeypatch):
    """Provenance is additive: existing consumers must be unaffected."""
    monkeypatch.setattr(WhisperService, "_select_device", classmethod(lambda cls: ("cpu", "int8")))

    result = asyncio.run(ws_mod.whisper_service.transcribe(_audio(tmp_path), quality="fast"))

    assert set(result) >= {"text", "language", "language_probability", "segments", "asr_provenance"}
    assert result["language"] == "en"
    assert result["segments"][0]["start"] == 0.0
    assert result["segments"][0]["words"][0]["word"] == "ok"


def test_load_with_device_keeps_its_two_value_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(WhisperService, "_select_device", classmethod(lambda cls: ("cpu", "int8")))

    model, device = WhisperService.load_with_device("fast")

    assert model.name == "base"
    assert device == "cpu"
    model3, info = WhisperService.load_with_provenance("fast")
    assert model3 is model
    assert info["device"] == "cpu"
    assert info["compute_type"] == "int8"
    assert info["model"] == "base"
    assert info["requested_device"] == "cpu"
    assert info["fallback_used"] is False


def test_unload_clears_executor_state(tmp_path, monkeypatch):
    monkeypatch.setattr(WhisperService, "_select_device", classmethod(lambda cls: ("cpu", "int8")))
    WhisperService.load("fast")
    assert WhisperService._loaded_compute_type == "int8"
    assert WhisperService.unload() is True
    assert WhisperService._loaded_device is None
    assert WhisperService._loaded_compute_type is None


def test_provenance_never_contains_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setattr(WhisperService, "_select_device", classmethod(lambda cls: ("cpu", "int8")))

    result = asyncio.run(ws_mod.whisper_service.transcribe(_audio(tmp_path), quality="fast"))

    provenance = result["asr_provenance"]
    assert set(provenance) <= {
        "model", "device", "compute_type", "requested_device",
        "fallback_used", "fallback_reason", "timing_only",
    }