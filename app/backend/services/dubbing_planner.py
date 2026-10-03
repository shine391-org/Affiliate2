# SPDX-License-Identifier: AGPL-3.0-only
"""Donor-derived dubbing planner and timeline fitter.

Behavior adapted from OpenCreator/Krillin Smart Dubbing (Apache-2.0):
runtime/krillinai/internal/service/dubbing/{types,planner,fit,audio}.go

Affiliate2-specific policy change:
- chunks that still require speed above speed_max after rewrite are explicit
  review/failure candidates instead of being silently forced beyond the limit.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass(slots=True)
class DubbingConfig:
    min_cue_duration_ms: int = 2500
    max_chunk_size: int = 5
    gap_tolerance_ms: int = 1500
    speed_accept: float = 1.15
    speed_max: float = 1.30
    # Emergency hard ceiling. Unlike speed_max this is NOT a quality promise: it
    # only decides whether a chunk that already exhausted every fitting strategy
    # may still be rendered so a human can review it. Anything above it is a hard
    # failure.
    emergency_speed_max: float = 1.35
    rewrite_max_attempts: int = 2

    def validate(self) -> None:
        if self.min_cue_duration_ms <= 0:
            raise ValueError("min_cue_duration_ms must be > 0")
        if self.max_chunk_size <= 0:
            raise ValueError("max_chunk_size must be > 0")
        if self.gap_tolerance_ms < 0:
            raise ValueError("gap_tolerance_ms must be >= 0")
        if not (1.0 <= self.speed_accept <= self.speed_max <= 2.0):
            raise ValueError("invalid dubbing speed policy")
        if self.emergency_speed_max < self.speed_max:
            raise ValueError("emergency_speed_max must be >= speed_max")
        if self.rewrite_max_attempts < 0:
            raise ValueError("rewrite_max_attempts must be >= 0")


@dataclass(slots=True)
class DubbingCue:
    index: int
    segment_id: str
    start_ms: int
    end_ms: int
    text: str
    speaker_id: str | None = None
    voice_id: str | None = None
    source_text: str = ""
    timing_locked: bool = False

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(slots=True)
class PlanItem:
    index: int
    segment_id: str
    original_start_ms: int
    original_end_ms: int
    original_text: str
    spoken_text: str
    speaker_id: str | None = None
    voice_id: str | None = None
    estimated_duration_ms: int = 0
    actual_duration_ms: int = 0
    target_start_ms: int = 0
    target_end_ms: int = 0
    speed_factor: float = 1.0
    chunk_id: int = 0
    rewrite_attempts: int = 0
    warning: str = ""
    timing_locked: bool = False


@dataclass(slots=True)
class DubbingChunk:
    id: int
    item_indexes: list[int]
    start_ms: int
    end_ms: int
    speaker_id: str | None = None
    voice_id: str | None = None
    actual_duration_ms: int = 0
    speed_factor: float = 1.0

    @property
    def available_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(slots=True)
class DubbingReport:
    warnings: list[str] = field(default_factory=list)
    failed_chunk_ids: list[int] = field(default_factory=list)
    max_speed_factor: float = 1.0
    rewrite_count: int = 0


# Calibration bounds. A coefficient belongs to ONE voice + ONE video, so it is
# clamped hard: a wild value would do more damage than the raw estimate.
_CALIBRATION_MIN_SAMPLES = 3
_CALIBRATION_MIN_RATE = 0.60
_CALIBRATION_MAX_RATE = 1.80


@dataclass(slots=True)
class DubbingRateCalibrator:
    """Learns this job's real speech rate from measured TTS durations.

    `estimate_spoken_ms` uses fixed per-language constants that know nothing
    about the actual voice, provider or speaking style. Measured evidence on a
    real job put actual/estimated between 0.77x and 2.10x. Rather than invent a
    better constant, calibrate from the measurements the job already makes:
    every synthesized chunk stores `actual_duration_ms`.

        calibrated_rate = sum(actual) / sum(estimated)   # measured chunks only

    The coefficient is deliberately NOT shared across jobs -- a slow narrator
    must not silently re-rate a fast one.
    """

    samples: int = 0
    total_estimated_ms: int = 0
    total_actual_ms: int = 0
    #: Persisted so a rerun resumes with prior knowledge instead of relearning.
    persisted_rate: float | None = None

    def observe(self, estimated_ms: int, actual_ms: int) -> None:
        """Record one measured chunk. Non-positive measurements are ignored."""
        if estimated_ms <= 0 or actual_ms <= 0:
            return
        self.samples += 1
        self.total_estimated_ms += int(estimated_ms)
        self.total_actual_ms += int(actual_ms)

    @property
    def rate(self) -> float:
        """Calibrated actual/estimated ratio, clamped to safe bounds.

        Uses total/total rather than mean-of-ratios so one pathological short
        chunk cannot dominate the coefficient.
        """
        rate = 1.0
        if self.samples >= _CALIBRATION_MIN_SAMPLES and self.total_estimated_ms > 0:
            rate = self.total_actual_ms / self.total_estimated_ms
        elif self.persisted_rate:
            rate = float(self.persisted_rate)
        return max(_CALIBRATION_MIN_RATE, min(_CALIBRATION_MAX_RATE, rate))

    @property
    def is_trustworthy(self) -> bool:
        """Enough measurements for telemetry; never authority to rewrite text."""
        return self.samples >= _CALIBRATION_MIN_SAMPLES or bool(self.persisted_rate)

    def calibrated_ms(self, estimated_ms: int) -> int:
        """Apply the learned rate to a fresh estimate."""
        return int(round(estimated_ms * self.rate))

    def as_dict(self) -> dict:
        return {
            "samples": self.samples,
            "total_estimated_ms": self.total_estimated_ms,
            "total_actual_ms": self.total_actual_ms,
            "rate": round(self.rate, 4),
            "is_trustworthy": self.is_trustworthy,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "DubbingRateCalibrator":
        if not isinstance(data, dict):
            return cls()
        try:
            return cls(
                samples=int(data.get("samples") or 0),
                total_estimated_ms=int(data.get("total_estimated_ms") or 0),
                total_actual_ms=int(data.get("total_actual_ms") or 0),
                persisted_rate=(
                    float(data["rate"]) if data.get("rate") else None
                ),
            )
        except (TypeError, ValueError):
            return cls()


_STRONG_END = re.compile(r"[.!?。！？…][\"'”’」』）】》)\]}»]*\s*$")

# Semantic segmentation already treats gaps up to 350 ms as a continuous thought.
# Preserve that continuity through TTS so a sentence split into two healthy-sized
# cues does not become two independent syntheses with a prosody reset between them.
_NATURAL_CONTINUATION_GAP_MS = 350
_NATURAL_CONTINUATION_MAX_SPAN_MS = 8000
_MAX_NATURAL_CUE_OVERLAP_MS = 120


def clean_text_for_speech(text: str) -> str:
    return " ".join((text or "").replace("\r", " ").replace("\n", " ").split()).strip()


def estimate_spoken_ms(text: str, language: str = "Vietnamese") -> int:
    """Statistical/heuristic pre-TTS estimate inspired by OpenCreator.

    KEEP as a budget hint only. Measured evidence on a real 29-chunk job showed
    actual/estimated spanning 0.77x-2.10x (median 0.99x), and the estimate was
    LOWER than reality on 15/29 chunks. It therefore must never decide whether
    approved text is rewritten -- see DubbingRateCalibrator.

    Actual synthesized duration always remains authoritative.
    """
    clean = clean_text_for_speech(text)
    if not clean:
        return 0

    lang = (language or "").lower()
    if "viet" in lang or lang in {"vi", "vi-vn"}:
        # Vietnamese is space-delimited; natural short-form speech commonly
        # lands around 3.2–4.0 words/s before punctuation pauses.
        words = len(clean.split())
        base = words / 3.6
    elif any("\u4e00" <= ch <= "\u9fff" for ch in clean):
        chars = sum(1 for ch in clean if not ch.isspace())
        base = chars / 4.2
    else:
        words = max(1, len(clean.split()))
        base = words / 2.7

    pause = 0.0
    for ch in clean:
        if ch in ",，、;；:：":
            pause += 0.07
        elif ch in ".。!?！？":
            pause += 0.12
        elif ch in "…—":
            pause += 0.16
    return max(180, round((base + pause) * 1000))


def build_plan(
    cues: list[DubbingCue],
    config: DubbingConfig,
    *,
    language: str = "Vietnamese",
    timeline_end_ms: int | None = None,
    split_before_segment_ids: set[str] | None = None,
) -> tuple[list[PlanItem], list[DubbingChunk]]:
    config.validate()
    ordered = sorted(cues, key=lambda x: (x.start_ms, x.index))
    if not ordered:
        return [], []

    for cue in ordered:
        if cue.start_ms < 0 or cue.end_ms <= cue.start_ms:
            raise ValueError(f"invalid cue interval for {cue.segment_id}")

    plan = [
        PlanItem(
            index=cue.index,
            segment_id=cue.segment_id,
            original_start_ms=cue.start_ms,
            original_end_ms=cue.end_ms,
            original_text=cue.text,
            spoken_text=clean_text_for_speech(cue.text),
            speaker_id=cue.speaker_id,
            voice_id=cue.voice_id,
            estimated_duration_ms=estimate_spoken_ms(cue.text, language),
            timing_locked=cue.timing_locked,
        )
        for cue in ordered
    ]

    chunks: list[DubbingChunk] = []
    current = DubbingChunk(
        id=1,
        item_indexes=[0],
        start_ms=ordered[0].start_ms,
        end_ms=ordered[0].end_ms,
        speaker_id=ordered[0].speaker_id,
        voice_id=ordered[0].voice_id,
    )

    for i in range(1, len(ordered)):
        prev = ordered[i - 1]
        cue = ordered[i]
        gap = cue.start_ms - prev.end_ms
        natural_gap = (
            -_MAX_NATURAL_CUE_OVERLAP_MS <= gap
            <= min(config.gap_tolerance_ms, _NATURAL_CONTINUATION_GAP_MS)
        )
        sentence_boundary = bool(
            _STRONG_END.search(prev.text)
            or _STRONG_END.search(prev.source_text or "")
        )
        continuation_span_ms = max(current.end_ms, cue.end_ms) - current.start_ms
        should_merge_continuation = (
            not sentence_boundary
            and natural_gap
            and continuation_span_ms <= _NATURAL_CONTINUATION_MAX_SPAN_MS
        )
        # Complete sentences are boundaries regardless of cue duration.
        # Viability is handled separately, using actual silence budgets.
        strong_boundary = sentence_boundary
        speaker_change = prev.speaker_id != cue.speaker_id
        # An explicit voice override must never bleed into a neighbouring
        # default-voice cue (or vice versa). None is meaningful here: it means
        # "resolve the profile/speaker default", so None <-> explicit is a
        # hard boundary as well.
        voice_change = prev.voice_id != cue.voice_id
        must_split = (
            cue.segment_id in (split_before_segment_ids or set())
            or len(current.item_indexes) >= config.max_chunk_size
            or continuation_span_ms > _NATURAL_CONTINUATION_MAX_SPAN_MS
            or not should_merge_continuation
            or prev.timing_locked or cue.timing_locked
            or strong_boundary
            or speaker_change
            or voice_change
        )
        if must_split:
            chunks.append(current)
            current = DubbingChunk(
                id=len(chunks) + 1,
                item_indexes=[i],
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                speaker_id=cue.speaker_id,
                voice_id=cue.voice_id,
            )
        else:
            current.item_indexes.append(i)
            current.end_ms = max(current.end_ms, cue.end_ms)

    chunks.append(current)
    allocate_silence(plan, chunks, config, timeline_end_ms=timeline_end_ms)

    # Only rescue a still-impossible continuation after allocating silence.
    # Never join text across a real pause, sentence, speaker, voice or lock.
    # A size/span split may still need one short continuation appended; that
    # exception remains bounded by max_chunk_size + 1 and the span ceiling.
    for chunk_pos in range(len(chunks) - 1, 0, -1):
        chunk = chunks[chunk_pos]
        previous = chunks[chunk_pos - 1]
        if len(chunk.item_indexes) != 1:
            continue
        orphan_idx = chunk.item_indexes[0]
        orphan = plan[orphan_idx]
        orphan_window_ms = chunk.available_ms
        if orphan.estimated_duration_ms <= orphan_window_ms * config.speed_max:
            continue
        previous_last = ordered[previous.item_indexes[-1]]
        orphan_cue = ordered[orphan_idx]
        orphan_gap = orphan_cue.start_ms - previous_last.end_ms
        if (
            orphan_cue.segment_id in (split_before_segment_ids or set())
            or previous_last.speaker_id != orphan_cue.speaker_id
            or previous_last.voice_id != orphan_cue.voice_id
            or orphan_gap < -_MAX_NATURAL_CUE_OVERLAP_MS
            or orphan_gap > min(config.gap_tolerance_ms, _NATURAL_CONTINUATION_GAP_MS)
            or previous_last.timing_locked or orphan_cue.timing_locked
            or _STRONG_END.search(previous_last.text)
            or _STRONG_END.search(previous_last.source_text)
        ):
            continue
        rescue_span_ms = max(previous.end_ms, chunk.end_ms) - previous.start_ms
        if (
            len(previous.item_indexes) >= config.max_chunk_size + 1
            or previous.available_ms > _NATURAL_CONTINUATION_MAX_SPAN_MS
            or (
                rescue_span_ms > _NATURAL_CONTINUATION_MAX_SPAN_MS
                and (
                    orphan_window_ms >= config.min_cue_duration_ms
                    or rescue_span_ms > (
                        _NATURAL_CONTINUATION_MAX_SPAN_MS
                        + config.gap_tolerance_ms + config.min_cue_duration_ms
                    )
                )
            )
        ):
            continue
        previous.item_indexes.extend(chunk.item_indexes)
        previous.end_ms = max(previous.end_ms, chunk.end_ms)
        chunks.pop(chunk_pos)

    # Rescue may remove an overlap pivot that capped a split chunk. Rebuild
    # windows for the final grouping so that cap cannot truncate a merged cue.
    allocate_silence(plan, chunks, config, timeline_end_ms=timeline_end_ms)

    # Merging an intermediate orphan removes a chunk, so normalize IDs before
    # persisting plans/artifact names and before targeted chunk regeneration can
    # address them later.
    for chunk_id, chunk in enumerate(chunks, start=1):
        chunk.id = chunk_id
        for idx in chunk.item_indexes:
            plan[idx].chunk_id = chunk.id
    return plan, chunks




def allocate_silence(
    plan: list[PlanItem],
    chunks: list[DubbingChunk],
    config: DubbingConfig,
    *,
    timeline_end_ms: int | None = None,
    only_chunk_ids: set[int] | None = None,
) -> list[DubbingChunk]:
    """Demand-driven allocation on the same chunk timeline, before text rescue.

    Prefer following silence so onset stays anchored; use preceding silence only
    if necessary. Retain min(source gap, 350ms) at every separated speech boundary.
    Recompute selected windows from source anchors on every call, reclaiming
    estimated slack once measured audio is available. Spend trailing slack toward
    1.0x first; only borrow before onset to reach the acceptable speed ceiling.
    Borrow limits are relative to originals, not the previous allocation.
    Expert timing locks are immutable. Insufficient budget remains an overflow
    for the canonical rewrite/retime/QC path, never an excuse to erase a pause.
    """
    config.validate()
    if timeline_end_ms is not None and timeline_end_ms < 0:
        raise ValueError("timeline_end_ms must be >= 0")
    original = [
        (min(plan[idx].original_start_ms for idx in c.item_indexes),
         max(plan[idx].original_end_ms for idx in c.item_indexes))
        for c in chunks
    ]
    locked = [any(plan[idx].timing_locked for idx in c.item_indexes) for c in chunks]
    immutable = [
        locked[pos] or (only_chunk_ids is not None and c.id not in only_chunk_ids)
        for pos, c in enumerate(chunks)
    ]
    for pos, chunk in enumerate(chunks):
        if not immutable[pos]:
            chunk.start_ms, chunk.end_ms = original[pos]

    # Alignment may overlap by <=120ms even when a hard boundary forces split.
    # Keep the later onset and cap the earlier flexible slot; if the earlier
    # slot is immutable, move only the later flexible onset instead. Originals
    # remain source coordinates, so measured retries normalize the same pivot.
    for pos in range(1, len(chunks)):
        previous, chunk = chunks[pos - 1], chunks[pos]
        overlap = previous.end_ms - chunk.start_ms
        if overlap <= 0:
            continue
        if immutable[pos - 1] and immutable[pos]:
            reason = "expert timing conflict" if locked[pos - 1] and locked[pos] else "immutable timing conflict"
            raise ValueError(f"{reason}: chunks {previous.id} and {chunk.id} overlap by {overlap}ms")
        if overlap > _MAX_NATURAL_CUE_OVERLAP_MS:
            raise ValueError(
                f"chunk timestamp overlap {overlap}ms exceeds "
                f"{_MAX_NATURAL_CUE_OVERLAP_MS}ms tolerance: chunks {previous.id} and {chunk.id}"
            )
        if not immutable[pos - 1]:
            if chunk.start_ms <= previous.start_ms:
                raise ValueError(f"timing conflict: chunk {previous.id} has no positive window after overlap normalization")
            previous.end_ms = chunk.start_ms
        else:
            if previous.end_ms >= chunk.end_ms:
                raise ValueError(f"timing conflict: chunk {chunk.id} has no positive window after overlap normalization")
            chunk.start_ms = previous.end_ms
    # Allocate the later cue first: it may need the tail of the preceding gap,
    # which the preceding cue must not consume before that request is known.
    for pos in range(len(chunks) - 1, -1, -1):
        chunk = chunks[pos]
        if only_chunk_ids is not None and chunk.id not in only_chunk_ids:
            continue
        if any(plan[idx].timing_locked for idx in chunk.item_indexes):
            continue
        start, end = original[pos]
        duration = chunk.actual_duration_ms or sum(
            plan[idx].estimated_duration_ms for idx in chunk.item_indexes
        )
        needed = max(0, duration - chunk.available_ms)
        if not needed:
            continue
        span_limit = max(_NATURAL_CONTINUATION_MAX_SPAN_MS, end - start)
        if pos + 1 < len(chunks):
            source_gap = max(0, original[pos + 1][0] - end)
            reserve = min(source_gap, _NATURAL_CONTINUATION_GAP_MS)
            boundary = chunks[pos + 1].start_ms - reserve
        else:
            boundary = end if timeline_end_ms is None else timeline_end_ms
        extension = max(0, min(
            needed, boundary - chunk.end_ms,
            end + config.gap_tolerance_ms - chunk.end_ms,
            span_limit - chunk.available_ms,
        ))
        chunk.end_ms += extension
        # Prefer a mild speed-up to moving onset unnecessarily. Following
        # silence, when sufficient, has already provided the full natural slot.
        needed = max(0, math.ceil(duration / config.speed_accept) - chunk.available_ms)
        if not needed:
            continue
        if pos:
            source_gap = max(0, start - original[pos - 1][1])
            reserve = min(source_gap, _NATURAL_CONTINUATION_GAP_MS)
            boundary = chunks[pos - 1].end_ms + reserve
        else:
            boundary = 0
        extension = max(0, min(
            needed, chunk.start_ms - boundary,
            chunk.start_ms - max(0, start - config.gap_tolerance_ms),
            span_limit - chunk.available_ms,
        ))
        chunk.start_ms -= extension
    return chunks


def borrow_following_silence(
    chunks: list[DubbingChunk],
    *,
    max_borrow_ms: int,
    timeline_end_ms: int | None = None,
) -> list[DubbingChunk]:
    """Extend chunks into otherwise-unused silence without crossing speech.

    Intermediate chunks may borrow only up to the next speech start. The last
    chunk may also borrow real trailing source-media silence when the canonical
    timeline end is known. Speaker/voice boundaries remain hard boundaries
    because this function only extends an already-planned chunk.
    """
    if max_borrow_ms < 0:
        raise ValueError("max_borrow_ms must be >= 0")
    if timeline_end_ms is not None and timeline_end_ms < 0:
        raise ValueError("timeline_end_ms must be >= 0")

    for i, chunk in enumerate(chunks):
        if i + 1 < len(chunks):
            boundary = chunks[i + 1].start_ms
        elif timeline_end_ms is not None:
            boundary = timeline_end_ms
        else:
            continue
        gap = max(0, boundary - chunk.end_ms)
        chunk.end_ms += min(gap, max_borrow_ms)
    return chunks

def chunk_text(plan: list[PlanItem], chunk: DubbingChunk) -> str:
    parts = [
        clean_text_for_speech(plan[idx].spoken_text)
        for idx in chunk.item_indexes
        if clean_text_for_speech(plan[idx].spoken_text)
    ]
    return " ".join(parts)


def required_speed(actual_duration_ms: int, available_ms: int) -> float:
    if actual_duration_ms <= 0 or available_ms <= 0:
        raise ValueError("actual and available durations must be positive")
    return max(1.0, actual_duration_ms / available_ms)


def fit_timeline(
    plan: list[PlanItem],
    chunks: list[DubbingChunk],
    config: DubbingConfig,
    *,
    soft_overflow_chunk_ids: set[int] | None = None,
) -> tuple[list[PlanItem], list[DubbingChunk], DubbingReport]:
    """Fit measured chunk audio and redistribute fitted time to cue items.

    A chunk listed in ``soft_overflow_chunk_ids`` already exhausted every
    fitting strategy and sits between the normal ceiling and the emergency
    ceiling. It is fitted up to the emergency ceiling rather than being forced
    back to ``speed_max`` (which would still overflow its window) and is
    reported separately from a hard overflow.
    """
    config.validate()
    report = DubbingReport()
    soft_ids = soft_overflow_chunk_ids or set()
    last_end = 0

    for chunk in chunks:
        if not chunk.item_indexes:
            raise ValueError(f"chunk {chunk.id} has no items")
        if chunk.start_ms < last_end:
            raise ValueError(f"chunk {chunk.id} overlaps previous fitted chunk")
        if chunk.actual_duration_ms <= 0:
            raise ValueError(f"chunk {chunk.id} has no measured audio duration")
        if chunk.available_ms <= 0:
            raise ValueError(f"chunk {chunk.id} has non-positive window")

        speed = required_speed(chunk.actual_duration_ms, chunk.available_ms)
        report.max_speed_factor = max(report.max_speed_factor, speed)

        if speed > config.speed_accept:
            report.warnings.append(
                f"chunk {chunk.id} speed {speed:.2f} exceeds acceptable {config.speed_accept:.2f}"
            )
        if speed > config.speed_max:
            report.failed_chunk_ids.append(chunk.id)
            report.warnings.append(
                f"chunk {chunk.id} speed {speed:.2f} exceeds max {config.speed_max:.2f}"
            )

        # Never silently force beyond the quality ceiling. A soft-overflow chunk
        # is allowed up to the emergency ceiling ONLY because it was already
        # classified for mandatory human review before this call.
        ceiling = config.speed_max
        if chunk.id in soft_ids:
            ceiling = config.emergency_speed_max
        applied = min(speed, ceiling)
        chunk.speed_factor = applied
        fitted_total = round(chunk.actual_duration_ms / applied)

        weights: list[float] = []
        for idx in chunk.item_indexes:
            item = plan[idx]
            weight = item.estimated_duration_ms
            if weight <= 0:
                weight = max(1, len(item.spoken_text))
            weights.append(float(weight))
        total_weight = sum(weights) or float(len(weights))

        cursor = chunk.start_ms
        remaining = fitted_total
        for pos, idx in enumerate(chunk.item_indexes):
            if pos == len(chunk.item_indexes) - 1:
                duration = remaining
            else:
                duration = max(1, round(fitted_total * weights[pos] / total_weight))
                remaining -= duration
            item = plan[idx]
            item.target_start_ms = cursor
            item.target_end_ms = cursor + duration
            item.speed_factor = applied
            cursor = item.target_end_ms

        last_end = cursor

    return plan, chunks, report


def atempo_filter(speed: float) -> str:
    """Build FFmpeg atempo chain, adapted from OpenCreator audio.go."""
    if speed <= 0 or not math.isfinite(speed):
        raise ValueError("speed must be finite and > 0")
    parts: list[str] = []
    remaining = speed
    while remaining > 2.0:
        parts.append("atempo=2.000")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.500")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.3f}")
    return ",".join(parts)
