# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Edge TTS — free Microsoft voices via edge-tts package. Zero cost, no API key."""
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import tempfile
from pathlib import Path

from backend.config import settings
from backend.core.exceptions import VoiceGenerationError

logger = logging.getLogger(__name__)

# Best natural-sounding Edge TTS voices (multilingual variants sound more human)
RECOMMENDED_VOICES = {
    # English — US (4 multilingual + 3 classic)
    "en-US-AndrewMultilingualNeural": "Andrew (Male, natural)",
    "en-US-AvaMultilingualNeural": "Ava (Female, natural)",
    "en-US-BrianMultilingualNeural": "Brian (Male, warm)",
    "en-US-EmmaMultilingualNeural": "Emma (Female, clear)",
    "en-US-AriaNeural": "Aria (Female, classic)",
    "en-US-GuyNeural": "Guy (Male, classic)",
    "en-US-JennyNeural": "Jenny (Female, friendly)",
    # English — AU / GB
    "en-AU-WilliamMultilingualNeural": "William (Male, Australian)",
    "en-GB-RyanNeural": "Ryan (Male, British)",
    "en-GB-SoniaNeural": "Sonia (Female, British)",
    # Multilingual-tier European voices
    "fr-FR-VivienneMultilingualNeural": "Vivienne (Female, French)",
    "fr-FR-RemyMultilingualNeural": "Remy (Male, French)",
    "de-DE-SeraphinaMultilingualNeural": "Seraphina (Female, German)",
    "de-DE-FlorianMultilingualNeural": "Florian (Male, German)",
    "it-IT-GiuseppeMultilingualNeural": "Giuseppe (Male, Italian)",
    "pt-BR-ThalitaMultilingualNeural": "Thalita (Female, Brazilian Portuguese)",
    # Multilingual-tier Korean
    "ko-KR-HyunsuMultilingualNeural": "Hyunsu (Male, Korean)",
    # Major non-English markets without a Multilingual variant
    "es-ES-XimenaNeural": "Ximena (Female, Spanish)",
    "es-MX-DaliaNeural": "Dalia (Female, Mexican Spanish)",
    "ja-JP-NanamiNeural": "Nanami (Female, Japanese)",
    "zh-CN-XiaoxiaoNeural": "Xiaoxiao (Female, Mandarin)",
    # Vietnamese — primary Affiliate2 localization target
    "vi-VN-HoaiMyNeural": "Hoài My (Female, Vietnamese)",
    "vi-VN-NamMinhNeural": "Nam Minh (Male, Vietnamese)",
}

# Default voice — AndrewMultilingual is significantly more natural than AriaNeural
DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"


def _add_natural_pauses(text: str) -> str:
    """Preserve source punctuation without inventing new prosody boundaries.

    Edge TTS already interprets punctuation. Injecting em-dashes before generic
    transition words such as "Now" or "But" creates pauses that were never in
    the translated script and makes chunk boundaries sound random. Keep only
    normalization of ellipsis that already exists in the input.
    """
    return re.sub(r'\.{3,}\s*', '... ', text)


def _word_metadata_path(audio_path: Path) -> Path:
    """Return the word-metadata sidecar path for an audio file."""
    return audio_path.with_suffix(audio_path.suffix + '.words.json')


