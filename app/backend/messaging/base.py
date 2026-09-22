# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Abstract messaging channel interface.

Every channel (Telegram, WhatsApp, Slack, ...) implements MessagingChannel.
The MessagingManager fans out notifications to all active channels and
routes inbound messages from channels back into the PlannerAgent.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional


PlannerCallback = Callable[[str, str], Awaitable[str]]


class NotificationEvent(str, Enum):
    SCOUT_COMPLETE = "scout_complete"
    DOWNLOAD_COMPLETE = "download_complete"
    VIDEO_GENERATED = "video_generated"
    VIDEO_UPLOADED = "video_uploaded"
    JOB_FAILED = "job_failed"


@dataclass
class NotificationPayload:
    event: NotificationEvent
    title: str
    body: str
    data: dict = field(default_factory=dict)
    # Optional action buttons: [{"label": "Download top 5", "callback": "download:abc"}]
    action_buttons: list[dict] = field(default_factory=list)


class MessagingChannel(ABC):
    """Abstract base for all messaging channels.

    Every channel implements the same lifecycle (start/stop), fan-out API
    (is_configured/send), and per-user management surface (configure/disconnect
    /send_test/status) so backend/api/messaging.py can treat them uniformly.
    """

    channel_name: str = "base"

    # ── Lifecycle ────────────────────────────────────────────────────────────

    @abstractmethod
    async def start(self) -> None:
        """Begin the channel (start polling, open connections, etc)."""

    @abstractmethod
    async def stop(self) -> None:
        """Shut the channel down cleanly."""

    # ── Fan-out (called by MessagingManager.notify) ──────────────────────────

    @abstractmethod
    async def is_configured(self, user_id: str) -> bool:
        """True if this channel has a valid active configuration for the user."""

    @abstractmethod
    async def send(self, user_id: str, payload: NotificationPayload) -> bool:
        """Deliver a notification to the user's chat. Returns True on success."""

    # ── Per-user management (called by backend/api/messaging.py) ─────────────

    @abstractmethod
    async def configure(self, user_id: str, **credentials) -> dict:
        """Persist credentials and bring the channel online for the user.

        Credentials differ per channel: Telegram/Discord take `bot_token`,
        Slack takes `bot_token` + `app_token`, WhatsApp takes none (QR pair).
        Returns channel-specific metadata (bot_username, chat_id, etc.).
        """

    @abstractmethod
    async def disconnect(self, user_id: str) -> bool:
        """Tear down the user's connection and clear their stored credentials."""

    @abstractmethod
    async def send_test(self, user_id: str) -> bool:
        """Send a test notification so the user can confirm the wiring works."""

    @abstractmethod
    def status(self, user_id: str) -> dict:
        """Return a plain-dict snapshot: connected, installed, awaiting_*, etc."""

    # ── Planner wiring (optional override; default is a no-op) ───────────────

    def set_planner_callback(self, callback: PlannerCallback) -> None:
        """Wire the PlannerAgent for two-way chat. Channels that route inbound
        messages override this; others can ignore the callback."""
        return None
