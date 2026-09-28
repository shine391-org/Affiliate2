# SPDX-License-Identifier: AGPL-3.0-only
"""HQ vocal/background separation adapted from YouDub-webui (Apache-2.0).

The stage uses Demucs htdemucs_ft, chunked processing with overlap/crossfade,
and atomic publication of vocals/background artifacts. Affiliate2 imports the
PyPI Demucs package rather than YouDub's repository submodule.
"""
from __future__ import annotations

import asyncio
import math
import os
import subprocess
import threading
import time
from pathlib import Path

CHUNK_SECONDS = 600
OVERLAP_SECONDS = 10
SHIFTS = 3
# Demucs splits audio into ~7.8s segments with 25% overlap. The model exposes
# the real values, this is only the fallback when they are unavailable.
FALLBACK_SEGMENT_SECONDS = 7.8
FALLBACK_OVERLAP = 0.25
# Progress is bridged to the UI as job percent. A 43.5s HQ source runs 96
# Demucs units (4 sub-models x 3 shifts x 8 segments) in roughly 20s, so
# unthrottled callbacks would flood the websocket with ~96 messages.
PROGRESS_MIN_INTERVAL_SECONDS = 0.5
PROGRESS_MIN_DELTA = 0.01


def _device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _cuda_memory_mb() -> float | None:
    """Currently allocated CUDA memory in MiB, or None off-GPU."""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        return torch.cuda.memory_allocated() / (1024 * 1024)
    except Exception:
        return None


def _model_segment_seconds(separator) -> float:
    """Read the authoritative segment length off the separator's model.

    `htdemucs_ft` is a BagOfModels, so the attribute lives on the sub-models
    rather than the bag — reading only `model.segment` silently fell through
    to the fallback and mis-sized the unit count.
    """
    candidates = []
    model = getattr(separator, "model", None)
    if model is not None:
        candidates.append(model)
        candidates.extend(getattr(model, "models", []) or [])
    for candidate in candidates:
        value = getattr(candidate, "segment", None)
        if value:
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return FALLBACK_SEGMENT_SECONDS


def _segment_plan(separator, audio_length: int) -> int:
    """How many inner segments Demucs will run for `audio_length` frames.

    Mirrors demucs.apply.apply_model's `split` branch: the audio is walked in
    strides of `(1 - overlap) * segment_length` frames. The model carries the
    authoritative segment/overlap; the module constants are the fallback.
    """
    segment = getattr(separator, "_segment", None)
    if segment is None:
        segment = _model_segment_seconds(separator)
    else:
        try:
            segment = float(segment)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            segment = _model_segment_seconds(separator)
    if not segment or segment <= 0:
        segment = _model_segment_seconds(separator)
    try:
        overlap = float(getattr(separator, "_overlap", FALLBACK_OVERLAP))
    except (TypeError, ValueError):
        overlap = FALLBACK_OVERLAP
    sample_rate = int(separator.samplerate)
    segment_length = max(1, int(sample_rate * segment))
    stride = max(1, int((1.0 - overlap) * segment_length))
    if audio_length <= 0:
        return 0
    return max(1, math.ceil(audio_length / stride))


