"""Phase 3.2 — Vietnamese TTS via Edge TTS (using ViralMint service)."""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_VOICE_MALE = "vi-VN-NamMinhNeural"
DEFAULT_VOICE_FEMALE = "vi-VN-HoaiMyNeural"

# ViralMint path
_VIRALMINT = Path(__file__).resolve().parents[3] / "tools" / "ViralMint"
if str(_VIRALMINT) not in sys.path:
    sys.path.insert(0, str(_VIRALMINT))


async def generate_tts(
    text: str,
    output_path: Path,
    voice_id: str = DEFAULT_VOICE_MALE,
) -> Path:
    """Generate Vietnamese speech using Edge TTS via ViralMint service."""
    from backend.services.edge_tts_service import generate_voice

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Clean text
    cleaned = " ".join(text.split())

    result = await generate_voice(
        cleaned,
        voice_id=voice_id,
        output_path=output_path,
    )
    return result


def get_duration(audio_path: Path) -> float:
    """Get audio file duration using ffprobe."""
    import json

    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "json",
        str(audio_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if res.returncode != 0:
        return 0.0
    try:
        data = json.loads(res.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except (json.JSONDecodeError, ValueError):
        return 0.0
