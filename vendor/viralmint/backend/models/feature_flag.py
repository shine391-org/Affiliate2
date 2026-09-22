# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
from sqlalchemy import Column, String, Boolean, Integer
from uuid import uuid4
from backend.database import Base


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), default="local", index=True)
    feature = Column(String(100), nullable=False)
    # features: premium_video | unlimited_scouts | multi_channel |
    #           tiktok_upload | advanced_analytics | api_access
    enabled = Column(Boolean, default=True)
    limit_value = Column(Integer, nullable=True)  # e.g. 10 videos/month
