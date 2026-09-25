"""Compatibility import for the canonical application Radar store."""
from __future__ import annotations

from backend.affiliate.radar.store import DB_PATH, DDL, get_conn

__all__ = ["DB_PATH", "DDL", "get_conn"]
