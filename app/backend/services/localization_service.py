# SPDX-License-Identifier: AGPL-3.0-only
"""Canonical Phase-C HQ localization engine.

Donor-derived behavior:
- VideoLingo / SmartSub: word-aware semantic segmentation + validated translation.
- OpenCreator: chunk planner, measured TTS, post-synthesis atempo fitting.
- YouDub: clean background workflow (separation is orchestrated by tool runner).
- pyVideoTrans: bounded video-retiming concept as a last-resort fit strategy.

There is ONE active dubbing engine in this module. Raw Whisper segments are never
treated as immutable TTS units.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from backend.config import settings
from backend.core.localization import (
    LocalizationProfile,
    LocalizationSegment,
    LocalizationTranscript,
    load_profile_store,
    localization_transcript_from_source,
)
from backend.services.dubbing_planner import (
    DubbingChunk,
    DubbingConfig,
    DubbingCue,
    PlanItem,
    atempo_filter,
    borrow_following_silence,
    build_plan,
    chunk_text,
    estimate_spoken_ms,
    fit_timeline,
    required_speed,
)

logger = logging.getLogger(__name__)

_TRANSLATION_BATCH_SIZE = 12
_CONTEXT_CHARS = 10_000
_DUBBING_SYNTHESIS_RULES_VERSION = 1

_PRESET_INSTRUCTIONS = {
    "natural_vietnamese": (
        "Use natural, conversational {language} that sounds spoken rather than "
        "word-for-word. Preserve meaning, names, numbers and factual claims."
    ),
    "affiliate_conversational": (
        "Use natural, energetic {language} suitable for a product/review short. "
        "Keep it concise and spoken. Preserve source meaning exactly: do not "
        "invent benefits, prices, CTAs, guarantees or claims."
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
class TTSRuntimeConfig:
    provider: object
    default_voice: str
    api_key: str
    model: str | None
    base_url: str | None
    region: str | None


@dataclass
class TimedDubResult:
    transcript: LocalizationTranscript
    dub_track: Path
    block_paths: list[Path]
    issues: list[dict]
    chunk_count: int = 0
    max_speed_factor: float = 1.0
    max_fitted_speed_factor: float = 1.0
    rewrite_count: int = 0
    video_slowdown: float = 1.0
    plan: list[PlanItem] | None = None
    chunks: list[DubbingChunk] | None = None


def resolve_localization_profile(user_settings, profile_id: str | None = None) -> LocalizationProfile:
    store = load_profile_store(getattr(user_settings, "localization_profiles_json", None))
    wanted = profile_id or store.default_profile_id
    for profile in store.profiles:
        if profile.id == wanted:
            return profile
    raise ValueError(f"Localization profile not found: {wanted}")


def materialize_localization_profile(
    user_settings,
    profile: LocalizationProfile,
    *,
    target_language: str,
    for_dub: bool = True,
) -> LocalizationProfile:
    """Resolve non-secret runtime bindings once for a reproducible job snapshot.

    Secrets stay in encrypted Settings; provider/model/base URL/voice/region are
    safe configuration and must not drift silently between resume/rerun calls.
    """
    ai = _profile_ai_client(user_settings, profile)

    data = profile.model_dump()
    data.update({
        "translation_provider": ai.provider.value,
        "translation_model": ai.model,
        "translation_base_url": ai.base_url,
    })
    if for_dub:
        tts = _tts_config(user_settings, profile, target_language)
        data.update({
            "tts_provider": getattr(tts.provider, "value", str(tts.provider)),
            "tts_model": tts.model,
            "tts_voice": tts.default_voice or None,
            "tts_base_url": tts.base_url,
            "tts_region": tts.region,
        })
    return LocalizationProfile.model_validate(data)


async def transcribe_for_localization(
    media_path: Path | str,
    profile: LocalizationProfile,
    *,
    language: str | None = None,
    user_settings=None,
) -> LocalizationTranscript:
    """C5: faster-whisper transcription followed by optional WhisperX alignment."""
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

    if profile.alignment_mode != "off" or profile.diarization_mode != "off":
        from backend.services.alignment_service import align_localization_transcript
        hf_token = ""
        if user_settings is not None and getattr(user_settings, "huggingface_token_encrypted", None):
            from backend.core.crypto import decrypt_safe
            hf_token = decrypt_safe(user_settings.huggingface_token_encrypted) or ""
        transcript, report = await align_localization_transcript(
            media_path,
            transcript,
            profile,
            huggingface_token=hf_token,
        )
        transcript.metadata["alignment"] = asdict(report)
    return transcript


def _profile_ai_client(user_settings, profile: LocalizationProfile):
    from backend.core.ai_provider import AIProvider, get_ai_client

    ai = get_ai_client(user_settings)
    requested = (profile.translation_provider or "").strip().lower()
    configured = (getattr(user_settings, "ai_provider", None) or "").strip().lower()

    if requested:
        generic_compatible = (
            requested == "compatible"
            and ai.provider == AIProvider.COMPATIBLE
        )
        if requested != configured and requested != ai.provider.value and not generic_compatible:
            raise ValueError(
                f"Profile requests AI provider '{requested}', but Settings configure "
                f"'{configured or ai.provider.value}'. Configure that provider first."
            )
    if profile.translation_model:
        ai.model = profile.translation_model
    if profile.translation_base_url:
        if ai.provider != AIProvider.COMPATIBLE:
            raise ValueError("translation_base_url is only valid for an OpenAI-compatible AI provider")
        ai.base_url = profile.translation_base_url.rstrip("/")
    return ai


async def prepare_semantic_transcript(
    transcript: LocalizationTranscript,
    *,
    profile: LocalizationProfile,
    user_settings,
) -> LocalizationTranscript:
    from backend.services.semantic_segmentation import (
        normalize_semantic_cues,
        refine_semantic_cues_with_ai,
    )

    normalized = normalize_semantic_cues(transcript)
    if not profile.semantic_segmentation or len(normalized.segments) <= 1:
        return normalized
    ai = _profile_ai_client(user_settings, profile)
    return await refine_semantic_cues_with_ai(normalized, ai)


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
            "speaker_id": seg.speaker_id,
            "text": seg.source_text,
            "source_duration_ms": seg.source_end_ms - seg.source_start_ms,
        }
        for seg in batch
    ]
    timing = (
        "For dubbing, keep each translation concise and naturally speakable. "
        "The downstream chunk planner will combine adjacent lines and measure real TTS."
        if for_dub
        else "Keep each segment aligned to the same semantic unit."
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

Return ONLY a JSON array:
[{{"id":"segment-id","text":"translated speech"}}]
Every input ID must appear exactly once and no extra IDs are allowed."""


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
    ai = _profile_ai_client(user_settings, profile)
    out = transcript.model_copy(deep=True)
    context = _translation_context(transcript)
    failed_total = 0

    for start in range(0, len(out.segments), _TRANSLATION_BATCH_SIZE):
        batch = out.segments[start:start + _TRANSLATION_BATCH_SIZE]
        texts, failed = await _translate_batch_resilient(
            ai, batch,
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


_DEFAULT_SPEECH_VOICES = {
    "vi": "vi-VN-HoaiMyNeural",
    "vietnamese": "vi-VN-HoaiMyNeural",
    "en": "en-US-AndrewMultilingualNeural",
    "english": "en-US-AndrewMultilingualNeural",
    "es": "es-ES-XimenaNeural",
    "spanish": "es-ES-XimenaNeural",
    "fr": "fr-FR-VivienneMultilingualNeural",
    "french": "fr-FR-VivienneMultilingualNeural",
    "de": "de-DE-SeraphinaMultilingualNeural",
    "german": "de-DE-SeraphinaMultilingualNeural",
    "it": "it-IT-GiuseppeMultilingualNeural",
    "italian": "it-IT-GiuseppeMultilingualNeural",
    "pt": "pt-BR-ThalitaMultilingualNeural",
    "portuguese": "pt-BR-ThalitaMultilingualNeural",
    "ja": "ja-JP-NanamiNeural",
    "japanese": "ja-JP-NanamiNeural",
    "ko": "ko-KR-HyunsuMultilingualNeural",
    "korean": "ko-KR-HyunsuMultilingualNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "chinese": "zh-CN-XiaoxiaoNeural",
    "nl": "nl-NL-ColetteNeural",
    "dutch": "nl-NL-ColetteNeural",
    "pl": "pl-PL-ZofiaNeural",
    "polish": "pl-PL-ZofiaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "russian": "ru-RU-SvetlanaNeural",
    "ar": "ar-SA-ZariyahNeural",
    "arabic": "ar-SA-ZariyahNeural",
    "hi": "hi-IN-SwaraNeural",
    "hindi": "hi-IN-SwaraNeural",
    "tr": "tr-TR-EmelNeural",
    "turkish": "tr-TR-EmelNeural",
    "sv": "sv-SE-SofieNeural",
    "swedish": "sv-SE-SofieNeural",
    "da": "da-DK-ChristelNeural",
    "danish": "da-DK-ChristelNeural",
    "no": "nb-NO-PernilleNeural",
    "norwegian": "nb-NO-PernilleNeural",
    "fi": "fi-FI-NooraNeural",
    "finnish": "fi-FI-NooraNeural",
}


def _default_speech_voice(target_language: str) -> str:
    key = (target_language or "").strip().lower()
    return _DEFAULT_SPEECH_VOICES.get(key, "en-US-AndrewMultilingualNeural")


def _tts_config(user_settings, profile: LocalizationProfile, target_language: str) -> TTSRuntimeConfig:
    from backend.core.crypto import decrypt_safe
    from backend.services.tts_service import DEFAULT_VOICES, TTSProvider

    configured_provider = (
        getattr(user_settings, "tts_provider", None)
        if user_settings is not None
        else None
    )
    requested_provider = (profile.tts_provider or "").strip().lower() or None
    configured_provider = (configured_provider or "").strip().lower() or None

    # Settings own one TTS connection/credential slot. A profile may pin that
    # provider, but it must never reinterpret another provider's secret or
    # endpoint.
    if requested_provider and configured_provider and requested_provider != configured_provider:
        raise ValueError(
            f"Profile requests TTS provider '{requested_provider}', but Settings "
            f"configure '{configured_provider}'. Configure that TTS provider first."
        )

    raw_provider = requested_provider or configured_provider or TTSProvider.EDGE_TTS.value
    try:
        provider = TTSProvider(raw_provider)
    except ValueError as exc:
        raise ValueError(f"Unsupported TTS provider: {raw_provider}") from exc

    # Do not inherit preferred_voice_id: it is the legacy generator voice slot
    # and often contains an ElevenLabs ID that is invalid for Edge/Azure/OpenAI.
    explicit_voice = (
        profile.tts_voice
        or getattr(user_settings, "preferred_tts_voice", None)
    )
    if explicit_voice:
        voice = explicit_voice
    elif provider in {TTSProvider.EDGE_TTS, TTSProvider.AZURE}:
        voice = _default_speech_voice(target_language)
    else:
        voice = DEFAULT_VOICES.get(provider, "")

    if provider == TTSProvider.ELEVENLABS and not voice:
        raise ValueError(
            "ElevenLabs TTS requires a voice ID in Settings or the Localization Profile."
        )

    api_key = ""
    encrypted_tts = getattr(user_settings, "tts_api_key_encrypted", None)
    if encrypted_tts:
        api_key = decrypt_safe(encrypted_tts) or ""
    if provider == TTSProvider.OPENAI_TTS and not api_key:
        if (
            (getattr(user_settings, "ai_provider", None) or "").lower() == "openai"
            and getattr(user_settings, "ai_api_key_encrypted", None)
        ):
            api_key = decrypt_safe(user_settings.ai_api_key_encrypted) or ""
        api_key = api_key or settings.OPENAI_API_KEY

    model = profile.tts_model or getattr(user_settings, "tts_model", None)
    base_url = profile.tts_base_url or getattr(user_settings, "tts_base_url", None)
    region = profile.tts_region or getattr(user_settings, "tts_region", None)
    return TTSRuntimeConfig(provider, voice or "", api_key, model, base_url, region)


def _chunk_speaker(plan: list[PlanItem], chunk: DubbingChunk) -> str | None:
    speakers = {
        plan[idx].speaker_id
        for idx in chunk.item_indexes
        if plan[idx].speaker_id is not None
    }
    if len(speakers) > 1:
        raise RuntimeError(f"Dubbing chunk {chunk.id} crosses speaker boundary")
    return next(iter(speakers)) if speakers else chunk.speaker_id


def _voice_for_chunk(
    profile: LocalizationProfile,
    runtime: TTSRuntimeConfig,
    speaker_id: str | None,
    explicit_voice: str | None = None,
) -> str:
    if explicit_voice:
        return explicit_voice
    if speaker_id and speaker_id in profile.speaker_voices:
        return profile.speaker_voices[speaker_id]
    return runtime.default_voice


def _reference_for_chunk(
    profile: LocalizationProfile,
    speaker_id: str | None,
) -> str | None:
    if speaker_id and speaker_id in profile.speaker_reference_audio:
        return profile.speaker_reference_audio[speaker_id]
    return profile.tts_reference_audio


def _extract_reference_clip_sync(
    media_path: Path,
    output_path: Path,
    *,
    start_ms: int,
    end_ms: int,
) -> Path:
    """Extract a clean speech reference without touching video pixels."""
    if end_ms <= start_ms:
        raise ValueError("reference end must be after start")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start_ms / 1000.0:.3f}",
        "-t", f"{(end_ms - start_ms) / 1000.0:.3f}",
        "-i", str(media_path),
        "-vn", "-ac", "1", "-ar", "32000",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(
            f"Reference audio extraction failed: {(result.stderr or '')[-500:]}"
        )
    return output_path


