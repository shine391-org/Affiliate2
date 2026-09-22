# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""REST /api/chat — CRUD for chat sessions + messages."""
import json
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select, func

from backend.database import AsyncSessionLocal
from backend.models.chat_session import ChatSession, ChatMessage

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    title: Optional[str] = "New chat"


class SessionUpdate(BaseModel):
    title: str


class MessageCreate(BaseModel):
    role: str
    content: Optional[str] = None
    msg_type: Optional[str] = None
    data_json: Optional[str] = None


class MessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: Optional[str]
    msg_type: Optional[str]
    data_json: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class SessionResponse(BaseModel):
    id: str
    title: str
    message_count: int
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# ── Session endpoints ────────────────────────────────────────────────────────

@router.get("/chat/sessions", response_model=list[SessionResponse])
async def list_sessions():
    """List all chat sessions, newest first."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ChatSession)
            .where(ChatSession.user_id == "local")
            .order_by(ChatSession.updated_at.desc())
        )
        sessions = result.scalars().all()
    return sessions


@router.post("/chat/sessions", response_model=SessionResponse, status_code=201)
async def create_session(body: SessionCreate):
    """Create a new chat session."""
    async with AsyncSessionLocal() as db:
        session = ChatSession(user_id="local", title=body.title or "New chat")
        db.add(session)
        await db.commit()
        await db.refresh(session)
    return session


@router.put("/chat/sessions/{session_id}", response_model=SessionResponse)
async def update_session(session_id: str, body: SessionUpdate):
    """Rename a chat session."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == "local")
        )
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        session.title = body.title
        await db.commit()
        await db.refresh(session)
    return session


@router.delete("/chat/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str):
    """Delete a chat session and all its messages."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == "local")
        )
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Delete all messages in this session
        msgs = await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id)
        )
        for msg in msgs.scalars().all():
            await db.delete(msg)

        await db.delete(session)
        await db.commit()


# ── Message endpoints ────────────────────────────────────────────────────────

@router.get("/chat/sessions/{session_id}/messages", response_model=list[MessageResponse])
async def list_messages(session_id: str):
    """Get all messages in a session, ordered chronologically."""
    async with AsyncSessionLocal() as db:
        # Verify session exists
        sess = await db.execute(
            select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == "local")
        )
        if not sess.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Session not found")

        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.asc())
        )
        messages = result.scalars().all()
    return messages


@router.post("/chat/sessions/{session_id}/messages", response_model=MessageResponse, status_code=201)
async def add_message(session_id: str, body: MessageCreate):
    """Add a message to a session (used by the WebSocket handler to persist messages)."""
    async with AsyncSessionLocal() as db:
        # Verify session exists
        sess_result = await db.execute(
            select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == "local")
        )
        session = sess_result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        msg = ChatMessage(
            session_id=session_id,
            user_id="local",
            role=body.role,
            content=body.content,
            msg_type=body.msg_type,
            data_json=body.data_json,
        )
        db.add(msg)

        # Update session metadata
        session.message_count = (session.message_count or 0) + 1
        session.updated_at = datetime.utcnow()

        # Auto-title from first user message if still "New chat"
        if session.title == "New chat" and body.role == "user" and body.content:
            session.title = body.content[:80] + ("..." if len(body.content) > 80 else "")

        await db.commit()
        await db.refresh(msg)
    return msg
