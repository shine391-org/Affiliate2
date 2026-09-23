# SPDX-License-Identifier: AGPL-3.0-only
"""HQ vocal/background separation adapted from YouDub-webui (Apache-2.0).

The stage uses Demucs htdemucs_ft, chunked processing with overlap/crossfade,
and atomic publication of vocals/background artifacts. Affiliate2 imports the
PyPI Demucs package rather than YouDub's repository submodule.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

CHUNK_SECONDS = 600
OVERLAP_SECONDS = 10
SHIFTS = 3


def _device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


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

    separator = Separator(
        model="htdemucs_ft",
        device=_device(),
        progress=False,
        shifts=SHIFTS,
    )
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
                subtype="FLOAT", format="WAV",
            ) as vocals_writer,
            sf.SoundFile(
                bgm_raw, "w", samplerate=sample_rate, channels=2,
                subtype="FLOAT", format="WAV",
            ) as bgm_writer,
        ):
            plan = _chunk_plan(
                len(reader),
                chunk_seconds * sample_rate,
                OVERLAP_SECONDS * sample_rate,
            )
            if not plan:
                raise RuntimeError("Extracted audio is empty")

            for own_start, own_stop, window_stop in plan:
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
                format="WAV",
            ) as writer:
                while True:
                    block = reader.read(1 << 20, dtype="float32", always_2d=True)
                    if len(block) == 0:
                        break
                    writer.write(block * scale)

        vocals_pending.replace(vocals_final)
        bgm_pending.replace(bgm_final)
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
        separator = None


async def separate_vocals_background(
    source_video: Path,
    work_dir: Path,
) -> tuple[Path, Path]:
    return await asyncio.to_thread(_separate_sync, source_video, work_dir)