async def _build_auto_voice_references(
    transcript: LocalizationTranscript,
    *,
    reference_media: Path,
    profile: LocalizationProfile,
    work_dir: Path,
) -> dict[str, dict]:
    """VideoLingo-style automatic source-reference extraction for cloning.

    Prefer a 2–10s source utterance per speaker. For global mode choose the
    single longest eligible utterance. Manual profile references always win.
    """
    mode = profile.voice_clone_reference_mode
    if mode == "manual":
        return {}

    eligible = [
        seg for seg in transcript.segments
        if 1500 <= (seg.source_end_ms - seg.source_start_ms) <= 12000
        and seg.source_text.strip()
    ]
    if not eligible:
        eligible = [
            seg for seg in transcript.segments
            if (seg.source_end_ms - seg.source_start_ms) >= 600
            and seg.source_text.strip()
        ]
    if not eligible:
        return {}

    chosen: dict[str, LocalizationSegment] = {}
    if mode == "global_auto":
        seg = max(eligible, key=lambda x: x.source_end_ms - x.source_start_ms)
        chosen["__default__"] = seg
    else:
        for seg in eligible:
            key = seg.speaker_id or "__default__"
            current = chosen.get(key)
            if (
                current is None
                or (seg.source_end_ms - seg.source_start_ms)
                > (current.source_end_ms - current.source_start_ms)
            ):
                chosen[key] = seg

    refs: dict[str, dict] = {}
    ref_dir = work_dir / "references"
    for key, seg in chosen.items():
        # Explicit profile references override auto extraction for that speaker.
        manual = (
            profile.speaker_reference_audio.get(key)
            if key != "__default__"
            else profile.tts_reference_audio
        )
        if manual:
            refs[key] = {
                "audio": manual,
                "text": profile.tts_reference_text or seg.source_text,
                "language": profile.tts_reference_language or transcript.language or "en",
                "source": "manual",
            }
            continue

        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in key)
        path = ref_dir / f"{safe}.wav"
        await asyncio.to_thread(
            _extract_reference_clip_sync,
            reference_media,
            path,
            start_ms=seg.source_start_ms,
            end_ms=seg.source_end_ms,
        )
        refs[key] = {
            "audio": str(path),
            "text": seg.source_text,
            "language": transcript.language or profile.tts_reference_language or "en",
            "source": "auto",
        }
    return refs


