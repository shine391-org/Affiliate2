"""Canonical application-DB connection for Radar observations."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.config import DATA_DIR

DB_PATH = DATA_DIR / "viralmint.db"

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS scout_results (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'local',
    platform TEXT NOT NULL,
    video_id TEXT NOT NULL,
    video_url TEXT NOT NULL,
    creator_id TEXT,
    author TEXT,
    title TEXT,
    description TEXT,
    upload_date TEXT,
    niche TEXT,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    is_downloaded INTEGER DEFAULT 0,
    is_analyzed INTEGER DEFAULT 0,
    first_seen_at TEXT,
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, platform, video_id)
);

CREATE TABLE IF NOT EXISTS radar_observations (
    id TEXT PRIMARY KEY,
    scout_result_id TEXT NOT NULL,
    legacy_snapshot_id INTEGER,
    captured_at TEXT NOT NULL,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    favorites INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(scout_result_id) REFERENCES scout_results(id)
);

CREATE TABLE IF NOT EXISTS radar_source_runs (
    id TEXT PRIMARY KEY,
    legacy_source_run_id INTEGER,
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

CREATE UNIQUE INDEX IF NOT EXISTS ux_scout_source_identity
ON scout_results(user_id, platform, video_id);

CREATE INDEX IF NOT EXISTS idx_scout_platform_creator
ON scout_results(platform, creator_id);

CREATE INDEX IF NOT EXISTS idx_radar_observations_scout
ON radar_observations(scout_result_id, captured_at);

CREATE INDEX IF NOT EXISTS idx_radar_observations_captured
ON radar_observations(captured_at);
"""


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.commit()
    return conn
