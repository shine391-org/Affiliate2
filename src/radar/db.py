"""
db.py — SQLite setup for Radar Ingestion (Phase 2.1)

Three tables:
  content          — deduplicated by (platform, content_id)
  metric_snapshots — immutable observation per scan
  source_runs      — one row per crawl invocation
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "radar.db"

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS content (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT    NOT NULL,
    content_id    TEXT    NOT NULL,
    canonical_url TEXT,
    creator_id    TEXT,
    creator_name  TEXT,
    title         TEXT,
    description   TEXT,
    publish_time  INTEGER,
    source_keyword TEXT,
    first_seen_at TEXT    NOT NULL,
    last_seen_at  TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    UNIQUE(platform, content_id)
);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT    NOT NULL,
    content_id  TEXT    NOT NULL,
    captured_at TEXT    NOT NULL,
    views       INTEGER,
    likes       INTEGER,
    comments    INTEGER,
    shares      INTEGER,
    favorites   INTEGER
);

CREATE TABLE IF NOT EXISTS source_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    platform            TEXT    NOT NULL,
    keyword             TEXT    NOT NULL,
    started_at          TEXT    NOT NULL,
    finished_at         TEXT,
    status              TEXT    NOT NULL DEFAULT 'RUNNING',
    records_raw         INTEGER,
    records_normalized  INTEGER,
    records_new         INTEGER,
    records_updated     INTEGER,
    error               TEXT
);

CREATE INDEX IF NOT EXISTS idx_content_platform_cid ON content(platform, content_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_platform_cid ON metric_snapshots(platform, content_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_captured ON metric_snapshots(captured_at);
"""


def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.commit()
    return conn
