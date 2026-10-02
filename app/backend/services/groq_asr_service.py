# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Groq cloud ASR adapter for the canonical localization pipeline.

Why a provider-specific HTTP client exists at all: Groq's speech endpoint is
NOT the ``/v1/chat/completions`` contract that ``AIClient`` owns, and the
canonical ASR owner (``whisper_service``) is local-only. AGENTS.md rule 6
allows extending the canonical ASR interface behind one adapter; it does not
allow a second *key/endpoint* owner. So this module still resolves its
credential and endpoint through the canonical encrypted AI-connection registry
(``_saved_ai_connection_config`` + ``decrypt_safe``) and never invents an
endpoint, never stores a second key, and never falls back to a local model.

Contract
--------
- input:  ``LocalizationProfile.asr_connection_id`` (a saved OpenAI-compatible
  connection whose endpoint is Groq) and ``asr_model``.
- output: canonical ``LocalizationTranscript`` carrying only the SOURCE
  timeline (``source_start_ms`` / ``source_end_ms`` / ``source_words``).
- failure: typed ``GroqASRError``. ``fallback_eligible`` is True ONLY for
  transport/timeout/429/5xx conditions. Credential (401/403) and malformed
  request/response failures are terminal: no retry and no fallback.

Media handling: ffmpeg extracts mono 16 kHz FLAC into the app's own scratch
dir (``settings.TMP_DIR``) and the temp file is always removed — success,
failure, or cancellation. Uploads over the Groq free-tier 25 MB limit are
rejected rather than chunked; chunking is intentionally not implemented here.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from backend.config import settings
from backend.core.localization import (
    LocalizationProfile,
    LocalizationSegment,
    LocalizationTranscript,
)

__all__ = [
    "GroqASRError",
    "DEFAULT_ASR_MODEL",
    "GROQ_API_HOST",
    "FREE_TIER_MAX_UPLOAD_BYTES",
    "MAX_ATTEMPTS",
    "MAX_RETRIES",
    "canonicalize_groq_base_url",
    "resolve_groq_connection",
    "transcriptions_url",
    "transcribe_groq",
]

#: Groq's OpenAI-compatible speech model for the fast localization path.
DEFAULT_ASR_MODEL = "whisper-large-v3-turbo"

#: The ONLY host this adapter will talk to. A saved connection pointed
#: elsewhere is a mis-wired ASR route, not a Groq connection.
GROQ_API_HOST = "api.groq.com"
_GROQ_OPENAI_ROOT = "/openai/v1"
_TRANSCRIPTIONS_RESOURCE = "audio/transcriptions"

#: Groq free-tier per-request upload ceiling. Dev tier is 100 MB; the free
#: limit is the safe default. No chunking — an oversized file is an error.
FREE_TIER_MAX_UPLOAD_BYTES = 25_000_000

#: Bounded retry budget. "retries" (not attempts): the initial call plus up to
#: MAX_RETRIES re-calls. The whole loop is additionally bounded by
#: TOTAL_TIMEOUT_S, which is what a Retry-After delay must fit inside.
MAX_RETRIES = 3
MAX_ATTEMPTS = MAX_RETRIES + 1

#: Finite per-attempt network budget and a finite total budget.
ATTEMPT_TIMEOUT_S = 180.0
CONNECT_TIMEOUT_S = 15.0
TOTAL_TIMEOUT_S = 600.0
FFMPEG_TIMEOUT_S = 300.0
RETRY_BACKOFF_S = 1.0

_CREDENTIAL_STATUSES = frozenset({401, 403})

# Module-level seam: tests record honored Retry-After delays without touching
# the global asyncio clock (same pattern as whisper_service's emit interval).
_sleep = asyncio.sleep


