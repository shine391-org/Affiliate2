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


def _collapse_ws(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _timed_words(transcript: LocalizationTranscript) -> list[dict]:
    """Flatten source words with stable IDs, timestamps and parent speaker."""
    out: list[dict] = []
    number = 1
    for seg in transcript.segments:
        for raw in seg.source_words:
            if not isinstance(raw, dict):
                continue
            start = _word_ms(raw, "start")
            end = _word_ms(raw, "end")
            raw_text = str(raw.get("word") or "")
            text = raw_text.strip()
            if start is None or end is None or end <= start or not text:
                continue
            out.append({
                "id": f"w{number:05d}",
                "text": text,
                "raw_text": raw_text,
                "start_ms": start,
                "end_ms": end,
                "speaker_id": raw.get("speaker_id") or seg.speaker_id,
                "raw": raw,
            })
            number += 1
    return out


def _deterministic_word_groups(words: list[dict]) -> list[list[str]]:
    """Fallback grouping that preserves speaker boundaries without fragmenting words.

    AI failure must not collapse a multi-speaker aligned segment back into one
    parent cue. Group contiguous words, split on a known speaker change, and
    prefer strong sentence punctuation / a bounded semantic duration.
    """
    if not words:
        return []

    groups: list[list[str]] = []
    current: list[str] = []
    current_speaker = None
    group_start = None

    for word in words:
        speaker = word.get("speaker_id")
        if current and speaker is not None and current_speaker is not None and speaker != current_speaker:
            groups.append(current)
            current = []
            group_start = None

        if not current:
            current_speaker = speaker
            group_start = int(word["start_ms"])
        elif current_speaker is None and speaker is not None:
            current_speaker = speaker

        current.append(word["id"])
        token = str(word.get("text") or "")
        elapsed = int(word["end_ms"]) - int(group_start or word["start_ms"])
        if _STRONG_END.search(token) or elapsed >= 6000:
            groups.append(current)
            current = []
            current_speaker = None
            group_start = None

    if current:
        groups.append(current)
    return groups


def _join_words_preserve_spacing(words: list[dict]) -> str:
    """Join grouped word tokens preserving original whitespace conventions.

    English Whisper tokens typically carry a leading space on non-initial words
    ("Hello", " there"). Concatenating those preserves correct spacing without
    introducing it. When tokens lack leading spaces (e.g. test fixtures or some
    non-Whisper tokenizers), English would collapse into "Hellothere." if we
    concatenated blindly.

    CJK tokens never carry inter-word whitespace, so they must always be
    concatenated. We use a CJK-detection heuristic: if the raw concatenated
    text contains CJK characters, no space should be inserted between tokens.
    Otherwise (bare Latin/Cyrillic/etc tokens), join with a single space.
    """
    raw_parts = [w.get("raw_text", w["text"]) for w in words]
    if any(p[:1].isspace() for p in raw_parts):
        return _collapse_ws("".join(raw_parts))
    compact = "".join(w["text"].strip() for w in words)
    if any("一" <= ch <= "鿿" for ch in compact):
        return compact
    return _collapse_ws(" ".join(w["text"].strip() for w in words))


def _word_groups_to_segments(
    groups: list[list[str]],
    words: list[dict],
    *,
    start_group_index: int = 1,
) -> list[LocalizationSegment]:
    by_id = {w["id"]: w for w in words}
    out: list[LocalizationSegment] = []
    for offset, ids in enumerate(groups):
        group = [by_id[word_id] for word_id in ids]
        speaker_ids = {w["speaker_id"] for w in group}
        speaker_id = next(iter(speaker_ids)) if len(speaker_ids) == 1 else None
        text = _join_words_preserve_spacing(group)
        out.append(LocalizationSegment(
            id=f"sem-{start_group_index + offset:04d}",
            speaker_id=speaker_id,
            source_text=text,
            source_start_ms=group[0]["start_ms"],
            source_end_ms=group[-1]["end_ms"],
            source_words=[w["raw"] for w in group],
        ))
    return out


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


async def _refine_words_with_ai(
    transcript: LocalizationTranscript,
    ai,
    *,
    max_words_per_window: int = 60,
) -> LocalizationTranscript | None:
    """SmartSub-style word-tier semantic segmentation.

    AI can only group stable word IDs. Exact token order/coverage is validated,
    so it cannot rewrite source content. Returns None when word timing is not
    trustworthy enough and caller should use cue-tier grouping instead.
    """
    words = _timed_words(transcript)
    if len(words) < 2:
        return None

    # If a meaningful portion of transcript text has no timed words, word-tier
    # segmentation would silently drop source content. Require approximate
    # coverage before using this tier.
    word_text = _collapse_ws(" ".join(w["text"] for w in words))
    source_text = _collapse_ws(
        transcript.text or " ".join(seg.source_text for seg in transcript.segments)
    )
    if not word_text or not source_text:
        return None
    compact_words = re.sub(r"\s+", "", word_text).lower()
    compact_source = re.sub(r"\s+", "", source_text).lower()
    if compact_source and len(compact_words) / max(len(compact_source), 1) < 0.80:
        return None

    output: list[LocalizationSegment] = []
    next_group_index = 1
    ai_available = True
    for start in range(0, len(words), max_words_per_window):
        window = words[start:start + max_words_per_window]
        if len(window) == 1:
            groups = [[window[0]["id"]]]
        elif not ai_available:
            groups = _deterministic_word_groups(window)
        else:
            public_items = [
                {
                    "id": w["id"],
                    "text": w["text"],
                    "start_ms": w["start_ms"],
                    "end_ms": w["end_ms"],
                    "speaker_id": w["speaker_id"],
                }
                for w in window
            ]
            prompt = f"""You are segmenting a word-timestamped transcript into semantic units.

You may ONLY group adjacent WORD IDs. Do not rewrite, translate, omit, duplicate, or reorder words.

Create natural thought/sentence units suitable for translation and subtitles.
Prefer boundaries at punctuation, complete thoughts, meaningful pauses, and speaker changes.
Avoid one-word fragments unless they are truly standalone.

INPUT WORDS:
{__import__("json").dumps(public_items, ensure_ascii=False)}

Return ONLY JSON:
[{{"ids":["w00001","w00002"]}}, {{"ids":["w00003"]}}]
Every input word ID must appear exactly once and in the same order."""

            try:
                raw = await ai.chat(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1800,
                )
            except Exception:
                # Semantic refinement is optional. One provider failure opens a
                # stage-local circuit breaker so the remaining windows do not
                # multiply the same network/model timeout.
                ai_available = False
                groups = _deterministic_word_groups(window)
            else:
                try:
                    text = (raw or "").strip()
                    if text.startswith("```"):
                        lines = text.splitlines()
                        lines = lines[1:] if lines else lines
                        if lines and lines[-1].strip().startswith("```"):
                            lines = lines[:-1]
                        text = "\n".join(lines).strip()
                    payload = __import__("json").loads(text)
                    groups = _validate_group_ids(payload, [w["id"] for w in window])

                    # Do not allow an AI group to cross a known speaker boundary.
                    by_id = {w["id"]: w for w in window}
                    for ids in groups:
                        known = {
                            by_id[word_id]["speaker_id"]
                            for word_id in ids
                            if by_id[word_id]["speaker_id"] is not None
                        }
                        if len(known) > 1:
                            raise ValueError("semantic group crosses speaker boundary")
                except Exception:
                    # Invalid model output is a data-quality fallback, not
                    # provider unavailability: later windows may still use AI.
                    groups = _deterministic_word_groups(window)

        generated = _word_groups_to_segments(
            groups,
            window,
            start_group_index=next_group_index,
        )
        output.extend(generated)
        next_group_index += len(generated)

    refined = transcript.model_copy(deep=True)
    refined.segments = output
    refined.text = " ".join(seg.source_text for seg in output).strip()
    return refined


async def refine_semantic_cues_with_ai(
    transcript: LocalizationTranscript,
    ai,
    *,
    window_size: int = 12,
) -> LocalizationTranscript:
    """SmartSub/VideoLingo-style boundary refinement with validated fallback.

    Prefer real word timestamps, allowing semantic boundaries inside raw ASR
    segments. When word coverage is incomplete, fall back to grouping existing
    deterministic cues. In neither tier may AI rewrite source text.
    """
    base = normalize_semantic_cues(transcript)

    word_refined = await _refine_words_with_ai(base, ai)
    if word_refined is not None:
        return word_refined

    if len(base.segments) <= 1:
        return base

    output: list[LocalizationSegment] = []
    group_index = 1
    ai_available = True
    for start in range(0, len(base.segments), window_size):
        window = base.segments[start:start + window_size]
        if len(window) == 1:
            output.append(_merge_group(window, group_index))
            group_index += 1
            continue
        if not ai_available:
            for seg in window:
                output.append(_merge_group([seg], group_index))
                group_index += 1
            continue

        items = [
            {
                "id": seg.id,
                "text": seg.source_text,
                "start_ms": seg.source_start_ms,
                "end_ms": seg.source_end_ms,
                "speaker_id": seg.speaker_id,
            }
            for seg in window
        ]
        prompt = f"""You are segmenting speech into semantic subtitle/translation units.

You may ONLY group adjacent cue IDs. You MUST NOT rewrite, translate, omit, duplicate, or reorder any cue.

Prefer a new group at:
- a complete thought or sentence boundary;
- a meaningful pause/topic turn;
- a speaker change.

Merge tiny fragments that depend on neighboring words. Avoid overly long groups.

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
        except Exception:
            ai_available = False
            groups = [[seg.id] for seg in window]
        else:
            try:
                text = (raw or "").strip()
                if text.startswith("```"):
                    lines = text.splitlines()
                    lines = lines[1:] if lines else lines
                    if lines and lines[-1].strip().startswith("```"):
                        lines = lines[:-1]
                    text = "\n".join(lines).strip()
                payload = __import__("json").loads(text)
                groups = _validate_group_ids(payload, [seg.id for seg in window])

                by_id = {seg.id: seg for seg in window}
                for ids in groups:
                    speakers = {
                        by_id[seg_id].speaker_id
                        for seg_id in ids
                        if by_id[seg_id].speaker_id is not None
                    }
                    if len(speakers) > 1:
                        raise ValueError("semantic group crosses speaker boundary")
            except Exception:
                # Malformed/invalid grouping falls back only for this window;
                # it does not imply the provider is unavailable.
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