async def _audio_duration_ms(path: Path) -> int:
    from backend.services.video_utils import probe_duration
    seconds = await asyncio.to_thread(probe_duration, path, 0.0)
    return max(0, round(float(seconds) * 1000))


async def _generate_tts_chunk(
    text: str,
    *,
    runtime: TTSRuntimeConfig,
    voice: str,
    profile: LocalizationProfile,
    speaker_id: str | None,
    target_language: str,
    output_path: Path,
    reference_audio: str | None = None,
    reference_text: str | None = None,
    reference_language: str | None = None,
    attempts: int = 3,
) -> int:
    from backend.services.tts_service import generate_tts

    last: Exception | None = None
    for _ in range(max(1, attempts)):
        output_path.unlink(missing_ok=True)
        try:
            await generate_tts(
                text,
                provider=runtime.provider,
                voice_id=voice,
                api_key=runtime.api_key,
                output_path=output_path,
                model=runtime.model,
                speed=1.0,
                base_url=runtime.base_url,
                region=runtime.region,
                reference_audio=reference_audio or _reference_for_chunk(profile, speaker_id),
                reference_text=reference_text or profile.tts_reference_text,
                reference_language=reference_language or profile.tts_reference_language,
                target_language=target_language,
            )
            duration = await _audio_duration_ms(output_path)
            if duration > 0:
                return duration
            last = RuntimeError("TTS generated zero-duration audio")
        except Exception as exc:
            last = exc
    raise RuntimeError(f"TTS chunk failed after {attempts} attempts: {last}")



