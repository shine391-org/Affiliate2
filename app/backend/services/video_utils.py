# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Shared video utility functions — used across multiple services."""
import json
import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaStreamInventory:
    """What a media file actually contains, measured — never assumed.

    `has_video` / `has_audio` are the two facts downstream stages gate on. A
    container that ffprobe cannot open reports `readable=False` and both flags
    False, which callers MUST NOT confuse with "successfully probed and found
    to have no audio": that is what `readable` is for.
    """

    has_video: bool = False
    has_audio: bool = False
    video_codec: str | None = None
    audio_codec: str | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    bitrate_bps: int | None = None
    container: str | None = None
    readable: bool = False

    def is_playable_full_video(self) -> bool:
        """Video AND audio both present in one container.

        The product contract for a "video" asset Studio accepts: a file with
        only a video stream is not a playable video, it is a video track, and
        every A/V consumer downstream (Demucs, Whisper, Clip Studio) fails on
        it with an opaque subprocess error.
        """
        return self.has_video and self.has_audio

    def describe_missing(self) -> str:
        """One short clause naming exactly which stream is absent."""
        if not self.has_video and not self.has_audio:
            return "không có luồng hình lẫn luồng âm thanh"
        if not self.has_audio:
            return "không có luồng âm thanh"
        if not self.has_video:
            return "không có luồng hình"
        return ""


def _ffprobe_json(file_path: Path | str, show_entries: str) -> dict:
    """ONE ffprobe JSON call, shared by every probe in this module.

    Every separate ffprobe implementation is a place where two call sites can
    drift on what "has audio" means. Return `{}` on any failure so callers
    treat an unreadable file as unmeasured, not as empty.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", show_entries,
                "-of", "json",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return {}
        payload = json.loads(result.stdout or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _as_int(value):
    try:
        n = int(value)
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def _as_float(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (TypeError, ValueError):
        return None


def _as_fps(value):
    if not value or value in ("0/0", "N/A"):
        return None
    try:
        num, den = str(value).split("/", 1)
        den_f = float(den)
        if den_f == 0:
            return None
        n = float(num) / den_f
        return round(n, 3) if math.isfinite(n) and n > 0 else None
    except (TypeError, ValueError):
        return _as_float(value)


def probe_stream_inventory(file_path: Path | str) -> MediaStreamInventory:
    """Measure a file's stream inventory in a single ffprobe call.

    The canonical "does this asset contain audio?" gate. Scoped per call site
    — a missing audio stream is a defect for a localization full_dub source
    and for a Douyin "video" download, but perfectly legitimate for an
    audio-only or deliberately silent asset, so this never asserts a policy
    on its own.
    """
    # Deliberately NO pre-flight `Path.exists()` check: ffprobe is the
    # authority on whether a file is readable, and an existence check in front
    # of it both duplicates that work and makes the probe untestable against a
    # stubbed ffprobe. A missing file simply makes ffprobe fail, which is the
    # same `readable=False` result.
    payload = _ffprobe_json(
        file_path,
        "stream=codec_type,codec_name,width,height,avg_frame_rate,bit_rate:"
        "format=duration,bit_rate,format_name",
    )
    if not payload:
        return MediaStreamInventory(readable=False)

    streams = payload.get("streams")
    streams = streams if isinstance(streams, list) else []
    video = next(
        (s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"),
        {},
    )
    audio = next(
        (s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"),
        {},
    )
    fmt = payload.get("format")
    fmt = fmt if isinstance(fmt, dict) else {}

    return MediaStreamInventory(
        has_video=bool(video),
        has_audio=bool(audio),
        video_codec=video.get("codec_name") or None,
        audio_codec=audio.get("codec_name") or None,
        duration=_as_float(fmt.get("duration")),
        width=_as_int(video.get("width")),
        height=_as_int(video.get("height")),
        fps=_as_fps(video.get("avg_frame_rate")),
        bitrate_bps=_as_int(fmt.get("bit_rate")) or _as_int(video.get("bit_rate")),
        container=fmt.get("format_name") or None,
        readable=True,
    )


def probe_duration(file_path: Path | str, default: float = 0.0) -> float:
    """Get media file duration in seconds using ffprobe.

    Returns *default* if the probe fails for any reason (missing file,
    corrupt media, ffprobe not installed, etc.).
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        return default



def probe_media_details(file_path: Path | str) -> dict:
    """Return actual stream/container properties for a media file.

    One ffprobe call, JSON output, no guessed defaults. Unknown fields remain
    None so QA can distinguish "not measured" from a real zero.
    """
    inv = probe_stream_inventory(file_path)
    if not inv.readable:
        return {}
    return {
        "has_video": inv.has_video,
        "has_audio": inv.has_audio,
        "width": inv.width,
        "height": inv.height,
        "fps": inv.fps,
        "video_codec": inv.video_codec,
        "audio_codec": inv.audio_codec,
        "bitrate_bps": inv.bitrate_bps,
        "duration_seconds": inv.duration,
        "container": inv.container,
    }

