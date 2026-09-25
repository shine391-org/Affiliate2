# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
from sqlalchemy import Column, String, Integer, Float, DateTime
from datetime import datetime
from uuid import uuid4
from backend.database import Base


class VideoMetrics(Base):
    __tablename__ = "video_metrics"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    generated_video_id = Column(String(36), nullable=False, index=True)
    platform = Column(String(20), nullable=False)  # youtube | tiktok | instagram

    # Unknown provider fields stay NULL, never a fabricated zero.
    views = Column(Integer, nullable=True)
    likes = Column(Integer, nullable=True)
    comments = Column(Integer, nullable=True)
    shares = Column(Integer, nullable=True)
    watch_time_hours = Column(Float, nullable=True)    # YouTube only
    avg_view_duration = Column(Float, nullable=True)   # YouTube only (seconds)
    ctr = Column(Float, nullable=True)                 # YouTube only (click-through rate %)

    fetched_at = Column(DateTime, default=datetime.utcnow, index=True)