def _chunk_synthesis_identity(
    *,
    text: str,
    runtime: TTSRuntimeConfig,
    voice: str,
    target_language: str,
    speaker_id: str | None,
    reference_audio: str | None,
    reference_text: str | None,
    reference_language: str | None,
) -> str:
    """Fingerprint every input that can change a raw natural-rate TTS chunk.

    This is intentionally separate from timing/fitting inputs: a timing-only
    edit may safely reuse natural TTS, while any speech/voice/provider change
    must regenerate it.
    """
    provider = getattr(runtime.provider, "value", str(runtime.provider))
    payload = {
        "rules": _DUBBING_SYNTHESIS_RULES_VERSION,
        "provider": provider,
        "model": runtime.model,
        "base_url": runtime.base_url,
        "region": runtime.region,
        "voice": voice,
        "language": target_language,
        "speaker_id": speaker_id,
        "text": text,
        "reference_audio": reference_audio,
        "reference_text": reference_text,
        "reference_language": reference_language,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _chunk_identity_path(audio_path: Path) -> Path:
    return audio_path.with_name(audio_path.name + ".input.json")


def _chunk_identity_matches(audio_path: Path, identity: str) -> bool:
    sidecar = _chunk_identity_path(audio_path)
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("rules") == _DUBBING_SYNTHESIS_RULES_VERSION
        and payload.get("identity") == identity
    )