class GroqASRError(RuntimeError):
    """Classified Groq ASR failure that is safe to surface without credentials.

    ``fallback_eligible`` is True ONLY for transient conditions (transport
    failure, timeout, HTTP 429, HTTP 5xx). Credential, size, request and
    malformed-response failures are terminal so the caller must not silently
    downgrade to a local model or another provider (AGENTS.md rule 7).
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        status_code: int | None = None,
        fallback_eligible: bool = False,
        retryable: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.fallback_eligible = bool(fallback_eligible)
        self.retryable = bool(retryable)
        self.retry_after = retry_after


# ── endpoint identity ────────────────────────────────────────────────────────

def canonicalize_groq_base_url(base_url: str | None) -> str:
    """Return the canonical ``https://<host>/openai/v1`` root for a Groq endpoint.

    The host must match ``GROQ_API_HOST``. This deliberately refuses to invent
    a URL: an empty or non-Groq endpoint raises instead of defaulting to a
    plausible-looking address. Path is normalized on the official host so both
    ``https://api.groq.com`` and ``https://api.groq.com/openai/v1`` resolve to
    the same transcriptions URL.
    """
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        raise GroqASRError(
            "The saved ASR connection has no endpoint URL.", kind="connection",
        )
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    if host != GROQ_API_HOST:
        raise GroqASRError(
            "The saved ASR connection is not a Groq endpoint "
            f"(host={host or 'unknown'}); expected {GROQ_API_HOST}.",
            kind="connection",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise GroqASRError("Groq endpoint must not contain URL credentials or query tokens.", kind="connection")
    if GROQ_API_HOST == "api.groq.com" and (parsed.scheme != "https" or parsed.port not in {None, 443}):
        raise GroqASRError("Groq credentials require the official HTTPS endpoint.", kind="connection")
    return urlunsplit(
        (parsed.scheme or "https", parsed.netloc, _GROQ_OPENAI_ROOT, "", "")
    )


def transcriptions_url(base_url: str | None) -> str:
    return f"{canonicalize_groq_base_url(base_url)}/{_TRANSCRIPTIONS_RESOURCE}"


def resolve_groq_connection(user_settings, connection_id: str | None) -> tuple[str, str]:
    """Resolve ``(base_url, api_key)`` from the saved encrypted connection registry.

    The canonical resolver and crypto are imported lazily: ``localization_service``
    pulls in heavyweight provider modules, and keeping the import inside the
    call means importing this adapter never drags the whole localization engine
    into memory (and never opens a DB).
    """
    from backend.core.crypto import decrypt_safe
    from backend.services.localization_service import _saved_ai_connection_config

    cid = (connection_id or "").strip()
    if not cid:
        raise GroqASRError(
            "No ASR connection is configured for Groq transcription.",
            kind="connection",
        )
    if user_settings is None:
        raise GroqASRError(
            f"No saved settings available to resolve ASR connection '{cid}'.",
            kind="connection",
        )

    saved = _saved_ai_connection_config(user_settings, cid)
    if not saved:
        raise GroqASRError(
            f"Saved ASR connection not found: {cid}", kind="connection",
        )

    provider = str(saved.get("provider") or "").strip().lower()
    if provider != "compatible":
        raise GroqASRError(
            f"ASR connection '{cid}' uses provider '{provider or 'unknown'}'; "
            "Groq ASR requires a saved OpenAI-compatible connection.",
            kind="connection",
        )

    base_url = canonicalize_groq_base_url(saved.get("base_url"))

    encrypted = saved.get("api_key_encrypted")
    api_key = decrypt_safe(encrypted) if encrypted else ""
    if not api_key:
        raise GroqASRError(
            f"ASR connection '{cid}' has no usable API key.", kind="credential",
        )
    return base_url, api_key


def _resolve_model(profile) -> str:
    model = (getattr(profile, "asr_model", None) or "").strip()
    return model or DEFAULT_ASR_MODEL


# ── media extraction ─────────────────────────────────────────────────────────

def _ffmpeg_extract_cmd(source: Path, target: Path) -> list[str]:
    """Mono 16 kHz FLAC — the smallest lossless container Groq accepts."""
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-y",
        "-i", str(source),
        "-vn", "-map", "0:a:0",
        "-ac", "1", "-ar", "16000",
        "-c:a", "flac", "-f", "flac",
        str(target),
    ]


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    """Best-effort kill + reap, safe to call while a task is being cancelled."""
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except BaseException:  # ProcessLookupError, or a re-delivered cancellation
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except BaseException:
        pass


async def extract_mono16k_flac(media_path) -> Path:
    """Extract mono 16 kHz FLAC into ``settings.TMP_DIR``.

    Cancellation-safe: the ffmpeg child is killed before ``CancelledError``
    propagates, and a partial output is removed. The caller owns deleting the
    returned file on success.
    """
    source = Path(media_path)
    if not source.is_file():
        raise GroqASRError(
            f"Media file not found for Groq transcription: {source}",
            kind="input",
        )

    settings.TMP_DIR.mkdir(parents=True, exist_ok=True)
    target = settings.TMP_DIR / f"groq_asr_{uuid4().hex[:12]}.flac"

    proc = await asyncio.create_subprocess_exec(
        *_ffmpeg_extract_cmd(source, target),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        try:
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=FFMPEG_TIMEOUT_S,
            )
        except asyncio.TimeoutError as exc:
            await _terminate_process(proc)
            raise GroqASRError(
                "ffmpeg audio extraction timed out.", kind="extract",
            ) from exc
        except asyncio.CancelledError:
            await _terminate_process(proc)
            raise

        # FFmpeg may exit 0 on a truncated MP4 while logging 'partial file'.
        # Never upload its surviving prefix as whole-video ASR.
        if proc.returncode != 0 or (stderr or b"").strip():
            detail = (stderr or b"").decode("utf-8", "replace").strip()[-300:]
            raise GroqASRError(
                f"ffmpeg audio extraction failed (exit {proc.returncode}): {detail}",
                kind="extract",
            )
        if not target.is_file() or target.stat().st_size == 0:
            raise GroqASRError(
                "ffmpeg produced no audio for Groq transcription.",
                kind="extract",
            )
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def _check_upload_size(audio_path: Path) -> int:
    size = audio_path.stat().st_size
    if size > FREE_TIER_MAX_UPLOAD_BYTES:
        raise GroqASRError(
            "Extracted audio is "
            f"{size / (1024 * 1024):.1f} MB, over the "
            f"{FREE_TIER_MAX_UPLOAD_BYTES // (1024 * 1024)} MB Groq free-tier "
            "upload limit; chunking is not implemented.",
            kind="too_large",
        )
    return size


# ── retry / Retry-After ──────────────────────────────────────────────────────

def _parse_retry_after(value) -> float | None:
    """Parse a Retry-After header as integer seconds or an HTTP date."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def _retry_delay(error: GroqASRError) -> float:
    """Never shorten a server-provided delay; only add a small default backoff."""
    if error.retry_after is not None:
        return max(0.0, float(error.retry_after))
    return RETRY_BACKOFF_S


