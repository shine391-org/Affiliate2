# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
UserProfile — AI-generated evolving summary of user preferences and behavior.
Updated periodically by AI based on accumulated behavior events.
Stored locally in SQLite.
"""
from sqlalchemy import Column, String, Text, DateTime, Integer
from datetime import datetime
from uuid import uuid4
from backend.database import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), default="local", unique=True, index=True)

    # AI-generated profile (JSON) — the core intelligence
    profile_json = Column(Text, nullable=True)
    # Structure:
    # {
    #   "niches": ["personal finance", "tech reviews"],
    #   "primary_language": "en",
    #   "content_languages": ["en", "zh"],
    #   "preferred_platforms": ["youtube", "tiktok"],
    #   "preferred_video_style": "short-form vertical",
    #   "preferred_voice_tone": "casual, energetic",
    #   "active_hours": "evenings UTC+8",
    #   "generation_preferences": {
    #     "aspect_ratio": "9:16",
    #     "caption_style": "viral",
    #     "tts_provider": "edge_tts",
    #     "video_tier": "free"
    #   },
    #   "behavior_patterns": ["downloads top 3-5", "prefers score > 70"],
    #   "ai_notes": "Experienced creator, prefers direct suggestions..."
    # }

    # AI-generated smart suggestions (cached, 1-hour TTL)
    suggestions_json = Column(Text, nullable=True)
    suggestions_updated_at = Column(DateTime, nullable=True)

    # Tracking when profile was last updated
    events_since_last_update = Column(Integer, default=0)
    last_profile_update = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
