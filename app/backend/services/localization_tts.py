# SPDX-License-Identifier: AGPL-3.0-only
"""TTS synthesis, Edge boundary trimming and voice resolution (C7).

Split out of ``localization_service`` along a responsibility boundary. This
module owns everything between "what voice/provider should speak this cue" and
"here is the measured audio": runtime config resolution, the preflight
validator, speaker/voice/reference selection, per-chunk synthesis with the
Edge boundary trim, and the small sync helpers that measure and concatenate
produced audio.

``localization_service`` re-exports the public names from here and keeps its
import at the END of the file, because this module imports its collaborators
back from that module.

Three helpers are monkeypatched as ATTRIBUTES of ``localization_service`` by
existing tests (``_tts_config``, ``_generate_tts_chunk``,
``_audio_duration_ms``). Their real bodies therefore live in ``*_impl``
functions and the public names dispatch through the facade at call time: a
plain direct import would bind the original object and the patch would stop
reaching the call site, silently turning an injected double into the real
provider.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import re
import subprocess
import uuid
from pathlib import Path

from backend.config import settings
from backend.core.localization import (
    LocalizationProfile,
    LocalizationSegment,
    LocalizationTranscript,
)
from backend.services.dubbing_planner import (
    DubbingChunk,
    PlanItem,
    atempo_filter,
)
from backend.services.dubbing_job_state import write_json_atomic

# The moved code imports its collaborators back from localization_service, and
# the three dispatched helpers resolve through it at CALL TIME so a
# monkeypatch installed on that module still reaches them. Imported as a
# module on purpose: `from ... import name` would bind the original object.
from backend.services import localization_service as _facade
from backend.services.localization_service import (
    _DEFAULT_SPEECH_VOICES,
    _EDGE_TTS_TRIM_END_PAD_S,
    _EDGE_TTS_TRIM_MIN_SAVING_MS,
    _EDGE_TTS_TRIM_MIN_SILENCE_S,
    _EDGE_TTS_TRIM_START_PAD_S,
    _EDGE_TTS_TRIM_THRESHOLD_DB,
    LocalizationTimingError,
    TTSRuntimeConfig,
    _audio_peak_db_sync,
    _await_provider_operation,
    _cancel_checkpoint,
    _load_json_map,
    _register_abandoned_tts_attempt,
    logger,
)


def _tts_chunk_deadline_s() -> float:
    """Deadline bound for one TTS chunk.

    Resolved through the facade because tests shorten this to force the
    budget-expiry branch; a module-level copy here would ignore the patch and
    the test would exercise the real deadline instead.
    """
    return _facade._TTS_CHUNK_DEADLINE_S


def _tts_request_timeout_s() -> float:
    """Per-request provider timeout. Same reason as the deadline bound."""
    return _facade._TTS_REQUEST_TIMEOUT_S


def _default_speech_voice(target_language: str) -> str:
    key = (target_language or "").strip().lower()
    return _DEFAULT_SPEECH_VOICES.get(key, "en-US-AndrewMultilingualNeural")


def _tts_config_impl(user_settings, profile: LocalizationProfile, target_language: str) -> TTSRuntimeConfig:
    from backend.core.crypto import decrypt_safe
    from backend.services.tts_service import DEFAULT_VOICES, TTSProvider

    active_connection_id = (
        getattr(user_settings, "tts_connection_id", None)
        if user_settings is not None else None
    )
    active_connection_id = (active_connection_id or "").strip().lower() or None
    active_provider = (
        getattr(user_settings, "tts_provider", None)
        if user_settings is not None else None
    )
    active_provider = (active_provider or "").strip().lower() or None
    requested_connection_id = (profile.tts_connection_id or "").strip().lower() or None

    registry = _load_json_map(
        getattr(user_settings, "tts_connections_json", None)
        if user_settings is not None else None
    )

    connection_cfg: dict = {}
    connection_id = requested_connection_id

    if requested_connection_id:
        if requested_connection_id == active_connection_id and active_provider:
            connection_cfg = {
                "provider": active_provider,
                "voice": getattr(user_settings, "preferred_tts_voice", None),
                "model": getattr(user_settings, "tts_model", None),
                "base_url": getattr(user_settings, "tts_base_url", None),
                "region": getattr(user_settings, "tts_region", None),
                "api_key_encrypted": getattr(user_settings, "tts_api_key_encrypted", None),
            }
        else:
            connection_cfg = dict(registry.get(requested_connection_id) or {})
        if not connection_cfg:
            raise ValueError(f"Unknown TTS connection: {requested_connection_id}")
    else:
        requested_provider = (profile.tts_provider or "").strip().lower() or None
        # Legacy/materialized jobs may carry only provider. Resolve it to one
        # saved connection when unambiguous so changing global active TTS does
        # not silently alter the old job.
        if requested_provider and requested_provider != active_provider:
            matches = [
                (cid, cfg) for cid, cfg in registry.items()
                if str(cfg.get("provider") or "").strip().lower() == requested_provider
            ]
            if len(matches) == 1:
                connection_id, connection_cfg = matches[0]
            elif len(matches) > 1:
                raise ValueError(
                    f"Multiple TTS connections use provider '{requested_provider}'. "
                    "Pin tts_connection_id in the localization profile."
                )
            elif requested_provider == TTSProvider.EDGE_TTS.value:
                connection_cfg = {"provider": requested_provider}
                connection_id = None
            else:
                # Legacy provider registry fallback.
                legacy = _load_json_map(
                    getattr(user_settings, "tts_provider_profiles_json", None)
                    if user_settings is not None else None
                ).get(requested_provider) or {}
                if legacy:
                    connection_cfg = {"provider": requested_provider, **legacy}
                    connection_id = requested_provider
                else:
                    raise ValueError(
                        f"Profile requests TTS provider '{requested_provider}', "
                        "but no saved TTS connection exists for that provider."
                    )
        else:
            connection_id = active_connection_id
            connection_cfg = {
                "provider": active_provider or TTSProvider.EDGE_TTS.value,
                "voice": getattr(user_settings, "preferred_tts_voice", None)
                    if user_settings is not None else None,
                "model": getattr(user_settings, "tts_model", None)
                    if user_settings is not None else None,
                "base_url": getattr(user_settings, "tts_base_url", None)
                    if user_settings is not None else None,
                "region": getattr(user_settings, "tts_region", None)
                    if user_settings is not None else None,
                "api_key_encrypted": getattr(user_settings, "tts_api_key_encrypted", None)
                    if user_settings is not None else None,
            }

    raw_provider = (
        (profile.tts_provider or "").strip().lower()
        or str(connection_cfg.get("provider") or "").strip().lower()
        or TTSProvider.EDGE_TTS.value
    )
    try:
        provider = TTSProvider(raw_provider)
    except ValueError as exc:
        raise ValueError(f"Unsupported TTS provider: {raw_provider}") from exc

    # Provider type is part of connection identity. A materialized profile may
    # pin it for reproducibility, but it must not reinterpret another
    # connection's secret as a different provider.
    cfg_provider = str(connection_cfg.get("provider") or raw_provider).strip().lower()
    if connection_cfg and cfg_provider and cfg_provider != raw_provider:
        raise ValueError(
            f"TTS connection '{connection_id}' is {cfg_provider}, "
            f"but profile requests {raw_provider}."
        )

    settings_voice = connection_cfg.get("voice")
    explicit_voice = profile.tts_voice or settings_voice
    if explicit_voice:
        voice = explicit_voice
    elif provider in {TTSProvider.EDGE_TTS, TTSProvider.AZURE}:
        voice = _default_speech_voice(target_language)
    else:
        voice = DEFAULT_VOICES.get(provider, "")

    if provider == TTSProvider.ELEVENLABS and not voice:
        raise ValueError("ElevenLabs TTS requires a voice ID.")

    encrypted_tts = connection_cfg.get("api_key_encrypted")
    api_key = decrypt_safe(encrypted_tts) if encrypted_tts else ""

    # OpenAI TTS can reuse an OpenAI AI connection key, but only from an
    # explicitly OpenAI connection. Never reinterpret another provider's key.
    if provider == TTSProvider.OPENAI_TTS and not api_key:
        ai_encrypted = None
        if user_settings is not None:
            active_ai_provider = (getattr(user_settings, "ai_provider", None) or "").strip().lower()
            if active_ai_provider == "openai":
                ai_encrypted = getattr(user_settings, "ai_api_key_encrypted", None)
            if not ai_encrypted:
                ai_connections = _load_json_map(
                    getattr(user_settings, "ai_connections_json", None)
                )
                openai_matches = [
                    cfg for cfg in ai_connections.values()
                    if str(cfg.get("provider") or "").strip().lower() == "openai"
                    and cfg.get("api_key_encrypted")
                ]
                if len(openai_matches) == 1:
                    ai_encrypted = openai_matches[0].get("api_key_encrypted")
            if not ai_encrypted:
                legacy_openai = _load_json_map(
                    getattr(user_settings, "ai_provider_profiles_json", None)
                ).get("openai") or {}
                ai_encrypted = legacy_openai.get("api_key_encrypted")
        if ai_encrypted:
            api_key = decrypt_safe(ai_encrypted) or ""
        api_key = api_key or settings.OPENAI_API_KEY

    # Model/base/region are snapshotted into the effective profile for
    # reproducibility; otherwise inherit from the selected connection.
    model = profile.tts_model or connection_cfg.get("model")
    base_url = profile.tts_base_url or connection_cfg.get("base_url")
    region = profile.tts_region or connection_cfg.get("region")

    if provider == TTSProvider.ELEVENLABS:
        from backend.services.elevenlabs_tts_service import canonicalize_base_url
        base_url = canonicalize_base_url(base_url)

    return TTSRuntimeConfig(
        provider, voice or "", api_key, model, base_url, region,
        connection_id=connection_id,
    )


async def validate_localization_tts_preflight_impl(
    user_settings,
    profile: LocalizationProfile,
    target_language: str,
) -> dict:
    """Fail before separation/ASR when the full-dub TTS binding is unusable."""
    from backend.services import tts_service

    runtime = _facade._tts_config(user_settings, profile, target_language)

    # GPT-SoVITS donor adapter is intentionally language-limited. Validate that
    # deterministic incompatibility before Demucs/ASR/translation consume time.
    if runtime.provider == tts_service.TTSProvider.GPT_SOVITS:
        from backend.services.gpt_sovits_service import _normalize_gpt_sovits_language
        try:
            _normalize_gpt_sovits_language(target_language)
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    health = await tts_service.test_tts_connection(
        provider=runtime.provider,
        api_key=runtime.api_key,
        base_url=runtime.base_url,
        model=runtime.model,
        voice_id=runtime.default_voice,
        region=runtime.region,
    )
    if not health.get("ok"):
        raise ValueError(health.get("message") or "TTS configuration is not usable.")
    return health


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


def _extract_reference_clip_sync_impl(
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
            _facade._extract_reference_clip_sync,
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


async def _audio_duration_ms_impl(path: Path) -> int:
    from backend.services.video_utils import probe_duration
    seconds = await asyncio.to_thread(probe_duration, path, 0.0)
    return max(0, round(float(seconds) * 1000))


def _trim_edge_tts_boundary_silence_sync_impl(
    audio_path: Path,
    raw_duration_ms: int,
) -> int:
    """Trim only leading/trailing digital silence from an Edge-TTS chunk.

    First detect silence regions, then trim only regions touching the start or
    end of the file. Internal pauses are deliberately preserved. A small pad is
    retained on both sides so soft consonant onsets/tails are not clipped.
    Failure is non-fatal: the original artifact/duration remains authoritative.
    """
    from backend.services.video_utils import probe_duration

    if raw_duration_ms <= 0 or not audio_path.is_file():
        return raw_duration_ms

    duration_s = raw_duration_ms / 1000.0
    detect_cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(audio_path),
        "-af",
        (
            "silencedetect="
            f"noise={_EDGE_TTS_TRIM_THRESHOLD_DB}dB:"
            f"d={_EDGE_TTS_TRIM_MIN_SILENCE_S}"
        ),
        "-f", "null", "-",
    ]
    try:
        detected = subprocess.run(
            detect_cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        logger.warning(
            "Edge TTS silence detection failed open for %s: %s",
            audio_path.name,
            exc,
        )
        return raw_duration_ms

    if detected.returncode != 0:
        logger.warning(
            "Edge TTS silence detection skipped for %s: %s",
            audio_path.name,
            (detected.stderr or "")[-300:],
        )
        return raw_duration_ms

    events: list[tuple[str, float]] = []
    pattern = re.compile(r"silence_(start|end):\s*(-?\d+(?:\.\d+)?)")
    for match in pattern.finditer(detected.stderr or ""):
        try:
            events.append((match.group(1), float(match.group(2))))
        except ValueError:
            continue

    regions: list[tuple[float, float]] = []
    open_start: float | None = None
    for kind, value in events:
        if kind == "start":
            open_start = max(0.0, value)
        elif open_start is not None:
            regions.append((open_start, min(duration_s, max(open_start, value))))
            open_start = None
    # Some ffmpeg builds do not emit silence_end when EOF itself is silent.
    if open_start is not None:
        regions.append((open_start, duration_s))

    trim_start_s = 0.0
    trim_end_s = duration_s
    if regions:
        first_start, first_end = regions[0]
        if first_start <= 0.03:
            trim_start_s = max(0.0, first_end - _EDGE_TTS_TRIM_START_PAD_S)

        last_start, last_end = regions[-1]
        if last_end >= duration_s - 0.03:
            trim_end_s = min(
                duration_s,
                last_start + _EDGE_TTS_TRIM_END_PAD_S,
            )

    if trim_end_s <= trim_start_s:
        return raw_duration_ms

    expected_duration_ms = round((trim_end_s - trim_start_s) * 1000)
    if raw_duration_ms - expected_duration_ms < _EDGE_TTS_TRIM_MIN_SAVING_MS:
        return raw_duration_ms

    trimmed = audio_path.with_name(
        audio_path.stem + ".boundary-trim.wav"
    )
    trimmed.unlink(missing_ok=True)
    trim_cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(audio_path),
        "-vn",
        "-af",
        (
            f"atrim=start={trim_start_s:.6f}:end={trim_end_s:.6f},"
            "asetpts=PTS-STARTPTS"
        ),
        "-ar", "48000", "-ac", "2",
        "-c:a", "pcm_s16le",
        str(trimmed),
    ]

    try:
        result = subprocess.run(
            trim_cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if (
            result.returncode != 0
            or not trimmed.exists()
            or trimmed.stat().st_size == 0
        ):
            logger.warning(
                "Edge TTS boundary trim skipped for %s: %s",
                audio_path.name,
                (result.stderr or "")[-300:],
            )
            return raw_duration_ms

        trimmed_duration_ms = max(
            0,
            round(float(probe_duration(trimmed, 0.0)) * 1000),
        )
        saved_ms = raw_duration_ms - trimmed_duration_ms
        if (
            trimmed_duration_ms <= 0
            or trimmed_duration_ms > raw_duration_ms + 20
            or saved_ms < _EDGE_TTS_TRIM_MIN_SAVING_MS
        ):
            return raw_duration_ms

        words_path = audio_path.with_suffix(audio_path.suffix + ".words.json")
        timing = None
        try:
            candidate = json.loads(words_path.read_text(encoding="utf-8"))
            if (candidate.get("version") == 1 and candidate.get("timing_basis") == "provider_word_boundary"
                    and candidate.get("audio_sha256") == hashlib.sha256(audio_path.read_bytes()).hexdigest()):
                timing = candidate
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        trimmed.replace(audio_path)
        # Timings describe the ORIGINAL decoded stream. Shift the valid native
        # events with the exact boundary trim; never rebind stale/partial events.
        try:
            if timing is None:
                words_path.unlink(missing_ok=True)
            else:
                shift_ms = trim_start_s * 1000
                words = []
                for word in timing["words"]:
                    start, end = float(word["start_ms"])-shift_ms, float(word["end_ms"])-shift_ms
                    if (not math.isfinite(start) or not math.isfinite(end) or start < -30
                            or end <= max(0.0, start) or end > trimmed_duration_ms + 30):
                        raise ValueError("Native timing does not fit boundary-trimmed PCM")
                    words.append({**word, "start_ms": max(0.0, start), "end_ms": min(end, trimmed_duration_ms)})
                timing.update(words=words, audio_sha256=hashlib.sha256(audio_path.read_bytes()).hexdigest(),
                              boundary_trim_start_ms=shift_ms, boundary_trim_end_ms=trim_end_s*1000)
                write_json_atomic(words_path, timing)
        except (OSError, ValueError, KeyError, TypeError):
            words_path.unlink(missing_ok=True)
            logger.warning("Edge native timing unavailable after boundary trim: %s", audio_path.name)
        logger.info(
            "Edge TTS boundary trim: %s %dms -> %dms (saved %dms; "
            "start=%.3fs end=%.3fs)",
            audio_path.name,
            raw_duration_ms,
            trimmed_duration_ms,
            saved_ms,
            trim_start_s,
            trim_end_s,
        )
        return trimmed_duration_ms
    except Exception as exc:
        logger.warning(
            "Edge TTS boundary trim failed open for %s: %s",
            audio_path.name,
            exc,
        )
        return raw_duration_ms
    finally:
        trimmed.unlink(missing_ok=True)


def _fit_cadence_chunk_sync_impl(source: Path, destination: Path, placements: list[dict], chunk_start_ms: int) -> None:
    """Retain every decoded PCM partition and add actual source-relative pauses."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    filters = []
    for i, unit in enumerate(placements):
        delay = max(0, round(unit["start_ms"] - chunk_start_ms))
        filters.append(f"[0:a]atrim=start={unit['raw_start_ms']/1000:.6f}:end={unit['raw_end_ms']/1000:.6f},"
                       f"asetpts=PTS-STARTPTS,{atempo_filter(unit['speed_factor'])},adelay={delay}:all=1[u{i}]")
    inputs = "".join(f"[u{i}]" for i in range(len(placements)))
    filters.append(f"{inputs}amix=inputs={len(placements)}:duration=longest:dropout_transition=0:normalize=0[out]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(source),
                    "-filter_complex", ";".join(filters), "-map", "[out]", "-ac", "1", "-ar", "24000",
                    "-c:a", "pcm_s16le", str(destination)], check=True, capture_output=True, timeout=120)


