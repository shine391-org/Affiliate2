"""Compatibility wrapper for Phase-C canonical candidate media ingestion."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ._canonical_bridge import PROJECT_ROOT, ensure_canonical_app_imports

ensure_canonical_app_imports()
from backend.services.candidate_intelligence_service import fetch_candidate_media

CANDIDATES_DIR = PROJECT_ROOT / "data" / "candidates"


def candidate_dir(platform: str, content_id: str) -> Path:
    path = CANDIDATES_DIR / f"{platform}_{content_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def fetch_media_async(
    platform: str,
    content_id: str,
    canonical_url: str,
    *,
    title_hint: str = "",
) -> dict:
    """Async adapter used by the converged Radar intelligence pipeline."""
    return await fetch_candidate_media(
        platform,
        content_id,
        canonical_url,
        candidate_dir(platform, content_id),
        title_hint=title_hint,
    )


def fetch_media(
    platform: str,
    content_id: str,
    canonical_url: str,
    *,
    title_hint: str = "",
) -> dict:
    """Sync compatibility API for older scripts/tests."""
    return asyncio.run(
        fetch_media_async(
            platform,
            content_id,
            canonical_url,
            title_hint=title_hint,
        )
    )
