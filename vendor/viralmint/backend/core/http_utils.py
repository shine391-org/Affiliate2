# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Centralized HTTP utilities for browser-like request headers.
Provides a rotating pool of realistic User-Agent strings covering
Chrome, Firefox, Safari, Edge, Brave, and Opera across multiple OS variants.
"""
import random

# Realistic User-Agent strings — 6 browsers × multiple OS combos
# Versions reflect late-2025 / early-2026 browser releases.
_USER_AGENTS = [
    # ── Chrome (Windows / macOS / Linux) ──────────────────────────
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",

    # ── Firefox (Windows / macOS / Linux) ─────────────────────────
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:134.0) Gecko/20100101 Firefox/134.0",

    # ── Safari (macOS only) ───────────────────────────────────────
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",

    # ── Edge (Windows / macOS) ────────────────────────────────────
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",

    # ── Brave (Windows / macOS) ───────────────────────────────────
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Brave/131",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Brave/131",

    # ── Opera (Windows / macOS) ───────────────────────────────────
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/116.0.0.0",
]

# Inter-download delay range (seconds) — generous spacing to avoid rate limiting
# YouTube/TikTok aggressively throttle rapid sequential downloads
INTER_DOWNLOAD_DELAY_MIN = 15
INTER_DOWNLOAD_DELAY_MAX = 30


def get_user_agent() -> str:
    """Return a random User-Agent string from the multi-browser pool."""
    return random.choice(_USER_AGENTS)


def get_default_headers() -> dict:
    """Return standard browser-like headers for HTTP requests."""
    return {
        "User-Agent": get_user_agent(),
        "Accept-Language": "en-US,en;q=0.9",
    }


def jittered_delay() -> float:
    """Return a randomized delay (seconds) for spacing out downloads."""
    return random.uniform(INTER_DOWNLOAD_DELAY_MIN, INTER_DOWNLOAD_DELAY_MAX)