def _parse_word_events(metadata_path: Path) -> list[dict] | None:
    """Parse JSONL word-boundary events from an edge_tts metadata file.

    Returns a list of ``{"word", "start_ms", "end_ms"}`` records, or ``None``
    when no valid events are found.  Each line is parsed independently so a
    single corrupt line does not discard the surrounding valid events.
    """
    words: list[dict] = []
    try:
        with open(metadata_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(event, dict) or event.get("type") != "WordBoundary":
                    continue
                offset_100ns = event.get("offset")
                duration_100ns = event.get("duration")
                text = event.get("text")
                if not isinstance(offset_100ns, (int, float)):
                    continue
                if not isinstance(duration_100ns, (int, float)):
                    continue
                if not isinstance(text, str) or not text:
                    continue
                start_ms = offset_100ns / 10_000
                end_ms = (offset_100ns + duration_100ns) / 10_000
                words.append({"word": text, "start_ms": start_ms, "end_ms": end_ms})
    except OSError:
        return None

    if not words:
        return None

    # Validate: finite, non-negative, monotonic start times, end >= start.
    prev_start = -1.0
    for w in words:
        if not math.isfinite(w["start_ms"]) or not math.isfinite(w["end_ms"]):
            return None
        if w["start_ms"] < 0 or w["end_ms"] < 0:
            return None
        if w["end_ms"] < w["start_ms"]:
            return None
        if w["start_ms"] < prev_start:
            return None
        prev_start = w["start_ms"]

    return words


def _write_word_metadata(
    audio_path: Path,
    text: str,
    processed_text: str,
    voice: str,
    words: list[dict],
) -> None:
    """Atomically write the word-metadata sidecar next to *audio_path*."""
    audio_sha256 = hashlib.sha256(audio_path.read_bytes()).hexdigest()
    payload = {
        "version": 1,
        "text": text,
        "processed_text": processed_text,
        "voice": voice,
        "audio_sha256": audio_sha256,
        "timing_basis": "provider_word_boundary",
        "words": words,
    }
    metadata_path = _word_metadata_path(audio_path)
    fd, tmp_str = tempfile.mkstemp(dir=metadata_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp_str, metadata_path)
    except Exception:
        try:
            os.unlink(tmp_str)
        except OSError:
            pass
        raise


async def generate_voice(
    text: str,
    voice_id: str = None,
    output_path: Path = None,
    use_ssml: bool = True,
    speed: float = 1.0,
) -> Path:
    """Generate speech using Edge TTS (free, no API key needed)."""
    if voice_id is None:
        voice_id = DEFAULT_VOICE
    if output_path is None:
        output_path = settings.AUDIO_DIR / f"edge_{hash(text) & 0xFFFFFFFF:08x}.mp3"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    import edge_tts

    # Preserve the script's own punctuation and use native rate/pitch.
    if not 0.75 <= float(speed) <= 1.25:
        raise VoiceGenerationError("Edge TTS speed must be between 0.75x and 1.25x")
    processed_text = _add_natural_pauses(text) if use_ssml else text
    rate_pct = round((float(speed) - 1.0) * 100)
    rate = f"{rate_pct:+d}%"

    # Remove any stale sidecar from a previous generation before starting.
    metadata_path = _word_metadata_path(output_path)
    metadata_path.unlink(missing_ok=True)

    # The localization chunk runner owns bounded retries. A nested retry loop
    # here used to multiply one chunk's three attempts into nine requests.
    try:
        # Pass boundary="WordBoundary" when the installed edge_tts supports it.
        # Older versions lack the kwarg; fall back to their default.
        try:
            communicate = edge_tts.Communicate(
                processed_text, voice_id, rate=rate, pitch="+0Hz", boundary="WordBoundary",
            )
        except TypeError:
            communicate = edge_tts.Communicate(
                processed_text, voice_id, rate=rate, pitch="+0Hz",
            )
        # Write native word-boundary metadata to a temp file so a partial
        # stream never leaves a half-written sidecar next to valid audio.
        fd, tmp_meta_str = tempfile.mkstemp(dir=output_path.parent, suffix=".jsonl")
        os.close(fd)
        tmp_meta_path = Path(tmp_meta_str)
        try:
            try:
                await communicate.save(str(output_path), str(tmp_meta_path))
            except TypeError:
                # Older Communicate.save() signatures lack metadata_fname.
                await communicate.save(str(output_path))
            words = _parse_word_events(tmp_meta_path)
        finally:
            tmp_meta_path.unlink(missing_ok=True)

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise VoiceGenerationError("Edge TTS returned empty audio")

        if words:
            _write_word_metadata(output_path, text, processed_text, voice_id, words)

        return output_path
    except VoiceGenerationError:
        raise
    except Exception as exc:
        raise VoiceGenerationError(f"Edge TTS generation failed: {exc}") from exc


async def get_voice_list() -> list[dict]:
    """List available Edge TTS voices, with recommended ones first."""
    try:
        import edge_tts
        voices = await edge_tts.list_voices()
        result = []
        recommended_ids = set(RECOMMENDED_VOICES.keys())

        # Add recommended voices first
        for v in voices:
            if v["ShortName"] in recommended_ids:
                result.append({
                    "voice_id": v["ShortName"],
                    "name": f"⭐ {RECOMMENDED_VOICES[v['ShortName']]}",
                    "category": v.get("Gender", "Unknown"),
                    "locale": v.get("Locale", ""),
                })

        # Then add all others
        for v in voices:
            if v["ShortName"] not in recommended_ids:
                result.append({
                    "voice_id": v["ShortName"],
                    "name": v["FriendlyName"],
                    "category": v.get("Gender", "Unknown"),
                    "locale": v.get("Locale", ""),
                })

        return result
    except Exception as e:
        logger.error(f"Failed to list Edge TTS voices: {e}")
        return []
