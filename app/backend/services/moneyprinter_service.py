# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Wrapper around vendor/moneyprinterturbo for standard-tier video generation.
Uses stock footage from Pexels to create videos.
"""
import asyncio
import logging
from pathlib import Path
from backend.config import settings

logger = logging.getLogger(__name__)


async def generate_stock_video(
    script: str,
    topic: str,
    pexels_api_key: str = "",
    voice_audio_path: str = None,
    aspect_ratio: str = "9:16",
    output_dir: Path = None,
) -> Path:
    """
    Generate a video using stock footage (MoneyPrinterTurbo-style).
    This is a simplified implementation — the full vendor wrapper
    requires vendor/moneyprinterturbo to be set up via git subtree.

    For now, returns None and logs that vendor is not set up.
    """
    if output_dir is None:
        output_dir = settings.GENERATED_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    vendor_path = Path("vendor/moneyprinterturbo")
    if not vendor_path.exists():
        logger.warning(
            "MoneyPrinterTurbo vendor not set up. "
            "Run: git subtree add --prefix=vendor/moneyprinterturbo mpt-upstream main --squash"
        )
        return None

    # When vendor is available, this would call the MPT pipeline
    # For now, return None to indicate standard tier is not yet available
    logger.info("MoneyPrinterTurbo stock video generation — vendor integration pending")
    return None
