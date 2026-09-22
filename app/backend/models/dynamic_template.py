# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Dynamic templates generated from trending data."""
from sqlalchemy import Column, String, Text, DateTime, Float
from datetime import datetime
from uuid import uuid4
from backend.database import Base


class DynamicTemplate(Base):
    __tablename__ = "dynamic_templates"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), default="local", index=True)

    mode = Column(String(20), nullable=False, index=True)  # stock | ai | avatar
    niche = Column(String(200), nullable=False, index=True)

    # Template content (same shape as static templates)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    icon = Column(String(10), default="🔥")
    tags_json = Column(Text, default="[]")          # JSON array of tag strings

    # Defaults applied when user selects (JSON dict)
    defaults_json = Column(Text, nullable=False)     # {captionStyle, musicGenre, aspectRatio, scriptInstructions, ...}

    # Provenance
    trend_source = Column(String(50), nullable=True)  # youtube_suggest | scout_results | search_demand
    trend_score = Column(Float, default=0.0)           # relevance score for ordering
    source_data_json = Column(Text, nullable=True)     # keywords/results that inspired this template

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    expires_at = Column(DateTime, nullable=True)       # auto-cleanup after ~7 days
