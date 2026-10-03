# SPDX-License-Identifier: AGPL-3.0-only
"""WhisperX forced alignment + speaker diarization for Phase-C HQ localization.

Alignment behavior is adapted from VideoLingo's local WhisperX pipeline
(Apache-2.0). Speaker-to-cue assignment follows SmartSub's overlap-duration
strategy (MIT): primary speaker wins by covered duration; sufficiently large
secondary overlap is retained in word metadata but the cue's primary
speaker_id remains stable for dubbing voice selection.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from dataclasses import dataclass, field
from pathlib import Path

from backend.config import settings
from backend.core.localization import LocalizationProfile, LocalizationSegment, LocalizationTranscript

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AlignmentReport:
    alignment_applied: bool = False
    diarization_applied: bool = False
    diarization_reason: str | None = None
    speaker_count: int = 0
    device: str = "cpu"
    diarization_requested: bool = False
    unassigned_segment_count: int = 0
    overlap_intervals: list[dict] = field(default_factory=list)


def _ensure_torch_checkpoint_compat():
    """Match VideoLingo's WhisperX/PyTorch>=2.6 compatibility guard.

    PyTorch changed torch.load's default to weights_only=True. Trusted
    WhisperX/pyannote checkpoints may contain OmegaConf objects and fail under
    that default. Patch only the default case; callers that explicitly choose
    weights_only keep their requested behavior.
    """
    import torch

    current = torch.load
    if getattr(current, "_affiliate2_weights_compat", False):
        return torch

    @functools.wraps(current)
    def compatible_load(*args, **kwargs):
        if kwargs.get("weights_only") is None:
            kwargs["weights_only"] = False
        return current(*args, **kwargs)

    compatible_load._affiliate2_weights_compat = True
    torch.load = compatible_load
    return torch


def _device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _make_diarization_pipeline(factory, *, token: str, device: str):
    """Construct WhisperX diarization with the exact upstream contracts.

    WhisperX 3.8.x names the gated credential `token`, while pyannote's
    Pipeline.to() requires an actual torch.device rather than a string.
    """
    import torch
    return factory(token=token, device=torch.device(device))


def _speaker_segments(rows) -> list[dict]:
    out: list[dict] = []
    if rows is None:
        return out
    if hasattr(rows, "to_dict"):
        try:
            rows = rows.to_dict("records")
        except TypeError:
            rows = rows.to_dict(orient="records")
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            start = float(row.get("start"))
            end = float(row.get("end"))
        except (TypeError, ValueError):
            continue
        speaker = row.get("speaker")
        if end <= start or speaker is None:
            continue
        out.append({"start": start, "end": end, "speaker": str(speaker)})
    return sorted(out, key=lambda x: (x["start"], x["end"]))


def _assign_speaker(
    start_s: float,
    end_s: float,
    diar_segments: list[dict],
) -> tuple[str | None, list[str]]:
    """SmartSub-style overlap assignment with a conservative secondary threshold."""
    duration = max(0.0, end_s - start_s)
    if duration <= 0:
        return None, []
    overlap: dict[str, float] = {}
    for row in diar_segments:
        if row["end"] <= start_s:
            continue
        if row["start"] >= end_s:
            break
        covered = max(0.0, min(end_s, row["end"]) - max(start_s, row["start"]))
        if covered > 0:
            overlap[row["speaker"]] = overlap.get(row["speaker"], 0.0) + covered
    ranked = sorted(overlap.items(), key=lambda x: (-x[1], x[0]))
    if not ranked:
        return None, []
    primary, primary_s = ranked[0]
    minimum_primary = max(0.08, duration * 0.08)
    if primary_s < minimum_primary:
        return None, []
    speakers = [primary]
    for speaker, seconds in ranked[1:]:
        if seconds >= 0.18 and seconds >= primary_s * 0.30:
            speakers.append(speaker)
    return primary, speakers


def _aligned_to_transcript(
    original: LocalizationTranscript,
    aligned_segments: list[dict],
    diar_segments: list[dict],
) -> LocalizationTranscript:
    out: list[LocalizationSegment] = []
    for index, seg in enumerate(aligned_segments or []):
        try:
            start = max(0.0, float(seg.get("start", 0)))
            end = max(start, float(seg.get("end", start)))
        except (TypeError, ValueError):
            continue
        text = str(seg.get("text") or "").strip()
        if not text or end <= start:
            continue

        primary, speakers = _assign_speaker(start, end, diar_segments)
        words: list[dict] = []
        for raw in seg.get("words") or []:
            if not isinstance(raw, dict):
                continue
            word = dict(raw)
            try:
                w_start = float(word["start"])
                w_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                words.append(word)
                continue
            w_primary, w_speakers = _assign_speaker(w_start, w_end, diar_segments)
            if w_primary is not None:
                word["speaker_id"] = w_primary
            if len(w_speakers) > 1:
                word["speaker_ids"] = w_speakers
            words.append(word)

        # Diarization-only rows retain the original ID and every ASR field.
        original_by_id = {item.id: item for item in original.segments}
        source = original_by_id.get(seg.get("id"))
        item = source.model_copy(deep=True) if source else LocalizationSegment(
            id=f"align-{index + 1:04d}", source_text=text,
            source_start_ms=round(start * 1000), source_end_ms=round(end * 1000),
        )
        item.speaker_id = primary
        item.source_words = words
        out.append(item)

    if not out:
        return original.model_copy(deep=True)
    result = original.model_copy(deep=True)
    result.segments = out
    result.text = " ".join(seg.source_text for seg in out).strip()
    result.source = "whisperx_alignment"
    return result


def _align_sync(
    media_path: Path,
    transcript: LocalizationTranscript,
    profile: LocalizationProfile,
    huggingface_token: str = "",
) -> tuple[LocalizationTranscript, AlignmentReport]:
    if profile.alignment_mode == "off" and profile.diarization_mode == "off":
        return transcript.model_copy(deep=True), AlignmentReport()

    token = (huggingface_token or settings.HUGGINGFACE_TOKEN or "").strip()
    report = AlignmentReport(diarization_requested=profile.diarization_mode != "off")
    if profile.alignment_mode == "off" and not token and profile.diarization_mode == "auto":
        report.diarization_reason = "missing_huggingface_token"
        report.unassigned_segment_count = len(transcript.segments)
        return transcript.model_copy(deep=True), report

    try:
        torch = _ensure_torch_checkpoint_compat()
        import whisperx
    except ImportError as exc:
        if profile.alignment_mode == "off" and profile.diarization_mode == "auto":
            report.diarization_reason = "missing_whisperx_dependencies"
            report.unassigned_segment_count = len(transcript.segments)
            return transcript.model_copy(deep=True), report
        raise RuntimeError(
            "HQ alignment requires WhisperX dependencies. Install app requirements."
        ) from exc

    device = _device()
    report.device = device
    audio = whisperx.load_audio(str(media_path))

    aligned_segments = [
        {
            "start": seg.source_start_ms / 1000.0,
            "end": seg.source_end_ms / 1000.0,
            "text": seg.source_text,
            "id": seg.id,
            "words": [dict(word) for word in seg.source_words],
        }
        for seg in transcript.segments
    ]

    if profile.alignment_mode == "whisperx":
        language = transcript.language or "en"
        align_model, metadata = whisperx.load_align_model(
            language_code=language,
            device=device,
        )
        try:
            aligned = whisperx.align(
                aligned_segments,
                align_model,
                metadata,
                audio,
                device,
                return_char_alignments=False,
            )
            aligned_segments = aligned.get("segments") or aligned_segments
            report.alignment_applied = True
        finally:
            del align_model
            if device == "cuda":
                torch.cuda.empty_cache()

    diar_segments: list[dict] = []
    if profile.diarization_mode != "off":
        if not token:
            if profile.diarization_mode == "whisperx":
                raise RuntimeError(
                    "WhisperX diarization requires HUGGINGFACE_TOKEN."
                )
            report.diarization_reason = "missing_huggingface_token"
        else:
            try:
                try:
                    from whisperx.diarize import DiarizationPipeline
                except ImportError:
                    DiarizationPipeline = whisperx.DiarizationPipeline
                pipeline = _make_diarization_pipeline(
                    DiarizationPipeline,
                    token=token,
                    device=device,
                )
                kwargs = {}
                if profile.diarization_speakers:
                    kwargs["min_speakers"] = profile.diarization_speakers
                    kwargs["max_speakers"] = profile.diarization_speakers
                diarized = pipeline(audio, **kwargs)
                diar_segments = _speaker_segments(diarized)
                report.diarization_applied = bool(diar_segments)
                report.speaker_count = len({x["speaker"] for x in diar_segments})
                if not diar_segments:
                    report.diarization_reason = "empty_result"
                del pipeline
                if device == "cuda":
                    torch.cuda.empty_cache()
            except Exception as exc:
                if profile.diarization_mode == "whisperx":
                    raise
                logger.warning("Optional diarization skipped: %s", exc)
                report.diarization_reason = f"inference_failed:{type(exc).__name__}"

    result = _aligned_to_transcript(transcript, aligned_segments, diar_segments)
    if not report.alignment_applied:
        result.source = transcript.source
    report.unassigned_segment_count = sum(seg.speaker_id is None for seg in result.segments)
    for index, first in enumerate(diar_segments):
        for second in diar_segments[index + 1:]:
            if second["start"] >= first["end"]:
                break
            start, end = max(first["start"], second["start"]), min(first["end"], second["end"])
            if first["speaker"] != second["speaker"] and end > start:
                report.overlap_intervals.append({
                    "start_ms": round(start * 1000), "end_ms": round(end * 1000),
                    "speaker_ids": sorted({first["speaker"], second["speaker"]}),
                })
    return result, report


async def align_localization_transcript(
    media_path: Path | str,
    transcript: LocalizationTranscript,
    profile: LocalizationProfile,
    *,
    huggingface_token: str = "",
) -> tuple[LocalizationTranscript, AlignmentReport]:
    return await asyncio.to_thread(
        _align_sync,
        Path(media_path),
        transcript,
        profile,
        huggingface_token,
    )
