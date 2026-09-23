# SPDX-License-Identifier: AGPL-3.0-only
"""Source cue normalization derived from VideoLingo/SmartSub segmentation ideas.

Raw ASR segments are acquisition artifacts, not sacred semantic boundaries.
This module uses real word times when available, splits very long cues, and
merges tiny adjacent cues before translation. It never rewrites source text.
"""
from __future__ import annotations

import re

from backend.core.localization import (
    LocalizationSegment,
    LocalizationTranscript,
)

_STRONG_END = re.compile(r"[.!?。！？…][\"'”’」』）】》]*\s*$")
_PUNCT = re.compile(r"[,;:，；：.!?。！？…]$")


def _word_ms(word: dict, key: str) -> int | None:
    value = word.get(key)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return round(seconds * 1000)


def _split_long_segment(
    seg: LocalizationSegment,
    *,
    max_duration_ms: int,
) -> list[LocalizationSegment]:
    words = [w for w in seg.source_words if isinstance(w, dict)]
    if seg.source_end_ms - seg.source_start_ms <= max_duration_ms or len(words) < 2:
        return [seg.model_copy(deep=True)]

    groups: list[list[dict]] = []
    current: list[dict] = []
    start_ms: int | None = None

    for word in words:
        w_start = _word_ms(word, "start")
        w_end = _word_ms(word, "end")
        if w_start is None or w_end is None or w_end < w_start:
            continue
        if start_ms is None:
            start_ms = w_start
        current.append(word)
        elapsed = w_end - start_ms
        token = str(word.get("word") or "").strip()
        boundary = elapsed >= max_duration_ms or (
            elapsed >= max_duration_ms * 0.55 and _PUNCT.search(token)
        )
        if boundary:
            groups.append(current)
            current = []
            start_ms = None

    if current:
        groups.append(current)
    if len(groups) <= 1:
        return [seg.model_copy(deep=True)]

    out: list[LocalizationSegment] = []
    for idx, group in enumerate(groups, start=1):
        start = _word_ms(group[0], "start")
        end = _word_ms(group[-1], "end")
        if start is None or end is None or end <= start:
            continue
        text = "".join(str(w.get("word") or "") for w in group).strip()
        if not text:
            continue
        out.append(LocalizationSegment(
            id=f"{seg.id}.{idx}",
            speaker_id=seg.speaker_id,
            source_text=text,
            source_start_ms=start,
            source_end_ms=end,
            source_words=group,
        ))
    return out or [seg.model_copy(deep=True)]


def normalize_semantic_cues(
    transcript: LocalizationTranscript,
    *,
    min_cue_duration_ms: int = 700,
    merge_gap_ms: int = 350,
    max_cue_duration_ms: int = 6000,
) -> LocalizationTranscript:
    """Create translation-friendly semantic cues without changing source text."""
    expanded: list[LocalizationSegment] = []
    for seg in transcript.segments:
        expanded.extend(
            _split_long_segment(seg, max_duration_ms=max_cue_duration_ms)
        )
    if not expanded:
        return transcript.model_copy(deep=True)

    merged: list[LocalizationSegment] = []
    for seg in expanded:
        if not merged:
            merged.append(seg.model_copy(deep=True))
            continue

        prev = merged[-1]
        gap = seg.source_start_ms - prev.source_end_ms
        prev_duration = prev.source_end_ms - prev.source_start_ms
        cur_duration = seg.source_end_ms - seg.source_start_ms
        combined_duration = seg.source_end_ms - prev.source_start_ms
        tiny = prev_duration < min_cue_duration_ms or cur_duration < min_cue_duration_ms
        strong_boundary = bool(_STRONG_END.search(prev.source_text))

        if (
            tiny
            and gap <= merge_gap_ms
            and combined_duration <= max_cue_duration_ms
            and not strong_boundary
            and prev.speaker_id == seg.speaker_id
        ):
            prev.source_text = f"{prev.source_text} {seg.source_text}".strip()
            prev.source_end_ms = seg.source_end_ms
            prev.source_words = [*prev.source_words, *seg.source_words]
            continue

        merged.append(seg.model_copy(deep=True))

    out = transcript.model_copy(deep=True)
    out.segments = merged
    out.text = " ".join(seg.source_text for seg in merged).strip()
    return out
