# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
from sqlalchemy import Column, String, Text, DateTime, Integer
from datetime import datetime
from uuid import uuid4
from backend.database import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), default="local", index=True)
    title = Column(String(500), default="New chat")
    message_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(36), default="local", index=True)
    role = Column(String(20), nullable=False)       # user | assistant | system | rich
    content = Column(Text, nullable=True)
    msg_type = Column(String(30), nullable=True)     # for rich messages: scout_results, job_progress, etc.
    data_json = Column(Text, nullable=True)          # JSON data for rich messages
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