class _DemucsProgress:
    """Turns Demucs' internal callback into a monotonic 0..1 fraction.

    Demucs calls back once per (model in bag, shift, segment) unit with
    `state` in {"start", "end"} plus `audio_length` and `models`. Counting the
    "end" events against the real unit total is the only honest progress
    source; the outer 600s chunk plan reports a single chunk for a 45s source
    and would show nothing at all.
    """

    def __init__(self, separator_factory=None, sink=None):
        self._separator = separator_factory() if separator_factory else None
        self._sink = sink
        self._done_units: set[tuple[int, int, int]] = set()
        self._total_units = 0
        self._audio_length = 0
        self._models = 1
        self._high = 0.0
        self._last_fraction = 0.0
        self._last_emit_at = 0.0
        self._lock = threading.Lock()

    def set_separator(self, separator) -> None:
        self._separator = separator

    def set_sink(self, sink) -> None:
        self._sink = sink

    @property
    def fraction(self) -> float:
        with self._lock:
            return self._high

    def _reset_if_new_work(self, payload: dict) -> None:
        """Re-baseline before the first unit of a new outer chunk is recorded.

        Must run before the unit is inserted, otherwise the first unit of every
        chunk is counted and then immediately cleared.
        """
        audio_length = int(payload.get("audio_length") or 0)
        models = int(payload.get("models") or 1)
        if audio_length == self._audio_length and models == self._models:
            return
        self._done_units.clear()
        self._audio_length = audio_length
        self._models = models
        segments = _segment_plan(self._separator, audio_length) if self._separator else 1
        self._total_units = max(1, models * max(1, SHIFTS) * segments)

    def __call__(self, payload: dict) -> None:
        """Demucs callback. Never raises — a progress bug must not fail compute."""
        try:
            if not isinstance(payload, dict) or payload.get("state") != "end":
                return
            unit = (
                int(payload.get("model_idx_in_bag") or 0),
                int(payload.get("shift_idx") or 0),
                int(payload.get("segment_offset") or 0),
            )
            with self._lock:
                self._reset_if_new_work(payload)
                self._done_units.add(unit)
                fraction = min(1.0, len(self._done_units) / self._total_units)
                # Demucs dispatches segment futures concurrently, so "end"
                # events can be observed slightly out of order across a pool.
                # Report the high-water mark so the UI never moves backwards.
                self._high = max(fraction, self._high)
            # Emit on every unit; `emit` owns the throttling.
            self.emit()
        except Exception:
            return

    def emit(self, *, force: bool = False) -> None:
        """Push the current fraction through the sink, throttled.

        Throttles on BOTH axes independently: a minimum wall-clock gap (so a
        slow run does not spam) and a minimum fraction delta (so a fast run
        still shows movement instead of one 6% followed straight to 15%).
        Completion is never throttled.

        Called from Demucs' own worker threads, so it must be non-blocking and
        must never propagate an exception into the compute path.
        """
        sink = self._sink
        if sink is None:
            return
        with self._lock:
            fraction = self._high
            now = time.monotonic()
            if not force and fraction < 1.0:
                if fraction - self._last_fraction < PROGRESS_MIN_DELTA:
                    return
                if now - self._last_emit_at < PROGRESS_MIN_INTERVAL_SECONDS:
                    return
            self._last_fraction = fraction
            self._last_emit_at = now
        try:
            sink(fraction)
        except Exception:
            # A broken progress consumer must never abort the separation.
            pass


def _chunk_plan(
    total_frames: int,
    chunk_frames: int,
    overlap_frames: int,
) -> list[tuple[int, int, int]]:
    if total_frames <= 0:
        return []
    if chunk_frames <= 0:
        raise ValueError("chunk_frames must be positive")
    if overlap_frames < 0:
        raise ValueError("overlap_frames must not be negative")
    out = []
    start = 0
    while start < total_frames:
        stop = min(start + chunk_frames, total_frames)
        if total_frames - stop <= overlap_frames:
            stop = total_frames
        out.append((start, stop, min(stop + overlap_frames, total_frames)))
        start = stop
    return out


def _crossfade(tail, head):
    import numpy as np

    length = min(len(tail), len(head))
    if length <= 0:
        return head
    ramp = np.linspace(0.0, 1.0, length, endpoint=False, dtype=np.float32)
    ramp = ramp.reshape(-1, *([1] * (head.ndim - 1)))
    result = head.copy()
    result[:length] = tail[:length] * (1.0 - ramp) + head[:length] * ramp
    return result


