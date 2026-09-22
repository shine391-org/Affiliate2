# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Wrapper around vendor/tiktokdownloader for cookie-based TikTok/Douyin scouting.
Falls back gracefully if vendor is not available.
"""
import logging

logger = logging.getLogger(__name__)


async def scout_tiktok_trending(cookie: str, niche: str, max_results: int = 30) -> list[dict]:
    """
    Scout TikTok trending using cookie-based access.
    This is a placeholder — full implementation requires vendor/tiktokdownloader
    to be set up via git subtree.
    """
    logger.info("TikTokDownloader cookie-based scout — vendor not yet set up")
    return []


async def scout_douyin_trending(cookie: str, niche: str, max_results: int = 30) -> list[dict]:
    """
    Scout Douyin trending using cookie-based access.
    Placeholder until vendor/tiktokdownloader is available.
    """
    logger.info("TikTokDownloader Douyin cookie-based scout — vendor not yet set up")
    return []
