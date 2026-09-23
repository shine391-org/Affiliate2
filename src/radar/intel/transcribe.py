"""Compatibility wrapper for canonical Candidate Intelligence ASR."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ._canonical_bridge import ensure_canonical_app_imports

ensure_canonical_app_imports()
from backend.services.candidate_intelligence_service import transcribe_candidate


def transcribe(media_path: Path, out_path: Path) -> dict:
    """Preserve the legacy CLI contract while using canonical Whisper."""
    return asyncio.run(
        transcribe_candidate(
            Path(media_path),
            Path(out_path),
            quality="balanced",
        )
    )