def _safe_detail(response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("error")
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("type")
        detail = detail or payload.get("message") or payload.get("detail")
        if detail:
            return str(detail)[:200]
    return str(getattr(response, "text", "") or "provider rejected the request")[:200]


def _classify_response(response) -> GroqASRError:
    status = int(getattr(response, "status_code", 0) or 0)
    detail = _safe_detail(response)
    retry_after = _parse_retry_after(response.headers.get("retry-after"))

    if status in _CREDENTIAL_STATUSES:
        return GroqASRError(
            f"Groq rejected the ASR credential (HTTP {status}); "
            "check the saved connection key.",
            kind="credential", status_code=status,
            fallback_eligible=False, retryable=False,
        )
    if status == 429:
        return GroqASRError(
            f"Groq rate limit reached (HTTP 429). {detail}",
            kind="rate_limit", status_code=status,
            fallback_eligible=True, retryable=True, retry_after=retry_after,
        )
    if status == 408:
        return GroqASRError(
            "Groq request timed out server-side (HTTP 408).",
            kind="network_timeout", status_code=status,
            fallback_eligible=True, retryable=True, retry_after=retry_after,
        )
    if status >= 500:
        return GroqASRError(
            f"Groq server error (HTTP {status}). {detail}",
            kind="server", status_code=status,
            fallback_eligible=True, retryable=True, retry_after=retry_after,
        )
    return GroqASRError(
        f"Groq rejected the request (HTTP {status}). {detail}",
        kind="request", status_code=status,
        fallback_eligible=False, retryable=False,
    )


def _parse_payload(response) -> dict:
    try:
        payload = response.json()
    except Exception as exc:
        raise GroqASRError(
            "Groq returned an invalid JSON body.", kind="invalid_response",
        ) from exc
    if not isinstance(payload, dict):
        raise GroqASRError(
            "Groq returned an unexpected response shape.", kind="invalid_response",
        )
    return payload


async def _post_transcription(
    url: str,
    api_key: str,
    model: str,
    audio_path: Path,
    language: str | None,
) -> dict:
    data: dict = {
        "model": model,
        "response_format": "verbose_json",
        "timestamp_granularities[]": ["word", "segment"],
    }
    if language:
        data["language"] = language
    files = {"file": (audio_path.name, audio_path.read_bytes(), "audio/flac")}
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    timeout = httpx.Timeout(ATTEMPT_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + TOTAL_TIMEOUT_S
    last_error: GroqASRError | None = None

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            error: GroqASRError
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise GroqASRError("Groq transcription total budget exhausted.", kind="network_timeout", fallback_eligible=True)
            try:
                response = await asyncio.wait_for(
                    client.post(url, headers=headers, data=data, files=files),
                    timeout=min(ATTEMPT_TIMEOUT_S, remaining),
                )
            except asyncio.CancelledError:
                raise
            except (httpx.TimeoutException, asyncio.TimeoutError):
                error = GroqASRError(
                    "Groq transcription timed out.",
                    kind="network_timeout", fallback_eligible=True, retryable=True,
                )
            except httpx.HTTPError:
                error = GroqASRError(
                    "Groq is unreachable.",
                    kind="network", fallback_eligible=True, retryable=True,
                )
            else:
                if response.status_code < 400:
                    return _parse_payload(response)
                error = _classify_response(response)
                if not error.retryable:
                    raise error

            last_error = error
            if attempt >= MAX_ATTEMPTS:
                break

            delay = _retry_delay(error)
            if delay > 0:
                remaining = deadline - loop.time()
                if delay >= remaining:
                    # Never sleep LESS than the server demanded; fail instead.
                    raise GroqASRError(
                        "Groq asked to retry after "
                        f"{delay:.1f}s, beyond the remaining transcription budget.",
                        kind=error.kind, status_code=error.status_code,
                        fallback_eligible=True, retryable=False, retry_after=delay,
                    )
                await _sleep(delay)

    assert last_error is not None
    raise last_error


# ── response normalization ───────────────────────────────────────────────────

def _finite_seconds(value, label: str) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise GroqASRError(
            f"Groq returned a non-numeric {label}.", kind="invalid_response",
        ) from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise GroqASRError(
            f"Groq returned an invalid {label}: {value!r}", kind="invalid_response",
        )
    return seconds


def _finite_optional_seconds(value) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _normalize_words(raw_words) -> list[dict]:
    """Normalize word timings to finite seconds, preserving canonical shape.

    Word timings stay in SECONDS to match the existing canonical
    ``source_words`` convention produced by ``whisper_service`` (segment times
    are converted to ms; words are carried verbatim).
    """
    out: list[dict] = []
    if not isinstance(raw_words, list):
        raise GroqASRError("Groq returned a malformed word list.", kind="invalid_response")
    for raw in raw_words or []:
        if not isinstance(raw, dict):
            raise GroqASRError("Groq returned a malformed word.", kind="invalid_response")
        word = str(raw.get("word") or raw.get("text") or "").strip()
        if not word:
            continue
        start = _finite_seconds(raw.get("start"), "word start")
        end = _finite_seconds(raw.get("end"), "word end")
        if end < start:
            raise GroqASRError(
                "Groq returned a word with end before start.",
                kind="invalid_response",
            )
        entry: dict = {"word": word, "start": start, "end": end}
        probability = raw.get("probability")
        if isinstance(probability, (int, float)) and math.isfinite(float(probability)):
            entry["probability"] = float(probability)
        out.append(entry)
    out.sort(key=lambda word: (word["start"], word["end"]))
    return out


def _words_in_span(words: list[dict], start: float, end: float) -> list[dict]:
    epsilon = 1e-3
    return [
        word for word in words
        if word["start"] >= start - epsilon and word["end"] <= end + epsilon
    ]


def _normalize_segments(payload: dict) -> list[tuple[float, float, str, list[dict]]]:
    raw_segments = payload.get("segments")
    if raw_segments is None:
        raw_segments = []
    if not isinstance(raw_segments, list):
        raise GroqASRError(
            "Groq returned a malformed segment list.", kind="invalid_response",
        )
    top_words = _normalize_words(payload.get("words") or [])

    normalized: list[tuple[float, float, str, list[dict]]] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        start = _finite_seconds(raw.get("start"), "segment start")
        end = _finite_seconds(raw.get("end"), "segment end")
        if end < start:
            raise GroqASRError(
                "Groq returned a segment with end before start.",
                kind="invalid_response",
            )
        words = (
            _normalize_words(raw.get("words"))
            if raw.get("words") else _words_in_span(top_words, start, end)
        )
        normalized.append((start, end, text, words))

    # Deterministic order even if the provider ever returns them shuffled.
    normalized.sort(key=lambda item: (item[0], item[1]))
    if top_words:
        # Provider words can straddle sentence boundaries. Assign each ONCE
        # by greatest overlap; do not lose words outside a containment test.
        assigned = [[] for _ in normalized]
        for word in top_words:
            overlaps = [max(0.0, min(word["end"], end) - max(word["start"], start)) for start, end, _, _ in normalized]
            candidates = [i for i, (start, end, _, _) in enumerate(normalized) if start - 0.02 <= word["start"] <= end + 0.02]
            # Groq emits real zero-duration CJK words at shared boundaries.
            # Right-open spans keep those on the new sentence, not the old one.
            exact = [i for i, (start, end, _, _) in enumerate(normalized) if start <= word["start"] < end]
            # Short utterances such as "可以" may have a word interval that
            # overlaps the preceding sentence more. The provider's exact text
            # plus real overlap disambiguate that utterance without inventing
            # timestamps or duplicating/dropping the word.
            token = word["word"].strip().rstrip(".,!?。？！").casefold()
            text_match = [i for i, (_, _, text, _) in enumerate(normalized) if overlaps[i] > 0 and not assigned[i] and text.strip().rstrip(".,!?。？！").casefold() == token]
            if text_match:
                index = text_match[0]
            elif overlaps and max(overlaps) > 0:
                index = max(range(len(overlaps)), key=overlaps.__getitem__)
            elif exact or candidates:
                index = (exact or candidates)[0]
            else:
                raise GroqASRError("Groq word timestamp lies outside all segments.", kind="invalid_response")
            assigned[index].append(word)
        normalized = [(start, end, text, assigned[i]) for i, (start, end, text, _) in enumerate(normalized)]
    return normalized


def _to_transcript(
    payload: dict,
    *,
    model: str,
    upload_bytes: int,
    connection_id: str | None,
    audio_duration_s: float | None = None,
) -> LocalizationTranscript:
    text = str(payload.get("text") or "").strip()
    language = payload.get("language")
    language = str(language).strip() if language else None

    normalized = _normalize_segments(payload)
    if not normalized:
        raise GroqASRError(
            "Groq returned an empty transcript (no speech detected).",
            kind="empty_transcript",
        )

    segments: list[LocalizationSegment] = []
    duration = _finite_seconds(payload["duration"], "duration") if payload.get("duration") is not None else audio_duration_s
    if audio_duration_s is not None and duration is not None and duration > audio_duration_s + 0.25:
        raise GroqASRError("Groq duration exceeds uploaded audio duration.", kind="invalid_response")
    bound = audio_duration_s if audio_duration_s is not None else duration
    for index, (start, end, seg_text, words) in enumerate(normalized):
        if end <= start or (bound is not None and end > bound + 0.1):
            raise GroqASRError("Groq segment timestamps exceed audio bounds or have zero duration.", kind="invalid_response")
        if not words:
            raise GroqASRError("Groq returned speech without word timestamps.", kind="invalid_response")
        if any(bound is not None and word["end"] > bound + 0.1 for word in words):
            raise GroqASRError("Groq word timestamp exceeds audio bounds.", kind="invalid_response")
        segments.append(LocalizationSegment(
            id=f"seg-{index + 1:04d}",
            source_text=seg_text,
            source_start_ms=round(start * 1000),
            source_end_ms=round(end * 1000),
            source_words=words,
        ))

    metadata: dict = {
        "provider": "groq",
        "model": model,
        "connection_id": connection_id,
        "upload_bytes": upload_bytes,
        "segment_count": len(segments),
        "word_count": sum(len(segment.source_words) for segment in segments),
    }
    if duration is not None:
        metadata["duration_s"] = duration

    return LocalizationTranscript(
        text=text or " ".join(segment.source_text for segment in segments).strip(),
        language=language,
        source="groq",
        segments=segments,
        metadata=metadata,
    )


# ── public entry point ───────────────────────────────────────────────────────

async def transcribe_groq(
    media_path,
    profile: LocalizationProfile,
    user_settings,
    language: str | None = None,
) -> LocalizationTranscript:
    """Transcribe ``media_path`` through a saved Groq connection.

    Returns the canonical source-side ``LocalizationTranscript``. Raises
    ``GroqASRError`` (classified) on any failure; never falls back to a local
    model. The temporary extracted audio is removed on every path, including
    task cancellation.
    """
    model = _resolve_model(profile)
    connection_id = getattr(profile, "asr_connection_id", None)

    base_url, api_key = resolve_groq_connection(user_settings, connection_id)
    url = f"{base_url}/{_TRANSCRIPTIONS_RESOURCE}"

    extracted: Path | None = None
    try:
        extracted = await extract_mono16k_flac(media_path)
        upload_bytes = _check_upload_size(extracted)
        # soundfile is already used by the canonical audio engine. Reading the
        # FLAC header is cheap and bounds timestamps against the ACTUAL upload,
        # not only the duration claimed by the provider.
        import soundfile as sf
        audio_duration_s = float(sf.info(extracted).duration)
        if not math.isfinite(audio_duration_s) or audio_duration_s <= 0:
            raise GroqASRError("Extracted audio has no valid duration.", kind="extract")
        payload = await _post_transcription(url, api_key, model, extracted, language)
    finally:
        if extracted is not None:
            extracted.unlink(missing_ok=True)

    return _to_transcript(
        payload,
        model=model,
        upload_bytes=upload_bytes,
        connection_id=connection_id,
        audio_duration_s=audio_duration_s,
    )