def _write_chunk_identity(audio_path: Path, identity: str) -> None:
    sidecar = _chunk_identity_path(audio_path)
    tmp = sidecar.with_name(sidecar.name + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "rules": _DUBBING_SYNTHESIS_RULES_VERSION,
                "identity": identity,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    tmp.replace(sidecar)


async def _generate_tts_chunk_atomic(
    text: str,
    *,
    output_path: Path,
    identity: str,
    **kwargs,
) -> int:
    """Publish a new raw chunk only after TTS and duration probing succeed."""
    pending = output_path.with_name(output_path.stem + ".pending" + output_path.suffix)
    pending.unlink(missing_ok=True)
    try:
        duration = await _generate_tts_chunk(
            text,
            output_path=pending,
            **kwargs,
        )
        pending.replace(output_path)
        try:
            _write_chunk_identity(output_path, identity)
        except Exception as exc:
            # Missing identity only disables future reuse; it must not invalidate
            # a TTS artifact that was already generated and measured correctly.
            logger.warning(
                "Could not persist synthesis identity for %s: %s",
                output_path.name,
                exc,
            )
        return duration
    finally:
        pending.unlink(missing_ok=True)


async def _adapt_chunk_items(
    ai,
    *,
    plan: list[PlanItem],
    chunk: DubbingChunk,
    segment_by_id: dict[str, LocalizationSegment],
    target_language: str,
    target_ms: int,
    profile: LocalizationProfile,
    issues: list[dict] | None = None,
    state: dict | None = None,
    setup_error: Exception | None = None,
) -> bool:
    items = []
    for idx in chunk.item_indexes:
        item = plan[idx]
        seg = segment_by_id[item.segment_id]
        items.append({
            "id": item.segment_id,
            "source": seg.source_text,
            "current": seg.translated_text or item.spoken_text,
        })
    prompt = f"""Rewrite these {target_language} dubbing lines so the WHOLE group can be spoken naturally in about {target_ms / 1000.0:.2f} seconds.

Rules:
- Preserve every item ID and order.
- Preserve essential meaning, names, numbers and factual claims.
- Do not invent CTAs, claims, examples or prices.
- Make wording concise and natural for speech.
- Return ONLY JSON: [{{"id":"...","text":"..."}}]

ITEMS:
{json.dumps(items, ensure_ascii=False)}"""
    if profile.glossary:
        prompt += "\nGlossary: " + "; ".join(
            f"{k} => {v}" for k, v in profile.glossary.items()
        )[:1800]
    if profile.translation_instructions:
        prompt += f"\nStyle: {profile.translation_instructions[:1200]}"

    def record_unavailable(exc: Exception) -> bool:
        logger.warning(
            "Dubbing rewrite unavailable for chunk %s: %s",
            chunk.id,
            exc,
        )
        if state is not None:
            state["unavailable"] = True
        if issues is not None:
            issues.append({
                "chunk_id": chunk.id,
                "reason": "rewrite_ai_unavailable",
                "error_type": type(exc).__name__,
                "message": str(exc)[:300],
                "segment_ids": [plan[i].segment_id for i in chunk.item_indexes],
            })
        return False

    def record_invalid(detail: str) -> bool:
        if issues is not None:
            issues.append({
                "chunk_id": chunk.id,
                "reason": "rewrite_invalid_response",
                "detail": detail,
                "segment_ids": [plan[i].segment_id for i in chunk.item_indexes],
            })
        return False

    if ai is None:
        return record_unavailable(
            setup_error or RuntimeError("Duration rewrite AI client is unavailable")
        )

    try:
        raw = await ai.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
        )
    except Exception as exc:
        return record_unavailable(exc)

    try:
        payload = json.loads(_strip_json_fence(raw))
    except Exception:
        return record_invalid("json_parse")
    if not isinstance(payload, list) or len(payload) != len(items):
        return record_invalid("shape_or_length")
    mapped: dict[str, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            return record_invalid("item_not_object")
        seg_id = str(item.get("id") or "")
        text = str(item.get("text") or "").strip()
        if not seg_id or not text or seg_id in mapped:
            return record_invalid("invalid_or_duplicate_item")
        mapped[seg_id] = text
    expected = [x["id"] for x in items]
    if set(mapped) != set(expected):
        return record_invalid("id_mismatch")

    changed = False
    for idx in chunk.item_indexes:
        item = plan[idx]
        new_text = mapped[item.segment_id]
        if new_text != item.spoken_text:
            changed = True
        item.spoken_text = new_text
        item.estimated_duration_ms = estimate_spoken_ms(new_text, target_language)
        item.rewrite_attempts += 1
        segment_by_id[item.segment_id].translated_text = new_text
    return changed


def _fit_chunk_audio_sync(source: Path, destination: Path, speed_factor: float) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(source),
        "-filter:a", atempo_filter(speed_factor),
        "-ar", "48000", "-ac", "2",
        "-c:a", "pcm_s16le",
        str(destination),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0 or not destination.exists():
        raise RuntimeError(f"Chunk audio fit failed: {(result.stderr or '')[-500:]}")
    return destination


def _compose_chunk_track_sync(
    timed_chunks: list[tuple[int, Path]],
    *,
    total_duration_ms: int,
    output_path: Path,
) -> Path:
    if not timed_chunks:
        raise RuntimeError("No fitted dubbing chunks to compose")
    total_s = max(total_duration_ms / 1000.0, 0.1)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-t", f"{total_s:.3f}",
        "-i", "anullsrc=r=48000:cl=stereo",
    ]
    for _, path in timed_chunks:
        cmd += ["-i", str(path)]
    filters = ["[0:a]volume=0[base]"]
    labels = ["[base]"]
    for input_idx, (start_ms, _) in enumerate(timed_chunks, start=1):
        delay = max(0, int(start_ms))
        label = f"d{input_idx}"
        filters.append(f"[{input_idx}:a]adelay={delay}|{delay}[{label}]")
        labels.append(f"[{label}]")
    filters.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:duration=longest:normalize=0,"
          f"alimiter=limit=0.95,atrim=0:{total_s:.3f}[mix]"
    )
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[mix]", "-c:a", "pcm_s16le", str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"Dub-track composition failed: {(result.stderr or '')[-500:]}")
    return output_path


def _scale_chunks_for_video_slowdown(
    chunks: list[DubbingChunk],
    factor: float,
) -> None:
    for chunk in chunks:
        chunk.start_ms = round(chunk.start_ms * factor)
        chunk.end_ms = round(chunk.end_ms * factor)


