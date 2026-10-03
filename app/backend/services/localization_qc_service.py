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
from backend.services.dubbing_job_state import speaker_gender_warnings


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
    target_language: str | None = None,
) -> QCReport:
    failures: list[dict] = []
    warnings: list[dict] = []
    source_meta = probe_media(source_video)
    final_meta = probe_media(final_video)
    alignment = transcript.metadata.get("alignment") or {}
    speaker_state = transcript.metadata.get("speakers") or {}
    speakers = {seg.speaker_id for seg in transcript.segments if seg.speaker_id}
    speakers.update(speaker_state.get("detected_speakers") or [])
    for seg in transcript.segments:
        for word in seg.source_words:
            speakers.update(word.get("speaker_ids") or [])
            if word.get("speaker_id"):
                speakers.add(word["speaker_id"])
    speaker_count = max(len(speakers), int(alignment.get("speaker_count") or 0))
    voice_map = speaker_state.get("speaker_voices") or profile.speaker_voices
    identities = speaker_state.get("speaker_voice_identities") or {}
    if not identities:
        identities = {speaker: [voice] for speaker, voice in voice_map.items() if voice}
    resolved_identities = {identity for values in identities.values() for identity in values}
    shared_voice = profile.speaker_voice_mode == "shared"
    unassigned = [seg.id for seg in transcript.segments if seg.source_text.strip() and seg.speaker_id is None]
    if profile.diarization_mode != "off" and not alignment.get("diarization_applied"):
        warnings.append({"code": "diarization_unavailable",
                         "reason": alignment.get("diarization_reason") or "not_run",
                         "message": "Speaker detection was not verified; review speaker assignments."})
    if profile.diarization_mode != "off" and unassigned:
        warnings.append({"code": "unassigned_speakers", "segment_ids": unassigned[:50]})
    unresolved_speakers = sorted(speaker for speaker in speakers if not identities.get(speaker))
    voice_owners: dict[str, set[str]] = {}
    for speaker, values in identities.items():
        for identity in values:
            voice_owners.setdefault(identity, set()).add(speaker)
    shared_identities = {identity: sorted(owners) for identity, owners in voice_owners.items() if len(owners) > 1}
    if check_tts_quality and speaker_count > 1 and not shared_voice and (
            len(resolved_identities) < speaker_count or unresolved_speakers or shared_identities):
        warnings.append({"code": "speaker_voice_mismatch", "speaker_count": speaker_count,
                         "resolved_voice_count": len(resolved_identities),
                         "unresolved_speakers": unresolved_speakers,
                         "shared_voice_identities": shared_identities,
                         "message": "Shared or unresolved speaker voices require human review."})
    source_genders = {**(alignment.get("speaker_genders") or {}), **(speaker_state.get("speaker_genders") or {})}
    source_genders.update({speaker: {"gender": gender, "confirmed": gender != "unknown", "source": "reviewer"}
                           for speaker, gender in profile.speaker_genders.items()})
    output_genders = speaker_state.get("resolved_voice_genders") or {}
    if check_tts_quality:
        warnings.extend(warning for warning in speaker_state.get("voice_warnings", [])
                        if warning.get("code") not in {"speaker_gender_mismatch", "speaker_gender_unverified"})
        for speaker in sorted(speakers):
            fact = source_genders.get(speaker) or {}
            voices = (speaker_state.get("resolved_voices") or {}).get(speaker)
            # Older artifacts have only the gender set; retain that evidence.
            pairs = list(voices.items()) if voices else [
                (voice_map.get(speaker) or profile.tts_voice or "", gender)
                for gender in (output_genders.get(speaker) or ["unknown"])]
            for voice, gender in pairs:
                warnings.extend(speaker_gender_warnings(speaker, fact, voice, gender,
                    speaker_state.get("provider") or profile.tts_provider))
    overlaps = alignment.get("overlap_intervals") or []
    if not overlaps:
        overlaps = [{"segment_id": seg.id, "start": word.get("start"), "end": word.get("end"),
                     "speaker_ids": word["speaker_ids"]}
                    for seg in transcript.segments for word in seg.source_words
                    if len(set(word.get("speaker_ids") or [])) > 1]
    if overlaps:
        warnings.append({"code": "unresolved_speaker_overlap", "count": len(overlaps), "intervals": overlaps[:50]})

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
    from backend.services.dubbing_rewrite_guard import untranslated_han
    unlocalized = [seg.id for seg in transcript.segments if untranslated_han(
        seg.translated_text or "", target_language or transcript.metadata.get("target_language"),
        allowed=[*profile.protected_terms, *profile.glossary.values()])]
    if unlocalized:
        failures.append({"code": "unlocalized_vietnamese_text", "segment_ids": unlocalized})

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

    pronunciation_warnings = [
        issue for issue in (timing_issues or [])
        if issue.get("reason") == "speech_pronunciation_normalized"
    ]
    if pronunciation_warnings:
        warnings.append({"code": "dubbing_pronunciation_review",
                         "count": len(pronunciation_warnings), "issues": pronunciation_warnings[:50],
                         "message": "Foreign-script spoken forms were normalized; confirm names/pronunciation before publish."})

    onset_warnings = [
        issue for issue in (timing_issues or [])
        if issue.get("reason") == "estimated_onset_drift"
    ]
    if onset_warnings:
        warnings.append({
            "code": "dubbing_onset_drift",
            "count": len(onset_warnings),
            "issues": onset_warnings[:50],
            "message": (
                "Planned cue onset differs from the source speech anchor. "
                "Chunk-internal offsets are estimates, not forced alignment; "
                "human cadence review is required."
            ),
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
            "diarization": alignment,
            "speaker_count": speaker_count,
            "speaker_voices": voice_map,
            "speaker_genders": source_genders,
            "resolved_voice_genders": output_genders,
            "resolved_voice_count": len(resolved_identities),
            "shared_voice": shared_voice,
            "unassigned_speaker_count": len(unassigned),
            "unresolved_overlap_count": len(overlaps),
        },
    )
