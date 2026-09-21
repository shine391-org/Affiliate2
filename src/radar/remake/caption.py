"""Phase 3.2 — Vietnamese caption burn via FFmpeg drawtext."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _find_font() -> str | None:
    """Find a usable TTF font on the system."""
    candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def burn_vietnamese_captions(
    video_path: Path,
    output_path: Path,
    caption_text: str = None,
    font_path: str | None = None,
) -> Path:
    """Burn Vietnamese text as caption overlay on video."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if font_path is None:
        font_path = _find_font()

    # Use subtitles filter with a temp SRT for robust Unicode handling
    srt_path = output_path.parent / "_captions.srt"
    srt_content = (
        "1\n"
        "00:00:00,000 --> 00:00:30,000\n"
        f"{caption_text}\n"
    )
    srt_path.write_text(srt_content, encoding="utf-8")

    # Escape path
    srt_str = str(srt_path.resolve()).replace("\\", "/").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video_path),
        "-vf", f"subtitles='{srt_str}'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        str(output_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg caption burn failed: {res.stderr[:500]}")
    if not output_path.exists():
        raise RuntimeError(f"FFmpeg produced no output: {output_path}")

    srt_path.unlink(missing_ok=True)
    return output_path


def burn_captions_timed(
    video_path: Path,
    output_path: Path,
    sections: list[dict],
    font_path: str | None = None,
) -> Path:
    """Burn timed Vietnamese captions onto video using SRT subtitles.

    Args:
        video_path: Input video
        output_path: Output video with captions
        sections: List of dicts with keys: start, end, text
        font_path: Path to TTF font file (not used with SRT method)

    Returns:
        Path to output video
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build SRT content
    srt_lines = []
    for i, sec in enumerate(sections, 1):
        start = sec.get("start", 0)
        end = sec.get("end", 5)
        text = sec.get("text", "").replace("\n", " ")
        
        def fmt_time(s):
            h = int(s // 3600)
            m = int((s % 3600) // 60)
            sec_int = int(s % 60)
            ms = int((s - int(s)) * 1000)
            return f"{h:02d}:{m:02d}:{sec_int:02d},{ms:03d}"
        
        srt_lines.append(f"{i}")
        srt_lines.append(f"{fmt_time(start)} --> {fmt_time(end)}")
        srt_lines.append(f"{text}")
        srt_lines.append("")

    srt_path = output_path.parent / "_captions.srt"
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")

    # Escape path for ffmpeg
    srt_str = str(srt_path.resolve()).replace("\\", "/").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video_path),
        "-vf", f"subtitles='{srt_str}'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        str(output_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg timed caption burn failed: {res.stderr[:500]}")
    if not output_path.exists():
        raise RuntimeError(f"FFmpeg produced no output: {output_path}")

    srt_path.unlink(missing_ok=True)
    return output_path