async def synthesize_timed_dub(
    transcript: LocalizationTranscript,
    *,
    source_media: Path,
    target_language: str,
    profile: LocalizationProfile,
    user_settings,
    work_dir: Path,
    fail_on_overflow: bool = True,
    regenerate_chunk_ids: set[int] | None = None,
    reuse_existing_chunks: bool = False,
    reference_media: Path | None = None,
) -> TimedDubResult:
    """C7 donor-derived chunk planner with measured TTS and bounded retiming."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = transcript.model_copy(deep=True)
    rewrite_ai_error: Exception | None = None
    try:
        ai = _profile_ai_client(user_settings, profile)
    except Exception as exc:
        # C7 rewrite is a quality optimization, not a hard dependency. A
        # persisted translation can still be synthesized/measured/fitted even
        # if its original AI provider is temporarily unavailable at resume.
        ai = None
        rewrite_ai_error = exc
        logger.warning("Duration rewrite AI client unavailable: %s", exc)
    runtime = _tts_config(user_settings, profile, target_language)

    valid_segments = [
        seg for seg in out.segments
        if seg.translation_status == "success" and (seg.translated_text or "").strip()
    ]
    if not valid_segments:
        raise RuntimeError("No translated speech is available for dubbing.")

    config = DubbingConfig(
        min_cue_duration_ms=profile.dub_min_cue_duration_ms,
        max_chunk_size=profile.dub_max_chunk_size,
        gap_tolerance_ms=profile.dub_gap_tolerance_ms,
        speed_accept=profile.dub_speed_accept,
        speed_max=profile.dub_speed_max,
        rewrite_max_attempts=profile.dub_rewrite_attempts,
    )
    cues = []
    for i, seg in enumerate(valid_segments):
        override = seg.overrides or {}
        start_ms = int(override.get("target_start_ms", seg.source_start_ms))
        end_ms = int(override.get("target_end_ms", seg.source_end_ms))
        if end_ms <= start_ms:
            raise ValueError(f"Invalid expert timing override for {seg.id}")
        cues.append(DubbingCue(
            index=i + 1,
            segment_id=seg.id,
            start_ms=start_ms,
            end_ms=end_ms,
            text=seg.translated_text or "",
            speaker_id=seg.speaker_id,
            voice_id=(str(override.get("voice_id")).strip() or None)
                if override.get("voice_id") is not None else None,
        ))
    plan, chunks = build_plan(cues, config, language=target_language)
    chunks = borrow_following_silence(
        chunks,
        max_borrow_ms=min(profile.dub_gap_tolerance_ms, 1500),
    )
    segment_by_id = {seg.id: seg for seg in out.segments}

    auto_references: dict[str, dict] = {}
    provider_value = getattr(runtime.provider, "value", str(runtime.provider))
    if provider_value == "gpt_sovits":
        auto_references = await _build_auto_voice_references(
            out,
            reference_media=Path(reference_media or source_media),
            profile=profile,
            work_dir=work_dir,
        )

    raw_paths: dict[int, Path] = {}
    block_paths: list[Path] = []
    regenerate_chunk_ids = set(regenerate_chunk_ids or set())
    issues: list[dict] = []
    rewrite_count = 0

    for chunk in chunks:
        speaker_id = _chunk_speaker(plan, chunk)
        explicit_voice = chunk.voice_id
        voice = _voice_for_chunk(profile, runtime, speaker_id, explicit_voice)
        path = work_dir / f"chunk_{chunk.id:04d}.wav"
        raw_paths[chunk.id] = path
        block_paths.append(path)

        ref_key = speaker_id or "__default__"
        ref = auto_references.get(ref_key) or auto_references.get("__default__") or {}
        initial_text = chunk_text(plan, chunk)
        initial_identity = _chunk_synthesis_identity(
            text=initial_text,
            runtime=runtime,
            voice=voice,
            target_language=target_language,
            speaker_id=speaker_id,
            reference_audio=ref.get("audio"),
            reference_text=ref.get("text"),
            reference_language=ref.get("language"),
        )

        # Targeted/resumed reuse is permitted only when the raw artifact's
        # synthesis identity proves it still matches current speech inputs.
        # This mirrors SmartSub's input-key invariant instead of trusting a
        # filename left over from a previous attempt.
        reuse_raw = (
            reuse_existing_chunks
            and chunk.id not in regenerate_chunk_ids
            and path.exists()
            and path.stat().st_size > 0
            and _chunk_identity_matches(path, initial_identity)
        )

        # OpenCreator-style estimator: try a bounded rewrite before spending on
        # a clearly impossible TTS request. Reused raw chunks already match the
        # durable translation snapshot from their prior successful run.
        estimated = sum(plan[i].estimated_duration_ms for i in chunk.item_indexes)
        attempts = 0
        rewrite_state: dict = {}
        if (
            not reuse_raw
            and estimated > chunk.available_ms * config.speed_max
            and attempts < config.rewrite_max_attempts
        ):
            changed = await _adapt_chunk_items(
                ai, plan=plan, chunk=chunk, segment_by_id=segment_by_id,
                target_language=target_language, target_ms=chunk.available_ms,
                profile=profile, issues=issues, state=rewrite_state,
                setup_error=rewrite_ai_error,
            )
            attempts += 1
            if rewrite_state.get("unavailable"):
                attempts = config.rewrite_max_attempts
            if changed:
                rewrite_count += 1

        speech_text = chunk_text(plan, chunk)
        synthesis_identity = _chunk_synthesis_identity(
            text=speech_text,
            runtime=runtime,
            voice=voice,
            target_language=target_language,
            speaker_id=speaker_id,
            reference_audio=ref.get("audio"),
            reference_text=ref.get("text"),
            reference_language=ref.get("language"),
        )
        # If a rewrite occurred, reuse_raw was already false. Recomputing the
        # identity here makes the newly generated artifact authoritative for the
        # exact post-rewrite speech text.
        reuse_raw = reuse_raw and synthesis_identity == initial_identity

        duration_ms = 0
        if reuse_raw:
            try:
                duration_ms = await _audio_duration_ms(path)
            except Exception as exc:
                logger.warning(
                    "Could not reuse dubbing chunk %s; regenerating: %s",
                    chunk.id,
                    exc,
                )
                reuse_raw = False
            if duration_ms <= 0:
                reuse_raw = False

        if not reuse_raw:
            duration_ms = await _generate_tts_chunk_atomic(
                speech_text,
                runtime=runtime,
                voice=voice,
                profile=profile,
                speaker_id=speaker_id,
                target_language=target_language,
                output_path=path,
                identity=synthesis_identity,
                reference_audio=ref.get("audio"),
                reference_text=ref.get("text"),
                reference_language=ref.get("language"),
            )

        while (
            required_speed(duration_ms, chunk.available_ms) > config.speed_max
            and attempts < config.rewrite_max_attempts
        ):
            changed = await _adapt_chunk_items(
                ai, plan=plan, chunk=chunk, segment_by_id=segment_by_id,
                target_language=target_language, target_ms=chunk.available_ms,
                profile=profile, issues=issues, state=rewrite_state,
                setup_error=rewrite_ai_error,
            )
            attempts += 1
            if rewrite_state.get("unavailable"):
                attempts = config.rewrite_max_attempts
            if not changed:
                break
            rewrite_count += 1
            speech_text = chunk_text(plan, chunk)
            synthesis_identity = _chunk_synthesis_identity(
                text=speech_text,
                runtime=runtime,
                voice=voice,
                target_language=target_language,
                speaker_id=speaker_id,
                reference_audio=ref.get("audio"),
                reference_text=ref.get("text"),
                reference_language=ref.get("language"),
            )
            duration_ms = await _generate_tts_chunk_atomic(
                speech_text,
                runtime=runtime,
                voice=voice,
                profile=profile,
                speaker_id=speaker_id,
                target_language=target_language,
                output_path=path,
                identity=synthesis_identity,
                reference_audio=ref.get("audio"),
                reference_text=ref.get("text"),
                reference_language=ref.get("language"),
            )
        chunk.actual_duration_ms = duration_ms

    # Last resort from the pyVideoTrans family of strategies: if the donor
    # planner still requires over-max speech, enlarge ALL source windows by one
    # bounded visual slowdown. The final renderer applies this in the same
    # visual encode as reframe/captions; TTS itself remains natural.
    raw_required = [
        required_speed(c.actual_duration_ms, c.available_ms)
        for c in chunks
    ]
    max_raw_required_speed = max([1.0, *raw_required])
    needed_slowdown = max(
        [1.0] + [speed / config.speed_max for speed in raw_required if speed > config.speed_max]
    )
    video_slowdown = 1.0
    if needed_slowdown > 1.0 and profile.video_retime_mode == "fallback":
        video_slowdown = min(needed_slowdown, profile.video_retime_max_slowdown)
        _scale_chunks_for_video_slowdown(chunks, video_slowdown)

    unresolved: list[dict] = []
    for chunk in chunks:
        speed = required_speed(chunk.actual_duration_ms, chunk.available_ms)
        if speed > config.speed_max + 1e-6:
            unresolved.append({
                "chunk_id": chunk.id,
                "reason": "timing_overflow",
                "required_speed": round(speed, 4),
                "speed_max": config.speed_max,
                "available_ms": chunk.available_ms,
                "tts_duration_ms": chunk.actual_duration_ms,
                "segment_ids": [plan[i].segment_id for i in chunk.item_indexes],
            })
    if unresolved and fail_on_overflow:
        raise LocalizationTimingError(unresolved)
    issues.extend(unresolved)

    fitted_plan, fitted_chunks, report = fit_timeline(plan, chunks, config)
    report.rewrite_count = rewrite_count

    timed_chunks: list[tuple[int, Path]] = []
    fitted_dir = work_dir / "fitted"
    for chunk in fitted_chunks:
        fitted = fitted_dir / f"chunk_{chunk.id:04d}.wav"
        await asyncio.to_thread(
            _fit_chunk_audio_sync,
            raw_paths[chunk.id],
            fitted,
            chunk.speed_factor,
        )
        block_paths.append(fitted)
        timed_chunks.append((chunk.start_ms, fitted))
        if chunk.speed_factor > config.speed_accept:
            issues.append({
                "chunk_id": chunk.id,
                "reason": "speed_warning",
                "required_speed": round(chunk.speed_factor, 4),
                "speed_accept": config.speed_accept,
                "segment_ids": [fitted_plan[i].segment_id for i in chunk.item_indexes],
            })

    item_by_segment = {item.segment_id: item for item in fitted_plan}
    for seg in out.segments:
        item = item_by_segment.get(seg.id)
        if item is None:
            if seg.translation_status != "success":
                seg.qc_status = "review"
            continue
        seg.target_start_ms = item.target_start_ms
        seg.target_end_ms = item.target_end_ms
        seg.tts_duration_ms = item.target_end_ms - item.target_start_ms
        seg.timing_status = "adjusted" if (
            item.speed_factor > 1.001 or video_slowdown > 1.001
        ) else "fit"
        seg.qc_status = "review" if item.speed_factor > config.speed_accept else "pass"

    from backend.services.video_utils import probe_duration
    source_duration_s = await asyncio.to_thread(probe_duration, source_media, 0.0)
    total_duration_ms = max(
        round(source_duration_s * 1000 * video_slowdown),
        max((seg.target_end_ms or 0) for seg in out.segments),
    )
    dub_track = work_dir / "dub_track.wav"
    await asyncio.to_thread(
        _compose_chunk_track_sync,
        timed_chunks,
        total_duration_ms=total_duration_ms,
        output_path=dub_track,
    )
    max_fitted_speed = max([1.0, *[c.speed_factor for c in fitted_chunks]])
    out.metadata["dubbing"] = {
        "chunk_count": len(fitted_chunks),
        "max_raw_required_speed": max_raw_required_speed,
        "max_fitted_speed": max_fitted_speed,
        "rewrite_count": rewrite_count,
        "video_slowdown": video_slowdown,
        "issues": issues,
    }
    return TimedDubResult(
        transcript=out,
        dub_track=dub_track,
        block_paths=block_paths,
        issues=issues,
        chunk_count=len(fitted_chunks),
        max_speed_factor=max_raw_required_speed,
        max_fitted_speed_factor=max_fitted_speed,
        rewrite_count=rewrite_count,
        video_slowdown=video_slowdown,
        plan=fitted_plan,
        chunks=fitted_chunks,
    )


def _mix_audio_sync(
    source_video: Path,
    dub_track: Path,
    output_path: Path,
    *,
    audio_mode: str,
    background_file: Path | None,
    preserve_background: bool,
    background_gain_db: float,
    dub_gain_db: float,
    video_slowdown: float,
) -> Path:
    """Create the final mixed AUDIO track; no picture is encoded here."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)

    if audio_mode == "hq_clean":
        if background_file is None or not background_file.exists():
            raise RuntimeError("HQ clean dub requires a separated background track.")
        cmd = ["ffmpeg", "-y", "-i", str(dub_track), "-i", str(background_file)]
        bg_chain = f"[1:a]volume={background_gain_db}dB"
        if video_slowdown > 1.001:
            bg_chain += f",atempo={1.0 / video_slowdown:.6f}"
        bg_chain += "[bg]"
        fc = (
            f"[0:a]volume={dub_gain_db}dB[dub];"
            + bg_chain + ";"
            "[dub][bg]amix=inputs=2:duration=longest:normalize=0,"
            "alimiter=limit=0.95[mix]"
        )
    else:
        from backend.services.ffmpeg_service import has_audio_stream_sync
        if not preserve_background or not has_audio_stream_sync(source_video):
            cmd = ["ffmpeg", "-y", "-i", str(dub_track)]
            fc = f"[0:a]volume={dub_gain_db}dB,alimiter=limit=0.95[mix]"
        else:
            cmd = ["ffmpeg", "-y", "-i", str(dub_track), "-i", str(source_video)]
            bg_chain = f"[1:a]volume={background_gain_db}dB"
            if video_slowdown > 1.001:
                bg_chain += f",atempo={1.0 / video_slowdown:.6f}"
            bg_chain += "[bg]"
            fc = (
                f"[0:a]volume={dub_gain_db}dB[dub];"
                + bg_chain + ";"
                "[bg][dub]sidechaincompress=threshold=0.025:ratio=12:"
                "attack=15:release=220[ducked];"
                "[ducked][dub]amix=inputs=2:duration=longest:normalize=0,"
                "alimiter=limit=0.95[mix]"
            )

    cmd += [
        "-filter_complex", fc,
        "-map", "[mix]",
        "-ar", "48000", "-ac", "2",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"Dub/background audio mix failed: {(result.stderr or '')[-500:]}")
    return output_path


