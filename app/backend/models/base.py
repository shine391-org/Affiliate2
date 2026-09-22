# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
from datetime import datetime
from uuid import uuid4
from sqlalchemy import Column, String, DateTime
from backend.database import Base


class TimestampMixin:
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UUIDMixin:
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
