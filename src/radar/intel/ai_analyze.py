"""Compatibility wrapper for canonical Candidate Intelligence AI analysis."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ._canonical_bridge import ensure_canonical_app_imports

ensure_canonical_app_imports()
from backend.services.candidate_intelligence_service import (
    analyze_candidate_intelligence,
    extract_candidate_json,
)

_extract_json = extract_candidate_json


def analyze_candidate(
    metadata: dict,
    transcript: dict | None,
    out_path: Path,
    force: bool = False,
) -> dict:
    """Preserve the legacy Radar CLI API while using canonical AI routing."""
    return asyncio.run(
        analyze_candidate_intelligence(
            metadata,
            transcript,
            Path(out_path),
            force=force,
        )
    )
