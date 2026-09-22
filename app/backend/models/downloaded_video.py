# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
from sqlalchemy import Column, String, Text, Integer, Float, DateTime, Boolean
from datetime import datetime
from uuid import uuid4
from backend.database import Base


class DownloadedVideo(Base):
    __tablename__ = "downloaded_videos"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), default="local", index=True)
    scout_result_id = Column(String(36), nullable=True, index=True)

    # Denormalized from scout_result — survives scout result deletion
    title = Column(Text, nullable=True)
    platform = Column(String(20), nullable=True)

    # File paths
    video_path = Column(Text, nullable=True)
    audio_path = Column(Text, nullable=True)
    thumbnail_path = Column(Text, nullable=True)

    # Transcription
    transcript = Column(Text, nullable=True)
    transcript_language = Column(String(10), nullable=True)
    transcript_source = Column(String(20), nullable=True)  # "creator_subtitles" | "auto_subtitles" | "whisper" | "manual"
    transcript_segments_json = Column(Text, nullable=True)  # Full Whisper [{start, end, text, words}]

    # AI-extracted insights (JSON stored as text)
    insights_json = Column(Text, nullable=True)
    segment_analysis_json = Column(Text, nullable=True)  # per-segment scored retention analysis
    improvement_suggestions_json = Column(Text, nullable=True)  # actionable improvements for user's version
    comments_json = Column(Text, nullable=True)              # raw top comments from platform
    comment_insights_json = Column(Text, nullable=True)      # AI-analyzed comment sentiment & themes

    # Metadata extracted from source video
    chapters_json = Column(Text, nullable=True)   # [{"start": 0, "end": 15, "title": "Hook"}, ...]
    tags_json = Column(Text, nullable=True)       # ["tag1", "tag2", ...] — creator's SEO tags
    category = Column(String(100), nullable=True) # YouTube category (e.g. "Education", "Entertainment")

    duration_seconds = Column(Integer, nullable=True)
    file_size_mb = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
