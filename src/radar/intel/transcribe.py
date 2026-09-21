"""
intel/transcribe.py — Whisper transcription for Candidate Intelligence (Phase 3.1)

Uses faster-whisper (local, proven in ViralMint Phase 1).
Output: detected language, segments with timestamps, full transcript.
No translation at this step.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_MODEL = None
_MODEL_SIZE = "small"


def _get_model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")
    return _MODEL


def transcribe(media_path: Path, out_path: Path) -> dict:
    """
    Transcribe a media file with faster-whisper.

    Returns dict:
      status: SUCCESS | ERROR
      data:   transcript dict (language, segments, full text)
    """
    try:
        model = _get_model()
        segments_iter, info = model.transcribe(str(media_path), language=None)
        segments = []
        full_text_parts = []
        for s in segments_iter:
            segments.append({
                "start": round(s.start, 2),
                "end": round(s.end, 2),
                "text": s.text.strip(),
            })
            full_text_parts.append(s.text.strip())

        data = {
            "transcribed_at": datetime.now(timezone.utc).isoformat(),
            "model": f"faster-whisper/{_MODEL_SIZE}",
            "detected_language": info.language,
            "language_probability": round(info.language_probability, 3),
            "duration": round(getattr(info, "duration", 0.0), 1),
            "segments": segments,
            "full_transcript": " ".join(full_text_parts),
        }
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"status": "SUCCESS", "data": data}
    except Exception as exc:
        return {"status": "ERROR", "data": {"error": f"{type(exc).__name__}: {exc}"}}
