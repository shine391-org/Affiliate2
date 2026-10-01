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


def _source_speech_ms(transcript: LocalizationTranscript) -> int:
    """Measure aligned source speech without counting long silence inside cues."""
    intervals: list[tuple[int, int]] = []
    fallback_ms = 0
    for segment in transcript.segments:
        if not segment.source_text.strip():
            continue
        word_intervals: list[tuple[int, int]] = []
        for word in segment.source_words or []:
            try:
                if isinstance(word, dict):
                    start, end = word.get("start"), word.get("end")
                else:
                    start, end = word.start, word.end
                start_ms = round(float(start) * 1000)
                end_ms = round(float(end) * 1000)
            except (AttributeError, TypeError, ValueError):
                continue
            if end_ms > start_ms:
                word_intervals.append((start_ms, end_ms))
        if word_intervals:
            intervals.extend(word_intervals)
        else:
            fallback_ms += max(0, segment.source_end_ms - segment.source_start_ms)

    if not intervals:
        return fallback_ms

    current_start, current_end = sorted(intervals)[0]
    aligned_ms = 0
    for start_ms, end_ms in sorted(intervals)[1:]:
        if start_ms <= current_end:
            current_end = max(current_end, end_ms)
        else:
            aligned_ms += current_end - current_start
            current_start, current_end = start_ms, end_ms
    aligned_ms += current_end - current_start
    return fallback_ms + aligned_ms


def _speech_coverage(transcript: LocalizationTranscript) -> float:
    source = _source_speech_ms(transcript)
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
    check_tts_quality: bool = True,
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

    overflow_all = [
        issue for issue in (timing_issues or [])
        if issue.get("reason") in {"timing_overflow", "speed_limit_exceeded"}
    ]
    # A soft overflow is bounded and already rendered, but it is NOT a pass:
    # it is a review warning that carries both ceilings so Studio can explain
    # exactly why a human acknowledgement is required before Publish.
    soft_overflow = [issue for issue in overflow_all if issue.get("soft_overflow") is True]
    hard_overflow = [issue for issue in overflow_all if issue.get("soft_overflow") is not True]
    if soft_overflow:
        warnings.append({
            "code": "timing_overflow_soft",
            "count": len(soft_overflow),
            "required_speed": max(
                float(issue.get("required_speed") or 1.0) for issue in soft_overflow
            ),
            "speed_max": soft_overflow[0].get("speed_max"),
            "emergency_speed_max": soft_overflow[0].get("emergency_speed_max"),
            "chunk_ids": [issue.get("chunk_id") for issue in soft_overflow][:30],
            "segment_ids": [
                segment_id
                for issue in soft_overflow
                for segment_id in (issue.get("segment_ids") or [])
            ][:50],
            "issues": soft_overflow[:30],
            "message": (
                "Dubbing exceeded the normal speed ceiling but stayed inside the "
                "emergency ceiling after every fitting strategy was exhausted. "
                "Human cadence review is required before this revision can publish."
            ),
        })
    if hard_overflow:
        failures.append({
            "code": "timing_overflow",
            "count": len(hard_overflow),
            "issues": hard_overflow[:30],
        })

    if check_tts_quality and (profile.tts_provider or "").strip().lower() == "edge_tts":
        warnings.append({
            "code": "preview_tts_provider",
            "provider": "edge_tts",
            "message": "Edge TTS is the free preview/fallback tier; human voice-quality approval is required before publish.",
        })

    rewrite_warnings = [
        issue for issue in (timing_issues or [])
        if issue.get("reason") in {"rewrite_ai_unavailable", "rewrite_invalid_response"}
    ]
    if rewrite_warnings:
        warnings.append({
            "code": "dubbing_rewrite_degraded",
            "count": len(rewrite_warnings),
            "issues": rewrite_warnings[:30],
            "message": "Duration-aware AI rewrite was unavailable/invalid; timing continued with measured TTS and bounded fallback.",
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

    if video_slowdown > 1.005:
        warnings.append({
            "code": "video_retimed",
            "slowdown_factor": round(video_slowdown, 5),
            "message": "Global video slowdown was used as a last-resort dubbing fit; human cadence review required.",
        })

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
    if soft_overflow and status == "READY":
        # Explicit invariant: a bounded soft overflow can never be reported as a
        # clean pass, whatever other thresholds happen to say.
        status = "NEEDS_REVIEW"
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