def _concat_tts_parts_sync_impl(parts: list[Path], destination: Path) -> None:
    """Concatenate complete decoded parts; no atrim, words or source fallback."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for path in parts:
        cmd.extend(["-i", str(path)])
    inputs = "".join(f"[{i}:a]" for i in range(len(parts)))
    cmd.extend(["-filter_complex", f"{inputs}concat=n={len(parts)}:v=0:a=1[out]",
                "-map", "[out]", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(destination)])
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    # Probe metadata alone cannot certify the complete concatenated stream.
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-xerror",
                    "-i", str(destination), "-f", "null", "-"],
                   check=True, capture_output=True, timeout=30)


def _edge_zero_audio_transport_failure(exc: Exception, path: Path) -> bool:
    """Only explicit no-audio or a reset before Edge's streaming writer wrote bytes."""
    cause = exc
    while cause is not None:
        if type(cause).__name__ == "NoAudioReceived":
            return True
        if isinstance(cause, ConnectionResetError):
            try:
                return path.stat().st_size == 0
            except FileNotFoundError:
                return True
            except OSError:
                return False
        cause = cause.__cause__
    return False


async def _generate_tts_chunk_impl(
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
    request_timeout_s: float | None = None,
    cancel_check=None,
    recovery_events: list[dict] | None = None,
    _allow_edge_split: bool = True,
    _deadline_at: float | None = None,
) -> int:
    from backend.services.tts_service import generate_tts, retryable_tts_error

    last: Exception | None = None
    deadline = (_deadline_at if _deadline_at is not None else
                asyncio.get_running_loop().time() + _tts_chunk_deadline_s())
    timeout = request_timeout_s if request_timeout_s is not None else _tts_request_timeout_s()
    if timeout <= 0:
        raise ValueError("TTS request timeout must be positive")
    attempts_made = 0
    zero_audio_transport_failure = False
    for attempt in range(max(1, attempts)):
        await _cancel_checkpoint(cancel_check, "TTS dispatch")
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            last = TimeoutError("TTS chunk deadline exhausted")
            break
        attempts_made = attempt + 1
        # Per-ATTEMPT scratch file. A provider writer thread that outlived an
        # earlier attempt finishes writing later and can re-create whatever path
        # it was given; sharing one path across attempts would let attempt N's
        # stale bytes be measured and published as attempt N+1's success.
        attempt_path = output_path.with_name(
            f"{output_path.stem}.attempt-{attempt}-{os.getpid()}-"
            f"{uuid.uuid4().hex[:8]}{output_path.suffix}")
        attempt_path.unlink(missing_ok=True)
        accepted = False
        zero_audio_transport_failure = False
        try:
            await _await_provider_operation(generate_tts(
                text,
                provider=runtime.provider,
                voice_id=voice,
                api_key=runtime.api_key,
                output_path=attempt_path,
                model=runtime.model,
                speed=1.0,
                base_url=runtime.base_url,
                region=runtime.region,
                reference_audio=reference_audio or _reference_for_chunk(profile, speaker_id),
                reference_text=reference_text or profile.tts_reference_text,
                reference_language=reference_language or profile.tts_reference_language,
                target_language=target_language,
            ), timeout_s=min(timeout, remaining), cancel_check=cancel_check)
            duration = await _facade._audio_duration_ms(attempt_path)
            provider_value = getattr(runtime.provider, "value", str(runtime.provider))
            if duration > 0 and provider_value == "edge_tts":
                duration = await asyncio.to_thread(
                    _facade._trim_edge_tts_boundary_silence_sync,
                    attempt_path,
                    duration,
                )
            if duration > 0:
                # Only an attempt that produced its OWN measurable audio may
                # publish, and it publishes by replacing the accepted path.
                attempt_path.replace(output_path)
                attempt_words = attempt_path.with_suffix(attempt_path.suffix + ".words.json")
                output_words = output_path.with_suffix(output_path.suffix + ".words.json")
                output_words.unlink(missing_ok=True)
                if attempt_words.exists():
                    attempt_words.replace(output_words)
                accepted = True
                return duration
            last = RuntimeError("TTS generated zero-duration audio")
        except Exception as exc:
            from backend.core.exceptions import JobCancelledError
            if isinstance(exc, JobCancelledError):
                raise
            last = exc
            # Inspect before abandoned-attempt cleanup can erase partial audio.
            zero_audio_transport_failure = _edge_zero_audio_transport_failure(exc, attempt_path)
            if not retryable_tts_error(exc):
                raise
        finally:
            if not accepted:
                # The writer thread may still re-create this file after we walk
                # away, so hand it to the bounded sweeper instead of sleeping.
                _register_abandoned_tts_attempt(attempt_path)
        if attempt + 1 < max(1, attempts):
            delay = random.uniform(0.25, min(4.0, 0.5 * 2 ** attempt))
            try:
                await _await_provider_operation(asyncio.sleep(delay),
                    timeout_s=max(0.001, deadline - asyncio.get_running_loop().time()),
                    cancel_check=cancel_check)
            except TimeoutError:
                # The budget ran out WHILE WAITING to retry. Cancellation
                # already propagated as JobCancelledError above; only a genuine
                # budget expiry lands here, and it must leave through the same
                # normalized chunk failure as every other exhausted attempt
                # instead of a bare TimeoutError.
                last = TimeoutError("TTS chunk deadline exhausted during retry backoff")
                break
    middle = text.rfind(" ", 0, len(text) // 2 + 1)
    # Transport recovery does not change or approve translation. Strict and
    # Auto share this exact verbatim, same-provider/voice/rate recovery path.
    if (_allow_edge_split and zero_audio_transport_failure
            and getattr(runtime.provider, "value", str(runtime.provider)) == "edge_tts"
            and 0 < middle < len(text)-1
            and deadline - asyncio.get_running_loop().time() > 30):
        import tempfile
        pieces = [text[:middle], text[middle+1:]]
        assert " ".join(pieces) == text
        # ONE fallback, TWO verbatim requests, same voice/rate, same absolute
        # chunk deadline. A failed part never publishes partial/empty speech.
        with tempfile.TemporaryDirectory(prefix="edge-split-", dir=output_path.parent) as temp:
            parts = []
            try:
                for i, piece in enumerate(pieces):
                    path = Path(temp) / f"part-{i}.wav"
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 30:
                        raise TimeoutError("TTS chunk deadline exhausted during split recovery")
                    await _facade._generate_tts_chunk(piece, runtime=runtime, voice=voice, profile=profile,
                        speaker_id=speaker_id, target_language=target_language, output_path=path,
                        reference_audio=reference_audio, reference_text=reference_text,
                        reference_language=reference_language, attempts=1,
                        request_timeout_s=min(timeout, (remaining-30)/2), cancel_check=cancel_check,
                        _allow_edge_split=False, _deadline_at=deadline)
                    peak = await asyncio.to_thread(_audio_peak_db_sync, path)
                    if not math.isfinite(peak):
                        raise RuntimeError("TTS split part produced silent or invalid speech")
                    parts.append(path)
                if deadline - asyncio.get_running_loop().time() <= 30:
                    raise TimeoutError("TTS chunk deadline exhausted before split concat")
                await _cancel_checkpoint(cancel_check, "TTS split concat")
                joined = Path(temp) / "joined.wav"
                await asyncio.to_thread(_facade._concat_tts_parts_sync, parts, joined)
                duration = await _facade._audio_duration_ms(joined)
                peak = await asyncio.to_thread(_audio_peak_db_sync, joined)
                if duration <= 0 or not math.isfinite(peak):
                    raise RuntimeError("TTS split produced silent or invalid speech")
                await _cancel_checkpoint(cancel_check, "TTS split publish")
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("TTS chunk deadline exhausted before split publish")
                joined.replace(output_path)
                # Concatenation has no whole-text measured word-boundary receipt.
                output_path.with_suffix(output_path.suffix + ".words.json").unlink(missing_ok=True)
                if recovery_events is not None:
                    recovery_events.append({"reason": "tts_transport_split", "text": text,
                        "pieces": pieces, "voice": voice, "attempts_before_split": attempts_made,
                        "raw_duration_ms": duration, "requires_human_review": True})
                return duration
            except Exception as exc:
                from backend.core.exceptions import JobCancelledError
                if isinstance(exc, JobCancelledError):
                    raise
                raise RuntimeError(f"TTS verbatim split recovery failed: {exc}") from exc
    reason = "request timeout" if isinstance(last, TimeoutError) else str(last)
    raise RuntimeError(f"TTS chunk failed after {attempts_made} attempts: {reason}") from last


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_audio_duration_ms` is patched as an attribute of localization_service by tests and
# donor integrations. Its body lives in `_audio_duration_ms_impl`; this public name is
# what the internal call sites resolve through the facade, so the patch still
# applies. Dispatching this wrapper back through the facade would recurse -
# the facade attribute IS this wrapper.
async def _audio_duration_ms(*args, **kwargs):
    return await _audio_duration_ms_impl(*args, **kwargs)


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_generate_tts_chunk` is patched as an attribute of localization_service by tests and
# donor integrations. Its body lives in `_generate_tts_chunk_impl`; this public name is
# what the internal call sites resolve through the facade, so the patch still
# applies. Dispatching this wrapper back through the facade would recurse -
# the facade attribute IS this wrapper.
async def _generate_tts_chunk(*args, **kwargs):
    return await _generate_tts_chunk_impl(*args, **kwargs)


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_tts_config` is patched as an attribute of localization_service by tests and
# donor integrations. Its body lives in `_tts_config_impl`; this public name is
# what the internal call sites resolve through the facade, so the patch still
# applies. Dispatching this wrapper back through the facade would recurse -
# the facade attribute IS this wrapper.
def _tts_config(*args, **kwargs):
    return _tts_config_impl(*args, **kwargs)


# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_concat_tts_parts_sync` is patched as an attribute of localization_service by tests and donor
# integrations. Its body lives in `_concat_tts_parts_sync_impl`; this public name is
# what the internal call sites resolve through the facade. Dispatching
# the wrapper back through the facade would recurse - the facade
# attribute IS this wrapper.
def _concat_tts_parts_sync(*args, **kwargs):
    return _concat_tts_parts_sync_impl(*args, **kwargs)

# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_extract_reference_clip_sync` is patched as an attribute of localization_service by tests and donor
# integrations. Its body lives in `_extract_reference_clip_sync_impl`; this public name is
# what the internal call sites resolve through the facade. Dispatching
# the wrapper back through the facade would recurse - the facade
# attribute IS this wrapper.
def _extract_reference_clip_sync(*args, **kwargs):
    return _extract_reference_clip_sync_impl(*args, **kwargs)

# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_fit_cadence_chunk_sync` is patched as an attribute of localization_service by tests and donor
# integrations. Its body lives in `_fit_cadence_chunk_sync_impl`; this public name is
# what the internal call sites resolve through the facade. Dispatching
# the wrapper back through the facade would recurse - the facade
# attribute IS this wrapper.
def _fit_cadence_chunk_sync(*args, **kwargs):
    return _fit_cadence_chunk_sync_impl(*args, **kwargs)

# ── dynamic dispatch seam ──────────────────────────────────────────────
# `_trim_edge_tts_boundary_silence_sync` is patched as an attribute of localization_service by tests and donor
# integrations. Its body lives in `_trim_edge_tts_boundary_silence_sync_impl`; this public name is
# what the internal call sites resolve through the facade. Dispatching
# the wrapper back through the facade would recurse - the facade
# attribute IS this wrapper.
def _trim_edge_tts_boundary_silence_sync(*args, **kwargs):
    return _trim_edge_tts_boundary_silence_sync_impl(*args, **kwargs)

# ── dynamic dispatch seam ──────────────────────────────────────────────
# `validate_localization_tts_preflight` is patched as an attribute of localization_service by tests and donor
# integrations. Its body lives in `validate_localization_tts_preflight_impl`; this public name is
# what the internal call sites resolve through the facade. Dispatching
# the wrapper back through the facade would recurse - the facade
# attribute IS this wrapper.
async def validate_localization_tts_preflight(*args, **kwargs):
    return await validate_localization_tts_preflight_impl(*args, **kwargs)
