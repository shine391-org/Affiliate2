# SPDX-License-Identifier: AGPL-3.0-only
"""Phase-C localization engine: ASR -> translation -> timed dub -> audio mix.

This is deliberately one domain service, not a workflow framework. Jobs/UI call
these functions; persistence and orchestration stay in the existing app.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.config import settings
from backend.core.localization import (
    LocalizationProfile,
    LocalizationSegment,
    LocalizationTranscript,
    load_profile_store,
    localization_transcript_from_source,
)

logger = logging.getLogger(__name__)

_TRANSLATION_BATCH_SIZE = 12
_CONTEXT_CHARS = 10_000
_SAFE_SPEED = {
    "strict": 1.00,
    "natural": 1.06,
    "auto": 1.12,
}

_PRESET_INSTRUCTIONS = {
    "natural_vietnamese": (
        "Use natural, conversational {language} that sounds spoken rather than "
        "word-for-word. Preserve meaning, names, numbers and factual claims."
    ),
    "affiliate_conversational": (
        "Use natural, energetic {language} suitable for a product/review short. "
        "Keep it concise and spoken. Preserve the source meaning exactly: do not "
        "invent benefits, prices, CTAs, guarantees or claims that are not present."
    ),
    "literal": (
        "Translate closely and faithfully. Prefer semantic accuracy over style; "
        "do not expand, summarize or add information."
    ),
    "custom": (
        "Preserve source meaning, names, numbers and factual claims. Follow the "
        "custom instructions only when they do not require inventing information."
    ),
}


class LocalizationTimingError(RuntimeError):
    def __init__(self, issues: list[dict]):
        self.issues = issues
        preview = "; ".join(
            (
                f"chunk {x.get('chunk_id')}: speed {x.get('required_speed', '?')}x"
                if x.get("chunk_id") is not None
                else f"{x.get('segment_id')}: {x.get('reason')}"
            )
            for x in issues[:5]
        )
        super().__init__(f"Unresolved dubbing timing: {preview}")


@dataclass
class TimedDubResult:
    transcript: LocalizationTranscript
    dub_track: Path
    block_paths: list[Path]
    issues: list[dict]
    chunk_count: int = 0
    max_speed_factor: float = 1.0
    rewrite_count: int = 0


def resolve_localization_profile(user_settings, profile_id: str | None = None) -> LocalizationProfile:
    store = load_profile_store(getattr(user_settings, "localization_profiles_json", None))
    wanted = profile_id or store.default_profile_id
    for profile in store.profiles:
        if profile.id == wanted:
            return profile
    raise ValueError(f"Localization profile not found: {wanted}")


async def transcribe_for_localization(
    media_path: Path | str,
    profile: LocalizationProfile,
    *,
    language: str | None = None,
) -> LocalizationTranscript:
    """C5 canonical ASR entry point for localization."""
    from backend.services.whisper_service import whisper_service

    tx = await whisper_service.transcribe(
        str(media_path),
        language=language,
        quality=profile.asr_quality,
        model_name=profile.asr_model,
    )
    transcript = localization_transcript_from_source(
        text=tx.get("text"),
        language=tx.get("language"),
        source="whisper",
        raw_segments=tx.get("segments") or [],
    )
    if not transcript.segments:
        raise ValueError("No speech detected — nothing to localize.")
    return transcript


def _profile_ai_client(user_settings, profile: LocalizationProfile):
    from backend.core.ai_provider import AIProvider, get_ai_client

    ai = get_ai_client(user_settings)
    requested = (profile.translation_provider or "").strip().lower()
    configured = (getattr(user_settings, "ai_provider", None) or "").strip().lower()

    if requested:
        # A shared OpenAI-compatible protocol does NOT make Ollama, LM Studio
        # and vLLM interchangeable endpoints. A profile naming a concrete
        # preset must match the configured preset. Generic "compatible" means
        # "use whichever compatible endpoint Settings currently owns".
        generic_compatible = (
            requested == "compatible"
            and ai.provider == AIProvider.COMPATIBLE
        )
        if requested != configured and requested != ai.provider.value and not generic_compatible:
            raise ValueError(
                f"Profile requests AI provider '{requested}', but Settings currently "
                f"configure '{configured or ai.provider.value}'. Configure that provider first."
            )

    if profile.translation_model:
        ai.model = profile.translation_model
    return ai


def _strip_json_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def prepare_semantic_transcript(
    transcript: LocalizationTranscript,
    *,
    profile: LocalizationProfile,
    user_settings,
) -> LocalizationTranscript:
    """VideoLingo/SmartSub-derived source cue preparation.

    Raw Whisper segments are first normalized with real word timing. When the
    profile enables semantic segmentation, the configured AI may only GROUP
    stable cue IDs; invalid responses fall back to deterministic normalized
    cues without rewriting source text.
    """
    from backend.services.semantic_segmentation import (
        normalize_semantic_cues,
        refine_semantic_cues_with_ai,
    )

    normalized = normalize_semantic_cues(transcript)
    if not profile.semantic_segmentation or len(normalized.segments) <= 1:
        return normalized

    ai = _profile_ai_client(user_settings, profile)
    return await refine_semantic_cues_with_ai(normalized, ai)


def _translation_context(transcript: LocalizationTranscript) -> str:
    text = (transcript.text or "").strip()
    if not text:
        text = " ".join(s.source_text for s in transcript.segments)
    if len(text) <= _CONTEXT_CHARS:
        return text
    half = _CONTEXT_CHARS // 2
    return text[:half] + "\n...[context truncated]...\n" + text[-half:]


def _translation_prompt(
    *,
    target_language: str,
    batch: list[LocalizationSegment],
    transcript_context: str,
    profile: LocalizationProfile,
    for_dub: bool,
) -> str:
    preset = _PRESET_INSTRUCTIONS[profile.translation_preset].format(
        language=target_language
    )
    glossary = (
        "\n".join(f"- {src} => {dst}" for src, dst in profile.glossary.items())
        or "(none)"
    )
    custom = profile.translation_instructions or "(none)"
    items = [
        {
            "id": seg.id,
            "text": seg.source_text,
            "source_duration_ms": seg.source_end_ms - seg.source_start_ms,
        }
        for seg in batch
    ]
    timing = (
        "For dubbing, keep each translation naturally speakable within its "
        "source_duration_ms. Prefer concise target-language phrasing over speed-reading."
        if for_dub else
        "Keep each segment aligned to the same semantic unit."
    )
    return f"""You are localizing a video into {target_language}.

