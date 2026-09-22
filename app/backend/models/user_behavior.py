# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
from sqlalchemy import Column, String, Text, DateTime
from datetime import datetime
from uuid import uuid4
from backend.database import Base


class UserBehavior(Base):
    __tablename__ = "user_behavior"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), default="local", index=True)
    event_type = Column(String(50), nullable=False, index=True)
    # event_types: niche_searched | video_downloaded | video_generated |
    #              video_uploaded | wizard_completed | setting_configured |
    #              scout_result_clicked | job_started | job_cancelled |
    #              api_usage
    data_json = Column(Text, nullable=True)  # JSON string with event-specific data
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
