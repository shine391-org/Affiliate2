"""Transitional Radar persistence.

Phase B keeps Radar in its existing SQLite database while runtime ownership
moves into the canonical app. Database convergence is a later phase.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def _default_db_path() -> Path:
    root = os.getenv("CONTENT_ENGINE_ROOT")
    if root:
        return Path(root) / "data" / "radar.db"
    return Path(__file__).resolve().parents[4] / "data" / "radar.db"


DB_PATH = Path(os.getenv("AFFILIATE2_RADAR_DB", str(_default_db_path())))

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    content_id TEXT NOT NULL,
    canonical_url TEXT,
    creator_id TEXT,
    creator_name TEXT,
    title TEXT,
    description TEXT,
    publish_time INTEGER,
    source_keyword TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(platform, content_id)
);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    content_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    favorites INTEGER
);

CREATE TABLE IF NOT EXISTS source_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    keyword TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    records_raw INTEGER,
    records_normalized INTEGER,
    records_new INTEGER,
    records_updated INTEGER,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_content_platform_cid
ON content(platform, content_id);

CREATE INDEX IF NOT EXISTS idx_content_platform_creator
ON content(platform, creator_id);

CREATE INDEX IF NOT EXISTS idx_snapshots_platform_cid
ON metric_snapshots(platform, content_id);

CREATE INDEX IF NOT EXISTS idx_snapshots_captured
ON metric_snapshots(captured_at);
"""


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.commit()
    return conn