NON-NEGOTIABLE:
- Preserve meaning and factual claims.
- Preserve segment IDs and order.
- Do not invent commentary, CTA, claims, prices, names or examples.
- Apply glossary terms consistently.
- {timing}

STYLE:
{preset}

CUSTOM INSTRUCTIONS:
{custom}

GLOSSARY:
{glossary}

FULL SOURCE CONTEXT:
{transcript_context}

SEGMENTS:
{json.dumps(items, ensure_ascii=False)}

Return ONLY a JSON array of objects with exactly this shape:
[{{"id":"segment-id","text":"translated speech"}}]
The array must contain every input ID exactly once and no extra IDs."""


def _validate_translation_payload(raw: str, batch: list[LocalizationSegment]) -> list[str]:
    payload = json.loads(_strip_json_fence(raw))
    if not isinstance(payload, list):
        raise ValueError("translation response must be a JSON array")
    expected = [s.id for s in batch]
    if len(payload) != len(expected):
        raise ValueError("translation response count mismatch")

    seen: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("translation item must be an object")
        seg_id = str(item.get("id") or "")
        text = str(item.get("text") or "").strip()
        if not seg_id or not text or seg_id in seen:
            raise ValueError("translation item has missing/duplicate id or empty text")
        seen[seg_id] = text
    if set(seen) != set(expected):
        raise ValueError("translation response IDs do not match input IDs")
    return [seen[seg_id] for seg_id in expected]


async def _translate_batch_resilient(
    ai,
    batch: list[LocalizationSegment],
    *,
    target_language: str,
    context: str,
    profile: LocalizationProfile,
    for_dub: bool,
) -> tuple[list[str], set[int]]:
    prompt = _translation_prompt(
        target_language=target_language,
        batch=batch,
        transcript_context=context,
        profile=profile,
        for_dub=for_dub,
    )
    raw = await ai.chat(messages=[{"role": "user", "content": prompt}], max_tokens=3000)
    try:
        return _validate_translation_payload(raw, batch), set()
    except (json.JSONDecodeError, ValueError) as exc:
        if len(batch) == 1:
            logger.warning("Translation shape failed for %s: %s", batch[0].id, exc)
            return [batch[0].source_text], {0}

        mid = len(batch) // 2
        left, lf = await _translate_batch_resilient(
            ai, batch[:mid], target_language=target_language, context=context,
            profile=profile, for_dub=for_dub,
        )
        right, rf = await _translate_batch_resilient(
            ai, batch[mid:], target_language=target_language, context=context,
            profile=profile, for_dub=for_dub,
        )
        return left + right, lf | {mid + i for i in rf}


async def translate_transcript(
    transcript: LocalizationTranscript,
    *,
    target_language: str,
    profile: LocalizationProfile,
    user_settings,
    for_dub: bool,
) -> LocalizationTranscript:
    """C6 contextual structured translation preserving segment identity."""
    ai = _profile_ai_client(user_settings, profile)
    out = transcript.model_copy(deep=True)
    context = _translation_context(transcript)
    failed_total = 0

    for start in range(0, len(out.segments), _TRANSLATION_BATCH_SIZE):
        batch = out.segments[start:start + _TRANSLATION_BATCH_SIZE]
        texts, failed = await _translate_batch_resilient(
            ai,
            batch,
            target_language=target_language,
            context=context,
            profile=profile,
            for_dub=for_dub,
        )
        failed_total += len(failed)
        for idx, (seg, text) in enumerate(zip(batch, texts)):
            seg.translated_text = text
            seg.translation_status = "untranslated" if idx in failed else "success"
            if not for_dub:
                seg.target_start_ms = seg.source_start_ms
                seg.target_end_ms = seg.source_end_ms
                seg.timing_status = "fit"
                seg.qc_status = "review" if idx in failed else "pass"

    if failed_total == len(out.segments):
        raise RuntimeError("Translation failed for every segment.")
    if failed_total and profile.qc_profile == "strict":
        raise RuntimeError(
            f"Translation left {failed_total} segment(s) untranslated under strict QC."
        )
    return out


async def _adapt_translation_for_duration(
    ai,
    seg: LocalizationSegment,
    *,
    target_language: str,
    target_ms: int,
    profile: LocalizationProfile,
) -> str:
    """One bounded refinement call, only after measured TTS overflow."""
    current = (seg.translated_text or "").strip()
    if not current:
        return current
    target_s = max(target_ms / 1000.0, 0.2)
    prompt = f"""Shorten this {target_language} dubbing line so it can be spoken naturally in about {target_s:.2f} seconds.

