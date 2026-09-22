# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""YouTube API quota tracking."""
import logging
from datetime import date

from backend.core.ws_manager import ws_manager

logger = logging.getLogger(__name__)

UPLOAD_COST = 1600
SEARCH_COST = 100
DAILY_LIMIT = 10_000

_usage_today = {"count": 0, "date": None}


async def check_and_consume(operation: str, user_id: str = "local") -> bool:
    """
    Check if we have quota remaining and consume if so.
    Returns True if the operation is allowed, False if quota exhausted.
    """
    today = date.today().isoformat()
    if _usage_today["date"] != today:
        _usage_today["count"] = 0
        _usage_today["date"] = today

    cost = {"upload": UPLOAD_COST, "search": SEARCH_COST}.get(operation, 0)

    if _usage_today["count"] + cost > DAILY_LIMIT:
        await ws_manager.send_constraint_warning(
            constraint="youtube_quota",
            message=f"YouTube API quota nearly exhausted ({_usage_today['count']}/{DAILY_LIMIT} units used today). Scheduling for tomorrow.",
            severity="warning",
            user_id=user_id,
        )
        return False

    _usage_today["count"] += cost
    return True
