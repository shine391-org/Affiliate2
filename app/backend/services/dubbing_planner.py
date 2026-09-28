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


_STRONG_END = re.compile(r"[.!?。！？…][\"'”’」』）】》]*\s*$")


def clean_text_for_speech(text: str) -> str:
    return " ".join((text or "").replace("\r", " ").replace("\n", " ").split()).strip()


def estimate_spoken_ms(text: str, language: str = "Vietnamese") -> int:
    """Statistical/heuristic pre-TTS estimate inspired by OpenCreator.

    Actual synthesized duration remains authoritative; this estimate is only
    used to decide whether a bounded rewrite is worth attempting before TTS.
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
        should_merge_short = (
            gap <= config.gap_tolerance_ms
            and (
                prev.duration_ms < config.min_cue_duration_ms
                or cue.duration_ms < config.min_cue_duration_ms
            )
        )
        # Strong sentence boundaries are useful only when they do not strand
        # a micro-cue. A very short following cue must remain mergeable with
        # the previous chunk (same speaker/voice, within the gap tolerance),
        # otherwise sub-second utterances can become impossible dubbing slots.
        strong_boundary = (
            bool(_STRONG_END.search(prev.text))
            and prev.duration_ms >= config.min_cue_duration_ms
            and cue.duration_ms >= config.min_cue_duration_ms
        )
        speaker_change = (
            prev.speaker_id is not None
            and cue.speaker_id is not None
            and prev.speaker_id != cue.speaker_id
        )
        # An explicit voice override must never bleed into a neighbouring
        # default-voice cue (or vice versa). None is meaningful here: it means
        # "resolve the profile/speaker default", so None <-> explicit is a
        # hard boundary as well.
        voice_change = prev.voice_id != cue.voice_id
        must_split = (
            len(current.item_indexes) >= config.max_chunk_size
            or not should_merge_short
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
            current.end_ms = cue.end_ms

    chunks.append(current)

    # A hard max-size split can strand a final aligned syllable in a window
    # far shorter than even its conservative speech estimate (real regression:
    # b8193843 left Vietnamese "sẽ" in 20ms). When the split itself created an
    # impossible singleton and no speaker/voice/gap boundary exists, merge that
    # one cue back into the preceding chunk. The exceptional chunk is at most
    # max_chunk_size + 1; preserving a viable speech window is more important
    # than enforcing a soft synthesis batch size that created invalid timing.
    for chunk_pos in range(len(chunks) - 1, 0, -1):
        chunk = chunks[chunk_pos]
        previous = chunks[chunk_pos - 1]
        if len(chunk.item_indexes) != 1:
            continue
        orphan_idx = chunk.item_indexes[0]
        orphan = plan[orphan_idx]
        orphan_window_ms = orphan.original_end_ms - orphan.original_start_ms
        if orphan.estimated_duration_ms <= orphan_window_ms * config.speed_max:
            continue
        previous_last = ordered[previous.item_indexes[-1]]
        orphan_cue = ordered[orphan_idx]
        if (
            previous_last.speaker_id != orphan_cue.speaker_id
            or previous_last.voice_id != orphan_cue.voice_id
            or orphan_cue.start_ms - previous_last.end_ms > config.gap_tolerance_ms
        ):
            continue
        previous.item_indexes.extend(chunk.item_indexes)
        previous.end_ms = chunk.end_ms
        chunks.pop(chunk_pos)

    # Merging an intermediate orphan removes a chunk, so normalize IDs before
    # persisting plans/artifact names and before targeted chunk regeneration can
    # address them later.
    for chunk_id, chunk in enumerate(chunks, start=1):
        chunk.id = chunk_id
        for idx in chunk.item_indexes:
            plan[idx].chunk_id = chunk.id
    return plan, chunks




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
) -> tuple[list[PlanItem], list[DubbingChunk], DubbingReport]:
    """Fit measured chunk audio and redistribute fitted time to cue items."""
    config.validate()
    report = DubbingReport()
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

        # Never silently force beyond the quality ceiling. Caller must rewrite,
        # regenerate, or route to review before assembly.
        applied = min(speed, config.speed_max)
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