SOURCE MEANING:
{seg.source_text}

CURRENT TRANSLATION:
{current}

Rules:
- Preserve the complete essential meaning, names, numbers and factual claims.
- Do not add CTA, claims or information.
- Prefer shorter natural phrasing, not abbreviations that sound robotic.
- Return ONLY the revised spoken line."""
    if profile.glossary:
        terms = "; ".join(f"{k} => {v}" for k, v in profile.glossary.items())
        prompt += f"\nGlossary to preserve: {terms[:1500]}"
    if profile.translation_instructions:
        prompt += f"\nStyle constraint: {profile.translation_instructions[:1000]}"
    revised = (await ai.chat(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=400,
    ) or "").strip().strip('"').strip()
    return revised or current


def _tts_config(
    user_settings,
    profile: LocalizationProfile,
    target_language: str,
):
    from backend.core.crypto import decrypt_safe
    from backend.services.tts_service import DEFAULT_VOICES, TTSProvider

    raw_provider = (
        profile.tts_provider
        or getattr(user_settings, "tts_provider", None)
        or TTSProvider.EDGE_TTS.value
    )
    try:
        provider = TTSProvider(raw_provider)
    except ValueError as exc:
        raise ValueError(f"Unsupported TTS provider: {raw_provider}") from exc

    explicit_voice = (
        profile.tts_voice
        or getattr(user_settings, "preferred_tts_voice", None)
        or getattr(user_settings, "preferred_voice_id", None)
    )
    if explicit_voice:
        voice = explicit_voice
    elif provider == TTSProvider.EDGE_TTS and target_language.lower() in {
        "vi", "vietnamese", "tiếng việt", "tieng viet",
    }:
        voice = "vi-VN-HoaiMyNeural"
    else:
        voice = DEFAULT_VOICES[provider]

    api_key = ""
    if provider == TTSProvider.OPENAI_TTS:
        if (
            (getattr(user_settings, "ai_provider", None) or "").lower() == "openai"
            and getattr(user_settings, "ai_api_key_encrypted", None)
        ):
            api_key = decrypt_safe(user_settings.ai_api_key_encrypted) or ""
        api_key = api_key or settings.OPENAI_API_KEY

    return provider, voice, api_key, profile.tts_model


def _timing_window(
    segments: list[LocalizationSegment],
    index: int,
    *,
    start_ms: int,
    tolerance_ms: int,
) -> int:
    seg = segments[index]
    end_ms = seg.source_end_ms + tolerance_ms
    if index + 1 < len(segments):
        next_start = segments[index + 1].source_start_ms
        if next_start > seg.source_end_ms:
            end_ms = min(end_ms, next_start)
        else:
            end_ms = seg.source_end_ms
    return max(end_ms - start_ms, 1)


async def _audio_duration_ms(path: Path) -> int:
    from backend.services.video_utils import probe_duration
    seconds = await asyncio.to_thread(probe_duration, path, 0.0)
    return max(0, round(float(seconds) * 1000))


async def _generate_tts_block(
    text: str,
    *,
    provider,
    voice: str,
    api_key: str,
    model: str | None,
    speed: float,
    output_path: Path,
) -> int:
    from backend.services.tts_service import generate_tts

    output_path.unlink(missing_ok=True)
    await generate_tts(
        text,
        provider=provider,
        voice_id=voice,
        api_key=api_key,
        output_path=output_path,
        model=model,
        speed=speed,
    )
    duration = await _audio_duration_ms(output_path)
    if duration <= 0:
        raise RuntimeError(f"TTS produced unreadable audio: {output_path.name}")
    return duration


def _compose_dub_track_sync(
    timed_blocks: list[tuple[LocalizationSegment, Path]],
    *,
    total_duration_ms: int,
    output_path: Path,
) -> Path:
    if not timed_blocks:
        raise RuntimeError("No TTS blocks to compose")
    total_s = max(total_duration_ms / 1000.0, 0.1)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-t", f"{total_s:.3f}",
        "-i", "anullsrc=r=48000:cl=stereo",
    ]
    for _, path in timed_blocks:
        cmd += ["-i", str(path)]

    filters = ["[0:a]volume=0[base]"]
    labels = ["[base]"]
    for idx, (seg, _) in enumerate(timed_blocks, start=1):
        delay = max(int(seg.target_start_ms or 0), 0)
        label = f"d{idx}"
        filters.append(
            f"[{idx}:a]aresample=48000,adelay={delay}|{delay}[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"
          f"atrim=0:{total_s:.3f}[mix]"
    )
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[mix]",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"Dub-track composition failed: {(result.stderr or '')[-500:]}")
    return output_path


async def synthesize_timed_dub(
    transcript: LocalizationTranscript,
    *,
    source_media: Path,
    target_language: str,
    profile: LocalizationProfile,
    user_settings,
    work_dir: Path,
    fail_on_overflow: bool = True,
) -> TimedDubResult:
    """C7: TTS each translated block, measure it, adapt safely, then place it."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = transcript.model_copy(deep=True)
    ai = _profile_ai_client(user_settings, profile)
    provider, voice, api_key, tts_model = _tts_config(
        user_settings, profile, target_language
    )

    timed_blocks: list[tuple[LocalizationSegment, Path]] = []
    block_paths: list[Path] = []
    issues: list[dict] = []
    previous_end = 0

    for index, seg in enumerate(out.segments):
        if seg.translation_status != "success" or not (seg.translated_text or "").strip():
            seg.qc_status = "review"
            issues.append({
                "segment_id": seg.id,
                "reason": "translation_unavailable",
            })
            continue

        target_start = max(seg.source_start_ms, previous_end)
        window_ms = _timing_window(
            out.segments, index,
            start_ms=target_start,
            tolerance_ms=(
                0 if profile.timing_policy == "strict"
                else profile.timing_tolerance_ms
            ),
        )
        path = work_dir / f"dub_{index:04d}.mp3"
        block_paths.append(path)

        adjusted = target_start != seg.source_start_ms
        text = seg.translated_text
        duration_ms = await _generate_tts_block(
            text,
            provider=provider, voice=voice, api_key=api_key, model=tts_model,
            speed=1.0, output_path=path,
        )

        if duration_ms > window_ms:
            revised = await _adapt_translation_for_duration(
                ai, seg, target_language=target_language,
                target_ms=window_ms, profile=profile,
            )
            if revised and revised != text:
                text = revised
                seg.translated_text = revised
                adjusted = True
                duration_ms = await _generate_tts_block(
                    text,
                    provider=provider, voice=voice, api_key=api_key, model=tts_model,
                    speed=1.0, output_path=path,
                )

        if duration_ms > window_ms:
            max_speed = _SAFE_SPEED[profile.tts_rate_policy]
            needed_speed = duration_ms / max(window_ms, 1)
            if max_speed > 1.0 and needed_speed <= max_speed:
                adjusted = True
                duration_ms = await _generate_tts_block(
                    text,
                    provider=provider, voice=voice, api_key=api_key, model=tts_model,
                    speed=max(1.0, needed_speed), output_path=path,
                )

        seg.target_start_ms = target_start
        seg.target_end_ms = target_start + duration_ms
        seg.tts_duration_ms = duration_ms

        if duration_ms > window_ms:
            overflow = duration_ms - window_ms
            seg.timing_status = "overflow"
            seg.qc_status = "review"
            issues.append({
                "segment_id": seg.id,
                "reason": "timing_overflow",
                "overflow_ms": overflow,
                "window_ms": window_ms,
                "tts_duration_ms": duration_ms,
            })
        else:
            seg.timing_status = "adjusted" if adjusted else "fit"
            seg.qc_status = "pass"
            timed_blocks.append((seg, path))
            previous_end = seg.target_end_ms

    if issues and fail_on_overflow:
        raise LocalizationTimingError(issues)
    if not timed_blocks:
        raise RuntimeError("No usable dubbed blocks were produced.")

    from backend.services.video_utils import probe_duration
    source_duration_s = await asyncio.to_thread(probe_duration, source_media, 0.0)
    total_duration_ms = max(
        round(source_duration_s * 1000),
        max((seg.target_end_ms or 0) for seg, _ in timed_blocks),
    )
    dub_track = work_dir / "dub_track.wav"
    await asyncio.to_thread(
        _compose_dub_track_sync,
        timed_blocks,
        total_duration_ms=total_duration_ms,
        output_path=dub_track,
    )
    return TimedDubResult(
        transcript=out,
        dub_track=dub_track,
        block_paths=block_paths,
        issues=issues,
        chunk_count=len(fitted_chunks),
        max_speed_factor=report.max_speed_factor,
        rewrite_count=report.rewrite_count,
    )


