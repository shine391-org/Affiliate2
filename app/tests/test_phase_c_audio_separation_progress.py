# SPDX-License-Identifier: AGPL-3.0-only
"""Demucs progress plumbing: device selection, normalization, and job mapping."""
import asyncio
import sys
import types

import pytest

from backend.services import audio_separation_service as svc


# ── device ───────────────────────────────────────────────────────────────────

def test_device_is_cpu_without_cuda(monkeypatch):
    fake = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake)
    assert svc._device() == "cpu"


def test_device_is_cuda_when_available(monkeypatch):
    fake = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True))
    monkeypatch.setitem(sys.modules, "torch", fake)
    assert svc._device() == "cuda"


# ── callback shape ───────────────────────────────────────────────────────────

class _FakeSeparator:
    samplerate = 44100
    _segment = 7.8
    _overlap = 0.25
    model = types.SimpleNamespace(segment=7.8)


class _FakeBagSeparator:
    """htdemucs_ft is a BagOfModels: `segment` lives on the sub-models."""

    samplerate = 44100
    _segment = None
    _overlap = 0.25

    def __init__(self, segment=7.8, sub_models=4):
        self.model = types.SimpleNamespace(
            models=[types.SimpleNamespace(segment=segment) for _ in range(sub_models)],
        )


def _payload(state, shift, segment, *, audio_length=44100 * 45, models=1, model_idx=0):
    return {
        "state": state,
        "shift_idx": shift,
        "segment_offset": segment,
        "audio_length": audio_length,
        "models": models,
        "model_idx_in_bag": model_idx,
    }


