# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Service API key resolution (BYOK).

Each helper checks the user's encrypted per-user key first, then falls back
to the corresponding `.env` value. Returns "" if neither is set — callers
treat that as "feature unavailable" and degrade gracefully.

Centralizing here keeps the BYOK fallback rules in one place and makes it
easy to add new services without touching every agent/service.
"""
from __future__ import annotations

from typing import Optional

from backend.config import settings as env
from backend.core.crypto import decrypt_safe


def _user_key(user_settings, attr: str) -> str:
    """Decrypt a per-user encrypted key. Returns "" if missing or decryption fails."""
    if not user_settings:
        return ""
    encrypted = getattr(user_settings, attr, None)
    if not encrypted:
        return ""
    return decrypt_safe(encrypted) or ""


def get_youtube_api_key(user_settings=None) -> str:
    """YouTube Data API v3 key. Per-user → .env → ""."""
    return _user_key(user_settings, "youtube_api_key_encrypted") or env.YOUTUBE_API_KEY or ""