def _mix_dub_with_video_sync(
    source_video: Path,
    dub_track: Path,
    output_path: Path,
    *,
    audio_mode: str,
    background_file: Path | None,
    preserve_background: bool,
    background_gain_db: float,
    dub_gain_db: float,
) -> Path:
    """Mix target dub while stream-copying the picture.

    hq_clean: mix against Demucs background stem (source speech removed).
    fast_duck: retain legacy source-audio sidechain ducking as preview/fallback.
    """
    from backend.services.ffmpeg_service import has_audio_stream_sync

    if audio_mode == "hq_clean":
        if background_file is None or not background_file.exists():
            raise RuntimeError("HQ clean dub requires a separated background track.")
        cmd = [
            "ffmpeg", "-y",
            "-i", str(source_video),
            "-i", str(dub_track),
            "-i", str(background_file),
            "-filter_complex",
            (
                f"[1:a]volume={dub_gain_db}dB[dub];"
                f"[2:a]volume={background_gain_db}dB[bg];"
                "[dub][bg]amix=inputs=2:duration=longest:normalize=0,"
                "alimiter=limit=0.95[mix]"
            ),
            "-map", "0:v:0", "-map", "[mix]",
        ]
    else:
        has_source_audio = has_audio_stream_sync(source_video)
        cmd = ["ffmpeg", "-y", "-i", str(source_video), "-i", str(dub_track)]
        if preserve_background and has_source_audio:
            fc = (
                f"[0:a]volume={background_gain_db}dB[bg];"
                f"[1:a]volume={dub_gain_db}dB[dub];"
                "[bg][dub]sidechaincompress=threshold=0.025:ratio=12:"
                "attack=15:release=220[ducked];"
                "[ducked][dub]amix=inputs=2:duration=longest:normalize=0,"
                "alimiter=limit=0.95[mix]"
            )
            cmd += ["-filter_complex", fc, "-map", "0:v:0", "-map", "[mix]"]
        else:
            cmd += ["-map", "0:v:0", "-map", "1:a:0"]

    cmd += [
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"Dub/source audio mix failed: {(result.stderr or '')[-500:]}")
    return output_path


async def mix_dub_with_video(
    source_video: Path,
    dub_track: Path,
    output_path: Path,
    profile: LocalizationProfile,
    *,
    background_file: Path | None = None,
) -> Path:
    """C8 donor-derived mix; audio-only stages never re-encode video."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    return await asyncio.to_thread(
        _mix_dub_with_video_sync,
        source_video,
        dub_track,
        output_path,
        audio_mode=profile.audio_mode,
        background_file=background_file,
        preserve_background=profile.preserve_background,
        background_gain_db=profile.background_gain_db,
        dub_gain_db=profile.dub_gain_db,
    )


def caption_segments_from_localization(
    transcript: LocalizationTranscript,
) -> list[dict]:
    """Render captions from FINAL resolved timing, never source word timing."""
    out: list[dict] = []
    for seg in transcript.segments:
        text = (seg.translated_text or "").strip()
        if not text:
            continue
        start_ms = seg.target_start_ms
        end_ms = seg.target_end_ms
        if start_ms is None or end_ms is None:
            start_ms, end_ms = seg.source_start_ms, seg.source_end_ms
        if end_ms <= start_ms:
            continue
        out.append({
            "start": start_ms / 1000.0,
            "end": end_ms / 1000.0,
            "text": text,
        })
    return out
