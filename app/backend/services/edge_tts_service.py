# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Edge TTS — free Microsoft voices via edge-tts package. Zero cost, no API key."""
import logging
import re
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

    import asyncio
    import edge_tts

    # Add natural pauses via punctuation and use native rate/pitch.
    if not 0.75 <= float(speed) <= 1.25:
        raise VoiceGenerationError("Edge TTS speed must be between 0.75x and 1.25x")
    processed_text = _add_natural_pauses(text) if use_ssml else text
    rate_pct = round((float(speed) - 1.0) * 100)
    rate = f"{rate_pct:+d}%"

    last_error = None
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(
                processed_text,
                voice_id,
                rate=rate,
                pitch="+2Hz",
            )
            await communicate.save(str(output_path))

            if not output_path.exists() or output_path.stat().st_size == 0:
                raise VoiceGenerationError("Edge TTS returned empty audio")

            logger.info(f"Edge TTS generated: {output_path} ({output_path.stat().st_size / 1024:.0f}KB, voice={voice_id})")
            return output_path
        except Exception as e:
            last_error = e
            logger.warning(f"Edge TTS attempt {attempt + 1}/3 failed ({e}), retrying...")
            await asyncio.sleep(1.0)

    raise VoiceGenerationError(f"Edge TTS generation failed after 3 attempts: {last_error}")


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
