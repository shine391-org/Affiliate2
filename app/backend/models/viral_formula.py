# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
from sqlalchemy import Column, String, Text, Integer, DateTime
from datetime import datetime
from uuid import uuid4
from backend.database import Base


class ViralFormula(Base):
    __tablename__ = "viral_formulas"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), default="local", index=True)
    niche = Column(String(200), nullable=False, index=True)
    formula_json = Column(Text, nullable=False)   # full viral formula document
    video_count = Column(Integer, default=0)       # how many videos were analyzed
    source_video_ids_json = Column(Text, nullable=True)  # IDs of downloaded videos used

    created_at = Column(DateTime, default=datetime.utcnow)
