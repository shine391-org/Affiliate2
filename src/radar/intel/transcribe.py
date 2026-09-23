"""Compatibility wrapper for canonical Candidate Intelligence ASR."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ._canonical_bridge import ensure_canonical_app_imports

ensure_canonical_app_imports()
from backend.services.candidate_intelligence_service import transcribe_candidate


async def transcribe_async(media_path: Path, out_path: Path) -> dict:
    """Async adapter used by the converged Radar intelligence pipeline."""
    return await transcribe_candidate(
        Path(media_path),
        Path(out_path),
        quality="balanced",
    )


def transcribe(media_path: Path, out_path: Path) -> dict:
    """Sync compatibility API for older scripts/tests."""
    return asyncio.run(transcribe_async(media_path, out_path))
