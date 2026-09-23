# SPDX-License-Identifier: AGPL-3.0-only
"""Automatic localization QC and exception report.

Combines SmartSub-style actionable exceptions with OpenCreator's dubbing report
and Affiliate2 media evidence. The report is machine-consumable and drives the
same job/review flow used by automation and expert override.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from backend.core.localization import LocalizationProfile, LocalizationTranscript


@dataclass
class QCReport:
    status: str  # READY | NEEDS_REVIEW | FAILED
    failures: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "failures": self.failures,
            "warnings": self.warnings,
            "metrics": self.metrics,
        }


def probe_media(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_streams", "-show_format",
        "-of", "json", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {(result.stderr or '')[-500:]}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    fmt = data.get("format") or {}

    def _float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    fps = 0.0
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
    if rate and rate != "0/0":
        try:
            a, b = rate.split("/", 1)
            fps = float(a) / float(b)
        except Exception:
            fps = 0.0

    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": round(fps, 4),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "video_bitrate": int(float(video.get("bit_rate") or 0)),
        "audio_bitrate": int(float(audio.get("bit_rate") or 0)),
        "bitrate": int(float(fmt.get("bit_rate") or 0)),
        "duration": _float(fmt.get("duration") or video.get("duration") or audio.get("duration")),
        "container": fmt.get("format_name"),
        "has_video": bool(video),
        "has_audio": bool(audio),
    }


def audio_max_volume_db(path: Path) -> float | None:
    cmd = [
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-vn", "-af", "volumedetect", "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    text = (result.stderr or "") + "\n" + (result.stdout or "")
    match = re.search(r"max_volume:\s*(-?[0-9.]+)\s*dB", text)
    return float(match.group(1)) if match else None


def _speech_coverage(transcript: LocalizationTranscript) -> float:
    source = sum(
        max(0, s.source_end_ms - s.source_start_ms)
        for s in transcript.segments
        if s.source_text.strip()
    )
    target = sum(
        max(0, (s.target_end_ms or 0) - (s.target_start_ms or 0))
        for s in transcript.segments
        if (s.translated_text or "").strip() and s.target_start_ms is not None
    )
    if source <= 0:
        return 1.0
    return min(2.0, target / source)


def evaluate_localization_qc(
    *,
    source_video: Path,
    final_video: Path,
    transcript: LocalizationTranscript,
    profile: LocalizationProfile,
    video_slowdown: float = 1.0,
    timing_issues: list[dict] | None = None,
) -> QCReport:
    failures: list[dict] = []
    warnings: list[dict] = []
    source_meta = probe_media(source_video)
    final_meta = probe_media(final_video)

    if not final_meta["has_video"]:
        failures.append({"code": "missing_video_stream"})
    if not final_meta["has_audio"]:
        failures.append({"code": "missing_audio_stream"})

    untranslated = [
        s.id for s in transcript.segments
        if s.translation_status != "success" or not (s.translated_text or "").strip()
    ]
    if untranslated:
        failures.append({
            "code": "untranslated_segments",
            "count": len(untranslated),
            "segment_ids": untranslated[:50],
        })

    overflow = [
        issue for issue in (timing_issues or [])
        if issue.get("reason") in {"timing_overflow", "speed_limit_exceeded"}
    ]
    if overflow:
        failures.append({
            "code": "timing_overflow",
            "count": len(overflow),
            "issues": overflow[:30],
        })

    speed_warnings = [
        issue for issue in (timing_issues or [])
        if issue.get("reason") == "speed_warning"
    ]
    if speed_warnings:
        warnings.append({
            "code": "high_speech_speed",
            "count": len(speed_warnings),
            "issues": speed_warnings[:30],
        })

    coverage = _speech_coverage(transcript)
    if coverage < 0.72:
        failures.append({"code": "low_target_speech_coverage", "ratio": round(coverage, 4)})
    elif coverage < 0.88:
        warnings.append({"code": "target_speech_coverage", "ratio": round(coverage, 4)})

    expected_duration = source_meta["duration"] * max(1.0, video_slowdown)
    duration_delta = abs(final_meta["duration"] - expected_duration)
    duration_tolerance = max(0.75, expected_duration * 0.02)
    if duration_delta > duration_tolerance:
        failures.append({
            "code": "duration_drift",
            "expected": round(expected_duration, 3),
            "actual": round(final_meta["duration"], 3),
            "delta": round(duration_delta, 3),
        })

    if profile.target_aspect == "9:16":
        if final_meta["height"] <= final_meta["width"]:
            failures.append({"code": "wrong_final_aspect", "expected": "9:16"})
    elif profile.target_aspect == "16:9":
        if final_meta["width"] <= final_meta["height"]:
            failures.append({"code": "wrong_final_aspect", "expected": "16:9"})

    max_volume = audio_max_volume_db(final_video)
    if max_volume is not None and max_volume >= -0.05:
        warnings.append({"code": "audio_near_clipping", "max_volume_db": max_volume})

    failed_segments = [s.id for s in transcript.segments if s.qc_status == "fail"]
    review_segments = [s.id for s in transcript.segments if s.qc_status == "review"]
    if failed_segments:
        failures.append({
            "code": "segment_qc_failed",
            "count": len(failed_segments),
            "segment_ids": failed_segments[:50],
        })
    if review_segments:
        warnings.append({
            "code": "segment_needs_review",
            "count": len(review_segments),
            "segment_ids": review_segments[:50],
        })

    status = "FAILED" if failures else ("NEEDS_REVIEW" if warnings else "READY")
    if profile.qc_profile == "strict" and warnings and status == "NEEDS_REVIEW":
        # Strict does not turn every warning into a fatal render failure; it
        # blocks automatic publish by keeping the output in review.
        status = "NEEDS_REVIEW"

    return QCReport(
        status=status,
        failures=failures,
        warnings=warnings,
        metrics={
            "source_media": source_meta,
            "final_media": final_meta,
            "speech_coverage_ratio": round(coverage, 4),
            "video_slowdown": round(video_slowdown, 5),
            "audio_max_volume_db": max_volume,
            "translated_segments": len(transcript.segments) - len(untranslated),
            "segment_count": len(transcript.segments),
        },
    )