def _extract_stereo_audio(
    source_video: Path,
    destination: Path,
    *,
    sample_rate: int,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(source_video),
        "-vn", "-map", "0:a:0",
        "-ac", "2", "-ar", str(sample_rate),
        "-c:a", "pcm_f32le",
        "-rf64", "auto",
        str(destination),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not destination.exists():
        raise RuntimeError(f"Audio extraction for separation failed: {(result.stderr or '')[-500:]}")
    return destination


def _separate_sync(
    source_video: Path,
    work_dir: Path,
    *,
    chunk_seconds: int = CHUNK_SECONDS,
    on_progress=None,
) -> tuple[Path, Path]:
    try:
        import numpy as np
        import soundfile as sf
        import torch
        from demucs.api import Separator
    except ImportError as exc:
        raise RuntimeError(
            "HQ clean dub requires Demucs dependencies. Install app requirements."
        ) from exc

    work_dir.mkdir(parents=True, exist_ok=True)
    vocals_final = work_dir / "audio_vocals.wav"
    bgm_final = work_dir / "audio_bgm.wav"
    vocals_pending = work_dir / ".audio_vocals.pending.wav"
    bgm_pending = work_dir / ".audio_bgm.pending.wav"
    source_wav = work_dir / ".demucs_input.wav"
    vocals_raw = work_dir / ".demucs_vocals.raw.wav"
    bgm_raw = work_dir / ".demucs_bgm.raw.wav"

    for path in (
        vocals_final, bgm_final, vocals_pending, bgm_pending,
        source_wav, vocals_raw, bgm_raw,
    ):
        path.unlink(missing_ok=True)

    progress = _DemucsProgress() if on_progress is not None else None
    separator = Separator(
        model="htdemucs_ft",
        device=_device(),
        progress=False,
        shifts=SHIFTS,
        callback=progress,
    )
    if progress is not None:
        progress.set_separator(separator)
    sample_rate = int(separator.samplerate)
    channels = int(separator.audio_channels)
    if channels != 2:
        raise RuntimeError(f"Demucs expected stereo output, got {channels} channels")

    _extract_stereo_audio(source_video, source_wav, sample_rate=sample_rate)
    tails = {"vocals": None, "bgm": None}
    peaks = {"vocals": 0.0, "bgm": 0.0}

    try:
        with (
            sf.SoundFile(source_wav) as reader,
            sf.SoundFile(
                vocals_raw, "w", samplerate=sample_rate, channels=2,
                subtype="FLOAT", format="RF64",
            ) as vocals_writer,
            sf.SoundFile(
                bgm_raw, "w", samplerate=sample_rate, channels=2,
                subtype="FLOAT", format="RF64",
            ) as bgm_writer,
        ):
            plan = _chunk_plan(
                len(reader),
                chunk_seconds * sample_rate,
                OVERLAP_SECONDS * sample_rate,
            )
            if not plan:
                raise RuntimeError("Extracted audio is empty")

            # The outer 600s plan usually yields a single chunk; when it does
            # not, scale the live chunk fraction over the whole file so the
            # job percent keeps climbing instead of resetting per chunk.
            _chunk_index = [0]
            if progress is not None and on_progress is not None:
                _total_chunks = len(plan)
                if _total_chunks > 1:
                    def _scale(frac, _state=_chunk_index, _n=_total_chunks):
                        on_progress(min(1.0, (_state[0] + frac) / _n))
                    progress.set_sink(_scale)
                else:
                    progress.set_sink(on_progress)

            for chunk_index, (own_start, own_stop, window_stop) in enumerate(plan):
                _chunk_index[0] = chunk_index
                reader.seek(own_start)
                window = reader.read(
                    window_stop - own_start,
                    dtype="float32",
                    always_2d=True,
                )
                mix = torch.from_numpy(np.ascontiguousarray(window.T))
                _, stems = separator.separate_tensor(mix, sample_rate)

                vocals = stems.get("vocals")
                if vocals is None:
                    raise RuntimeError("Demucs returned no vocals stem")
                background = None
                for name, stem in stems.items():
                    if name == "vocals":
                        continue
                    background = stem if background is None else background + stem
                if background is None:
                    raise RuntimeError("Demucs returned no background stems")

                owned = own_stop - own_start
                for name, stem, writer in (
                    ("vocals", vocals, vocals_writer),
                    ("bgm", background, bgm_writer),
                ):
                    samples = stem.detach().cpu().numpy().T
                    core = samples[:owned]
                    previous = tails[name]
                    if previous is not None:
                        core = _crossfade(previous, core)
                    peaks[name] = max(
                        peaks[name],
                        float(np.abs(core).max(initial=0.0)),
                    )
                    writer.write(core)
                    tails[name] = samples[owned:].copy()

        # Match YouDub's global peak protection before publishing artifacts.
        for raw, pending, name in (
            (vocals_raw, vocals_pending, "vocals"),
            (bgm_raw, bgm_pending, "bgm"),
        ):
            scale = 1.0 / max(1.01 * peaks[name], 1.0)
            with sf.SoundFile(raw) as reader, sf.SoundFile(
                pending,
                "w",
                samplerate=sample_rate,
                channels=2,
                subtype="PCM_16",
                format="RF64",
            ) as writer:
                while True:
                    block = reader.read(1 << 20, dtype="float32", always_2d=True)
                    if len(block) == 0:
                        break
                    writer.write(block * scale)

        vocals_pending.replace(vocals_final)
        bgm_pending.replace(bgm_final)
        if progress is not None:
            progress.emit(force=True)
        return vocals_final, bgm_final
    except BaseException:
        vocals_final.unlink(missing_ok=True)
        bgm_final.unlink(missing_ok=True)
        vocals_pending.unlink(missing_ok=True)
        bgm_pending.unlink(missing_ok=True)
        raise
    finally:
        for path in (source_wav, vocals_raw, bgm_raw, vocals_pending, bgm_pending):
            path.unlink(missing_ok=True)
        # Drop the model references but keep the torch caching allocator warm:
        # WhisperX runs next in the same process and reallocating the CUDA
        # caching allocator is more expensive than retaining reusable blocks.
        separator = None


async def separate_vocals_background(
    source_video: Path,
    work_dir: Path,
    on_progress=None,
) -> tuple[Path, Path]:
    """Separate vocals/background, optionally reporting 0..1 as work advances.

    `on_progress` may be any callable taking a float. It is invoked from the
    Demucs worker threads, so callers on an event loop should bridge it
    themselves (see the Whisper progress bridge in clip_extractor). Any
    exception it raises is swallowed — progress must never fail separation.
    """
    if on_progress is None:
        return await asyncio.to_thread(_separate_sync, source_video, work_dir)
    return await asyncio.to_thread(
        _separate_sync, source_video, work_dir, on_progress=on_progress
    )
