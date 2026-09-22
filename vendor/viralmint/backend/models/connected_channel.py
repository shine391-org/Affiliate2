# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Connected YouTube/TikTok channels — supports multiple channels per user."""
from sqlalchemy import Column, String, Text, Integer, DateTime
from datetime import datetime
from uuid import uuid4
from backend.database import Base
from backend.models.base import TimestampMixin


class ConnectedChannel(Base, TimestampMixin):
    __tablename__ = "connected_channels"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), default="local", index=True)

    platform = Column(String(20), nullable=False, index=True)  # youtube | tiktok
    channel_id = Column(String(200), nullable=True)  # YouTube channel ID (UCxxx)
    channel_url = Column(Text, nullable=False)  # Full URL
    channel_name = Column(String(300), nullable=True)  # Display name
    thumbnail_url = Column(Text, nullable=True)
    subscriber_count = Column(Integer, default=0)
    video_count = Column(Integer, default=0)
