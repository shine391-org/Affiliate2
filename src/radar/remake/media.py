"""Phase 3.2 — Segment plan and FFmpeg media processing."""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Segment:
    """A segment plan entry."""
    start: float
    end: float
    purpose: str
    keep: bool = True
    narration_section: str = ""

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "purpose": self.purpose,
            "keep": self.keep,
            "narration_section": self.narration_section,
        }


@dataclass
class SegmentPlan:
    """Plan for cutting/assembling source video."""
    source_path: str
    segments: list[Segment] = field(default_factory=list)
    target_width: int = 1080
    target_height: int = 1920
    target_aspect: str = "9:16"

    def to_dict(self) -> dict:
        return {
            "source_path": self.source_path,
            "target_width": self.target_width,
            "target_height": self.target_height,
            "target_aspect": self.target_aspect,
            "segments": [s.to_dict() for s in self.segments],
        }


def probe_video(video_path: Path) -> dict:
    """Run ffprobe and return stream/format info."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(video_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        return {}
    return json.loads(res.stdout)


def get_duration(video_path: Path) -> float:
    """Get video duration in seconds."""
    probe = probe_video(video_path)
    return float(probe.get("format", {}).get("duration", 0))


def get_dimensions(video_path: Path) -> tuple[int, int]:
    """Get (width, height) from first video stream."""
    probe = probe_video(video_path)
    for s in probe.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s.get("width", 0)), int(s.get("height", 0))
    return 0, 0


def validate_segment_plan(plan: SegmentPlan, source_duration: float) -> list[str]:
    """Validate a segment plan. Returns list of errors."""
    errors = []
    for i, seg in enumerate(plan.segments):
        if seg.start < 0:
            errors.append(f"segment {i}: start < 0 ({seg.start})")
        if seg.end <= seg.start:
            errors.append(f"segment {i}: end <= start ({seg.start}-{seg.end})")
        if seg.end > source_duration + 0.5:
            errors.append(f"segment {i}: end ({seg.end}) > source duration ({source_duration})")
    return errors


def extract_segment(
    source: Path,
    output: Path,
    start: float,
    end: float,
    target_width: int = 1080,
    target_height: int = 1920,
) -> Path:
    """Extract a segment from source video, reframe to 9:16.

    Handles:
    - Vertical 9:16 source (already correct aspect)
    - Vertical non-9:16 source (scale to height, crop sides)
    - Square/4:5 source (scale to height, crop sides)
    - Horizontal 16:9 source (blur-fill to 9:16)
    """
    duration = end - start
    if duration <= 0:
        raise ValueError(f"Invalid segment duration: {duration}")

    output.parent.mkdir(parents=True, exist_ok=True)

    src_w, src_h = get_dimensions(source)
    if src_w == 0 or src_h == 0:
        raise RuntimeError(f"Cannot probe source dimensions: {source}")

    src_ratio = src_w / src_h
    target_ratio = target_width / target_height  # 9/16 = 0.5625

    # Determine if source is vertical-ish (ratio <= 0.75 i.e. taller than 4:3)
    is_vertical = src_ratio <= 0.75

    if is_vertical and src_w == target_width and src_h == target_height:
        # Already exact target resolution
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", str(start), "-i", str(source),
            "-t", str(duration),
            "-c", "copy",
            str(output),
        ]
    elif is_vertical:
        # Vertical but different resolution — scale up then crop
        # Scale so height matches, then crop width
        vf = (
            f"scale=-1:{target_height},"
            f"crop={target_width}:{target_height}:(in_w-{target_width})/2:0"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", str(start), "-i", str(source),
            "-t", str(duration),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            str(output),
        ]
    else:
        # Horizontal or square — blur-fill approach using filter_complex
        filter_complex = (
            f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
            f"crop={target_width}:{target_height},boxblur=20:5[bg];"
            f"[0:v]scale={target_width}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", str(start), "-i", str(source),
            "-t", str(duration),
            "-filter_complex", filter_complex,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            str(output),
        ]

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg extract failed: {res.stderr[:500]}")
    if not output.exists():
        raise RuntimeError(f"FFmpeg produced no output: {output}")

    return output


def concat_segments(
    segments: list[Path],
    output: Path,
) -> Path:
    """Concatenate video segments using FFmpeg concat demuxer."""
    if not segments:
        raise ValueError("No segments to concatenate")

    output.parent.mkdir(parents=True, exist_ok=True)

    # Create concat list file
    concat_list = output.parent / "_concat_list.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for seg in segments:
            f.write(f"file '{seg.resolve()}'\n")

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(output),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    concat_list.unlink(missing_ok=True)

    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed: {res.stderr[:500]}")
    if not output.exists():
        raise RuntimeError(f"FFmpeg produced no output: {output}")

    return output


def mux_audio_video(
    video_path: Path,
    audio_path: Path,
    output: Path,
) -> Path:
    """Replace audio track of video with new audio."""
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        str(output),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg mux failed: {res.stderr[:500]}")
    if not output.exists():
        raise RuntimeError(f"FFmpeg produced no output: {output}")

    return output


def burn_captions(
    video_path: Path,
    caption_ass_path: Path,
    output: Path,
) -> Path:
    """Burn ASS captions onto video."""
    output.parent.mkdir(parents=True, exist_ok=True)

    # Escape path for filter
    ass_str = str(caption_ass_path.resolve()).replace("\\", "\\\\").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video_path),
        "-vf", f"ass={ass_str}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        str(output),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg caption burn failed: {res.stderr[:500]}")
    if not output.exists():
        raise RuntimeError(f"FFmpeg produced no output: {output}")

    return output
