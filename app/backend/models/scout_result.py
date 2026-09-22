# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
from sqlalchemy import Column, String, Text, Integer, Float, DateTime, Boolean
from datetime import datetime
from uuid import uuid4
from backend.database import Base


class ScoutResult(Base):
    __tablename__ = "scout_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), default="local", index=True)
    job_id = Column(String(36), nullable=True, index=True)

    # Source metadata
    platform = Column(String(20), nullable=False, index=True)
    # platforms: youtube | tiktok | douyin (plus any yt-dlp-supported platform via dynamic search)
    video_id = Column(String(200), nullable=False)
    video_url = Column(Text, nullable=False)
    embed_url = Column(Text, nullable=True)

    # Content
    title = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    author = Column(String(200), nullable=True)
    author_url = Column(Text, nullable=True)
    thumbnail_url = Column(Text, nullable=True)

    # Metrics
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    duration_seconds = Column(Integer, nullable=True)
    upload_date = Column(DateTime, nullable=True)

    # ViralMint scoring
    virality_score = Column(Float, default=0.0)   # 0-100
    views_per_hour = Column(Float, nullable=True)  # VPH velocity metric
    outlier_score = Column(Float, nullable=True)   # x above channel average
    subscriber_count = Column(Integer, nullable=True)
    channel_avg_views = Column(Integer, nullable=True)
    niche = Column(String(200), nullable=True)     # search niche that found this

    # State
    is_downloaded = Column(Boolean, default=False)
    is_analyzed = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
