# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float
from backend.database import Base
from backend.models.base import TimestampMixin


class UserSettings(Base, TimestampMixin):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), default="local", index=True)  # SaaS-ready

    # ── AI Provider (BYOK) ─────────────────────────────
    ai_provider = Column(String(20), default="openai")              # "anthropic" | "openai" | "openrouter" | "compatible"
    ai_model = Column(String(100), nullable=True)                   # Optional model override
    ai_base_url = Column(String(500), nullable=True)                # Base URL for OpenAI-compatible provider
    ai_api_key_encrypted = Column(Text, nullable=True)              # Optional per-user key (overrides .env)

    # ── Service API keys (BYOK — override .env per-user) ──
    youtube_api_key_encrypted = Column(Text, nullable=True)         # YouTube Data API v3 key
    huggingface_token_encrypted = Column(Text, nullable=True)        # WhisperX/pyannote gated diarization models

    # ── Legacy TikTok upload cookie fallback ───────────
    # UploadAgent may still use an existing encrypted TikTok sessionid when no
    # OAuth token is present. Discovery/download no longer consumes this field.
    tiktok_cookie_encrypted = Column(Text, nullable=True)

    # ── Canonical downloader browser session ───────────
    # Non-secret selectors only. Actual cookies stay in the user's browser and
    # are read by yt-dlp on demand.
    download_cookie_browser = Column(String(20), nullable=True)     # auto|edge|chrome|brave|firefox
    download_cookie_profile = Column(String(200), nullable=True)    # e.g. Default / Profile 1

    # ── YouTube upload OAuth ───────────────────────────
    youtube_credentials_json_encrypted = Column(Text, nullable=True)
    youtube_channel_title = Column(String(200), nullable=True)

    # ── TikTok upload (dual method) ────────────────────
    tiktok_upload_token_encrypted = Column(Text, nullable=True)   # OAuth access token
    tiktok_upload_token_expiry = Column(DateTime, nullable=True)
    tiktok_upload_refresh_token_encrypted = Column(Text, nullable=True)

    # ── My Channels (connected by URL — no OAuth needed) ──
    my_youtube_channel_url = Column(Text, nullable=True)
    my_youtube_channel_id = Column(String(100), nullable=True)
    my_tiktok_profile_url = Column(Text, nullable=True)

    # ── Video generation ───────────────────────────────
    preferred_voice_id = Column(String(100), nullable=True)  # Provider-specific voice ID
    whisper_quality = Column(String(20), default="balanced")  # fast|balanced|accurate|best
    localization_profiles_json = Column(Text, nullable=True)   # Phase-C saved profiles; one JSON payload, not a new table

    # ── TTS Provider ───────────────────────────────────
    tts_provider = Column(String(40), default="edge_tts")
    preferred_tts_voice = Column(String(200), nullable=True)
    tts_model = Column(String(200), nullable=True)
    tts_base_url = Column(Text, nullable=True)
    tts_region = Column(String(100), nullable=True)
    tts_api_key_encrypted = Column(Text, nullable=True)

    # ── Caption Style ──────────────────────────────────
    caption_style = Column(String(20), default="viral")  # viral|classic|bold|none
    caption_enabled = Column(Boolean, default=True)
    caption_emoji_style = Column(String(20), default="moderate")  # none|minimal|moderate|heavy
    auto_zoom_enabled = Column(Boolean, default=False)

    # ── Sound Effects ──────────────────────────────────
    sfx_enabled = Column(Boolean, default=True)
    sfx_style = Column(String(20), default="moderate")  # none|minimal|moderate|heavy

    # ── Background Music ───────────────────────────────
    music_enabled = Column(Boolean, default=True)
    music_genre = Column(String(20), default="lofi")  # lofi|cinematic|upbeat|ambient|corporate
    music_volume_db = Column(Float, default=-14.0)

    # ── Instagram Upload ───────────────────────────────
    instagram_access_token_encrypted = Column(Text, nullable=True)
    instagram_user_id = Column(String(100), nullable=True)

    # ── Upload preferences ─────────────────────────────
    upload_to_youtube = Column(Boolean, default=True)
    upload_to_tiktok = Column(Boolean, default=False)
    tiktok_default_privacy = Column(String(30), default="PUBLIC_TO_EVERYONE")
