# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Custom caption style model — user-created or AI-generated caption presets."""
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text
from datetime import datetime
from uuid import uuid4
from backend.database import Base


class CaptionStyle(Base):
    __tablename__ = "caption_styles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), default="local", index=True)
    name = Column(String(100), nullable=False)

    # Style properties (matching CAPTION_STYLES dict structure)
    font = Column(String(100), default="Arial Bold")
    font_size_portrait = Column(Integer, default=56)
    font_size_landscape = Column(Integer, default=42)
    primary_color = Column(String(20), default="&H00FFFFFF")       # ASS BGR
    highlight_color = Column(String(20), default="&H0000FFFF")     # ASS BGR
    outline_color = Column(String(20), default="&H00000000")       # ASS BGR
    outline_width = Column(Integer, default=3)
    shadow_depth = Column(Integer, default=1)
    alignment = Column(Integer, default=5)                          # ASS numpad alignment
    margin_v = Column(Integer, default=80)
    words_per_group = Column(Integer, default=3)

    # Metadata
    is_ai_generated = Column(Boolean, default=False)
    description = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)