def _segments_for(seconds=45, segment=7.8, overlap=0.25, sr=44100):
    stride = int((1 - overlap) * sr * segment)
    return -(-int(sr * seconds) // stride)


def test_segment_plan_matches_demucs_stride():
    assert svc._segment_plan(_FakeSeparator(), 44100 * 45) == _segments_for()


def test_segment_plan_falls_back_when_model_exposes_no_segment():
    bare = types.SimpleNamespace(samplerate=44100, _segment=None, model=None)
    assert svc._segment_plan(bare, 44100 * 45) == _segments_for()


def test_segment_plan_reads_segment_off_a_bag_of_models():
    """htdemucs_ft is a BagOfModels; the bag itself has no `segment`."""
    bag = _FakeBagSeparator(segment=7.8)
    assert getattr(bag.model, "segment", None) is None
    assert svc._segment_plan(bag, 44100 * 45) == _segments_for()


def test_segment_plan_respects_a_non_default_model_segment():
    bag = _FakeBagSeparator(segment=10.0)
    assert svc._segment_plan(bag, 44100 * 45) == _segments_for(segment=10.0)


def test_bag_of_models_multiplies_the_unit_total():
    """4 sub-models x 3 shifts x segments is the real unit count for htdemucs_ft."""
    bag = _FakeBagSeparator(sub_models=4)
    progress = svc._DemucsProgress(sink=lambda f: None)
    progress.set_separator(bag)
    segments = _segments_for()
    expected = 4 * svc.SHIFTS * segments
    for model_idx in range(4):
        for shift in range(svc.SHIFTS):
            for segment in range(segments):
                progress(_payload(
                    "end", shift, segment, models=4, model_idx=model_idx,
                ))
    assert progress._total_units == expected
    assert progress.fraction == pytest.approx(1.0)


def test_segment_plan_is_zero_for_empty_audio():
    assert svc._segment_plan(_FakeSeparator(), 0) == 0


def test_demucs_callback_normalizes_to_unit_interval():
    total_segments = _segments_for()
    progress = svc._DemucsProgress(sink=lambda f: None)
    progress.set_separator(_FakeSeparator())
    seen = []
    progress.set_sink(seen.append)

    units = [(shift, segment)
             for shift in range(svc.SHIFTS)
             for segment in range(total_segments)]
    for shift, segment in units:
        progress(_payload("start", shift, segment))
        progress(_payload("end", shift, segment))

    assert seen, "callback must report progress during compute"
    assert all(0.0 <= f <= 1.0 for f in seen)
    assert seen == sorted(seen), "progress must be monotonic"
    assert seen[-1] == pytest.approx(1.0)


def test_progress_ignores_start_events_and_bad_payloads():
    progress = svc._DemucsProgress(sink=lambda f: None)
    progress.set_separator(_FakeSeparator())
    seen = []
    progress.set_sink(seen.append)

    progress(_payload("start", 0, 0))
    progress("not a dict")
    progress(None)
    progress({})
    assert progress.fraction == 0.0
    assert seen == []


def test_progress_is_monotonic_under_repeated_units():
    progress = svc._DemucsProgress(sink=lambda f: None)
    progress.set_separator(_FakeSeparator())
    total_segments = _segments_for()
    for shift in range(svc.SHIFTS):
        for segment in range(total_segments):
            progress(_payload("end", shift, segment))
    high = progress.fraction
    # A duplicated/late unit must not walk the fraction back down.
    progress(_payload("end", 0, 0))
    assert progress.fraction == high == pytest.approx(1.0)


def test_progress_resets_unit_numbering_for_a_new_outer_chunk():
    progress = svc._DemucsProgress(sink=lambda f: None)
    progress.set_separator(_FakeSeparator())
    for shift in range(svc.SHIFTS):
        for segment in range(_segments_for()):
            progress(_payload("end", shift, segment))
    assert progress.fraction == pytest.approx(1.0)
    # Second outer chunk reuses (shift, segment) indices from zero.
    progress.set_sink(lambda f: None)
    for shift in range(svc.SHIFTS):
        for segment in range(_segments_for()):
            progress(_payload("end", shift, segment))
    assert progress.fraction == pytest.approx(1.0)


def test_sink_exception_never_breaks_the_callback():
    def boom(_fraction):
        raise RuntimeError("progress consumer exploded")

    progress = svc._DemucsProgress(sink=boom)
    progress.set_separator(_FakeSeparator())
    total_segments = _segments_for()
    for shift in range(svc.SHIFTS):
        for segment in range(total_segments):
            progress(_payload("end", shift, segment))
    assert progress.fraction == pytest.approx(1.0)


def test_emit_throttles_but_always_reports_completion():
    seen = []
    progress = svc._DemucsProgress(sink=seen.append)
    progress.set_separator(_FakeSeparator())
    progress.set_sink(seen.append)
    progress._high = 0.5
    progress.emit(force=True)
    assert seen[-1] == pytest.approx(0.5)
    # A repeat inside the min interval / min delta is suppressed.
    progress.emit()
    assert len(seen) == 1
    # Completion is never suppressed.
    progress._high = 1.0
    progress.emit(force=True)
    assert seen[-1] == pytest.approx(1.0)


def test_emit_with_no_sink_is_a_no_op():
    progress = svc._DemucsProgress()
    progress._high = 1.0
    progress.emit(force=True)  # must not raise


def test_throttle_tracks_value_and_time_separately():
    """Regression: one counter was serving as both, so a mid-run fraction was
    compared against a wall-clock timestamp and always looked like no change,
    collapsing the whole stage to a single 6% then 15%."""
    seen = []
    progress = svc._DemucsProgress(sink=seen.append)
    progress.set_separator(_FakeSeparator())
    segments = _segments_for()
    for shift in range(svc.SHIFTS):
        for segment in range(segments):
            progress(_payload("end", shift, segment))
    # A whole stage that completes inside the min interval must still report
    # intermediate movement, not jump straight from 0 to 1.
    assert len(seen) >= 2
    assert any(0.0 < f < 1.0 for f in seen)
    assert seen == sorted(seen)
    assert seen[-1] == pytest.approx(1.0)


def test_throttle_respects_the_wall_clock_gap(monkeypatch):
    seen = []
    progress = svc._DemucsProgress(sink=seen.append)
    progress.set_separator(_FakeSeparator())
    clock = {"t": 1000.0}
    monkeypatch.setattr(svc.time, "monotonic", lambda: clock["t"])
    progress._high = 0.1
    progress.emit(force=True)
    progress._high = 0.9  # big delta, but no time has passed
    progress.emit()
    assert len(seen) == 1
    clock["t"] += svc.PROGRESS_MIN_INTERVAL_SECONDS + 0.01
    progress.emit()
    assert len(seen) == 2
    assert seen[-1] == pytest.approx(0.9)


# ── job mapping ──────────────────────────────────────────────────────────────

def test_separation_maps_onto_the_six_to_fifteen_window():
    from backend.core.tool_runners import (
        SEPARATION_PROGRESS_END,
        SEPARATION_PROGRESS_START,
        separation_job_progress,
    )

    assert separation_job_progress(0.0) == SEPARATION_PROGRESS_START == 6
    assert separation_job_progress(0.25) == pytest.approx(8.25)
    assert separation_job_progress(0.5) == pytest.approx(10.5)
    assert separation_job_progress(0.75) == pytest.approx(12.75)
    assert separation_job_progress(1.0) == SEPARATION_PROGRESS_END == 15


def test_separation_progress_never_reaches_the_asr_start():
    from backend.core.tool_runners import separation_job_progress

    for value in (1.0, 1.5, 99.0, float("inf"), float("nan")):
        assert separation_job_progress(value) < 16


def test_separation_progress_tolerates_garbage():
    from backend.core.tool_runners import separation_job_progress

    assert separation_job_progress(None) == 6
    assert separation_job_progress("nope") == 6
    assert separation_job_progress(-3) == 6


def test_separation_step_text_reports_the_fraction():
    from backend.core.tool_runners import separation_step_text

    assert "42%" in separation_step_text(0.42)
    assert "100%" in separation_step_text(1.0)


def test_forwarder_never_walks_job_progress_backwards():
    from backend.core.tool_runners import (
        SEPARATION_PROGRESS_START,
        _separation_progress_forwarder,
    )

    emitted = []

    async def run():
        previous = [SEPARATION_PROGRESS_START]
        forward = _separation_progress_forwarder("job", "user", previous)
        for value in (0.0, 0.5, 0.2, 0.9, 1.0):
            forward(value)
        await asyncio.sleep(0)
        return previous

    previous = asyncio.run(run())
    assert previous[0] >= SEPARATION_PROGRESS_START
    assert previous[0] <= 15


def test_progress_callback_omitted_preserves_legacy_call(monkeypatch):
    """Callers that pass no callback must behave exactly as before."""
    calls = []

    def fake_sync(source, work_dir, **kwargs):
        calls.append((source, work_dir, kwargs))
        return "vocals", "bgm"

    monkeypatch.setattr(svc, "_separate_sync", fake_sync)
    result = asyncio.run(svc.separate_vocals_background("src", "dir"))
    assert result == ("vocals", "bgm")
    assert calls[0][2] == {}


def test_progress_callback_is_forwarded_when_supplied(monkeypatch):
    seen = {}

    def fake_sync(source, work_dir, **kwargs):
        seen.update(kwargs)
        return "vocals", "bgm"

    monkeypatch.setattr(svc, "_separate_sync", fake_sync)
    sink = lambda f: None  # noqa: E731
    asyncio.run(svc.separate_vocals_background("src", "dir", on_progress=sink))
    assert seen["on_progress"] is sink