def probe_media(file_path: Path | str) -> tuple[int, int, float]:
    """Get (width, height, duration_seconds) in ONE ffprobe call.

    Prefer this over calling a dimensions probe and `probe_duration`
    back-to-back — a caller that needs both otherwise spawns two subprocesses
    per file, which adds up on a pipeline that touches every clip. Returns 0
    for whichever fields could not be read; callers decide the fallback.

    The three fields fail INDEPENDENTLY. This used to parse all-or-nothing,
    so a container whose format carries no duration — ffprobe prints a
    literal "N/A", and `float("N/A")` raises — threw away perfectly good
    dimensions and returned (0, 0, 0.0). Every dimension consumer defaults to
    9:16 on zeros (`aspect_from_dims`), so a 1920x1080 elementary stream was
    labelled portrait, and the caption geometry took that label: wrong
    margins and font size, burned into the file. Read each field on its own.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return 0, 0, 0.0

    # Output is one value per line, in this order: width, height, duration.
    parts = [p for p in result.stdout.strip().splitlines() if p.strip()]

    def _num(index: int, cast):
        try:
            return cast(parts[index])
        except (IndexError, TypeError, ValueError):
            return cast(0)

    width, height = _num(0, int), _num(1, int)
    duration = _num(2, float)
    # A negative or non-finite duration is as unusable as a missing one, and
    # both reach ffmpeg as a seek bound downstream.
    if not math.isfinite(duration) or duration < 0:
        duration = 0.0
    return width, height, duration


# Aspect labels the app stores on GeneratedVideo.aspect_ratio and keys its
# Library tile sizing / filter chips off. Anything not near-square and not
# portrait reads as landscape.
def aspect_from_dims(width: int, height: int, default: str = "9:16") -> str:
    """Map raw dimensions onto the app's aspect vocabulary. Pure.

    The 1:1 band is deliberately narrow (±2%) — encoders round odd
    dimensions, so 1080x1082 is square in intent.
    """
    if width <= 0 or height <= 0:
        return default
    ratio = width / height
    if 0.98 <= ratio <= 1.02:
        return "1:1"
    return "16:9" if ratio > 1 else "9:16"


def aspect_label(file_path: Path | str, default: str = "9:16") -> str:
    """Return "9:16" / "16:9" / "1:1" for a media file's real dimensions.

    Callers persist this instead of assuming an aspect: clip extraction only
    reframes a LANDSCAPE source, so a square or 4:5 source passes through
    untouched and was still labelled 9:16 — and the Library renders the tile
    at the label's shape, not the file's. Returns *default* when the probe
    fails so a missing ffprobe never breaks a save.
    """
    w, h, _ = probe_media(file_path)
    if w <= 0 or h <= 0:
        # Loud, because it is not cosmetic: the caption engine takes this
        # label too, so a probe that quietly fails under load burns portrait
        # margins into a landscape video.
        logger.warning(
            "aspect probe failed for %s — falling back to %s; captions and the "
            "Library tile will use that shape", file_path, default,
        )
    return aspect_from_dims(w, h, default=default)


def cover_vf(target_w: int, target_h: int, setsar: bool = True) -> str:
    """FFmpeg filter chain that fills target_w x target_h without distortion.

    Scales up until BOTH dimensions are covered (`increase`), then centre-crops
    the overflow. This is the "cover" behaviour a CSS `object-fit: cover` gives
    you, and it is the only correct pre-step before `zoompan`: zoompan's crop
    window inherits the INPUT aspect and its `s=WxH` then anamorphically
    STRETCHES it, so anything reaching zoompan must already be at the target
    aspect.

    `setsar` is off for chains that continue into another filter which sets it.
    """
    chain = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h}"
    )
    return f"{chain},setsar=1" if setsar else chain


def probe_dimensions(file_path: Path | str) -> tuple[int, int]:
    """Get (width, height) for any media file, still images included.

    Deliberately NOT `probe_media`: that one asks for `format=duration` in the
    same call and a still image reports `N/A` there, so the float() fails and
    the whole probe degrades to (0, 0, 0.0) — an image looks like an
    unreadable file. Returns (0, 0) on real failure.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        parts = [p for p in result.stdout.strip().splitlines() if p.strip()]
        if len(parts) < 2:
            return 0, 0
        return int(parts[0]), int(parts[1])
    except Exception:
        return 0, 0