async def mix_dub_audio(
    source_video: Path,
    dub_track: Path,
    output_path: Path,
    profile: LocalizationProfile,
    *,
    background_file: Path | None = None,
    video_slowdown: float = 1.0,
) -> Path:
    return await asyncio.to_thread(
        _mix_audio_sync,
        source_video,
        dub_track,
        output_path,
        audio_mode=profile.audio_mode,
        background_file=background_file,
        preserve_background=profile.preserve_background,
        background_gain_db=profile.background_gain_db,
        dub_gain_db=profile.dub_gain_db,
        video_slowdown=video_slowdown,
    )


def persist_dubbing_rewrites(
    translation: LocalizationTranscript,
    dubbed: LocalizationTranscript,
) -> LocalizationTranscript:
    """Persist only spoken-text decisions from C7 back into C6 state.

    C7 may shorten translated lines so real TTS fits the timeline. Target
    timing remains a dubbing-stage concern, but the final spoken wording must
    survive targeted chunk regeneration/resume or reused raw audio can diverge
    from captions/text.
    """
    out = translation.model_copy(deep=True)
    dubbed_by_id = {seg.id: seg for seg in dubbed.segments}
    for seg in out.segments:
        resolved = dubbed_by_id.get(seg.id)
        if resolved is None:
            continue
        if (resolved.translated_text or "").strip():
            seg.translated_text = resolved.translated_text
            seg.translation_status = resolved.translation_status
    return out


def caption_segments_from_localization(transcript: LocalizationTranscript) -> list[dict]:
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
            "speaker_id": seg.speaker_id,
        })
    return out
