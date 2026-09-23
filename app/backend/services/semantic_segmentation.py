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



def _merge_group(segments: list[LocalizationSegment], group_index: int) -> LocalizationSegment:
    if not segments:
        raise ValueError("semantic group cannot be empty")
    first, last = segments[0], segments[-1]
    speaker_ids = {seg.speaker_id for seg in segments}
    speaker_id = first.speaker_id if len(speaker_ids) == 1 else None
    return LocalizationSegment(
        id=f"sem-{group_index:04d}",
        speaker_id=speaker_id,
        source_text=" ".join(seg.source_text for seg in segments).strip(),
        source_start_ms=first.source_start_ms,
        source_end_ms=last.source_end_ms,
        source_words=[w for seg in segments for w in seg.source_words],
    )


def _validate_group_ids(
    payload,
    expected_ids: list[str],
) -> list[list[str]]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("semantic grouping must be a non-empty JSON array")
    flat: list[str] = []
    groups: list[list[str]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("semantic group must be an object")
        ids = item.get("ids")
        if not isinstance(ids, list) or not ids:
            raise ValueError("semantic group ids must be a non-empty list")
        group = [str(x) for x in ids]
        groups.append(group)
        flat.extend(group)
    if flat != expected_ids:
        raise ValueError("semantic grouping must preserve every cue ID exactly once and in order")
    return groups


async def refine_semantic_cues_with_ai(
    transcript: LocalizationTranscript,
    ai,
    *,
    window_size: int = 12,
) -> LocalizationTranscript:
    """SmartSub-style AI boundary refinement with strict structural validation.

    The model is never allowed to rewrite source text. It can only group
    existing cue IDs. Any invalid/provider response degrades to the deterministic
    normalized cues for that window.
    """
    base = normalize_semantic_cues(transcript)
    if len(base.segments) <= 1:
        return base

    output: list[LocalizationSegment] = []
    group_index = 1
    for start in range(0, len(base.segments), window_size):
        window = base.segments[start:start + window_size]
        if len(window) == 1:
            output.append(_merge_group(window, group_index))
            group_index += 1
            continue

        items = [
            {
                "id": seg.id,
                "text": seg.source_text,
                "start_ms": seg.source_start_ms,
                "end_ms": seg.source_end_ms,
            }
            for seg in window
        ]
        prompt = f"""You are segmenting speech into semantic subtitle/translation units.

You may ONLY group adjacent cue IDs. You MUST NOT rewrite, translate, omit, duplicate, or reorder any cue.

Prefer a new group at:
- a complete thought or sentence boundary;
- a meaningful pause/topic turn;
- a speaker change when visible in the IDs/data.

Merge tiny fragments that depend on neighboring words. Avoid creating overly long groups.

INPUT:
{__import__("json").dumps(items, ensure_ascii=False)}

Return ONLY JSON:
[{{"ids":["cue-id-1","cue-id-2"]}}, {{"ids":["cue-id-3"]}}]
Every input ID must appear exactly once and in the same order."""

        try:
            raw = await ai.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1200,
            )
            text = (raw or "").strip()
            if text.startswith("```"):
                lines = text.splitlines()
                lines = lines[1:] if lines else lines
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                text = "\n".join(lines).strip()
            payload = __import__("json").loads(text)
            groups = _validate_group_ids(payload, [seg.id for seg in window])
        except Exception:
            groups = [[seg.id] for seg in window]

        by_id = {seg.id: seg for seg in window}
        for ids in groups:
            output.append(
                _merge_group([by_id[seg_id] for seg_id in ids], group_index)
            )
            group_index += 1

    refined = base.model_copy(deep=True)
    refined.segments = output
    refined.text = " ".join(seg.source_text for seg in output).strip()
    return refined
