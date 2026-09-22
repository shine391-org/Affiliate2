"""Local persistence for the Phase 3.5 Publish Gate."""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .review_store import find_project_root, utc_now


class PublishValidationError(ValueError):
    pass


class PublishStore:
    def __init__(self, db_path: Path | None = None, project_root: Path | None = None):
        self.root = (project_root or find_project_root()).resolve()
        self.db_path = (db_path or (self.root / "data" / "radar.db")).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS publish_accounts (
                    platform        TEXT PRIMARY KEY,
                    credentials_json TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS publish_oauth_states (
                    state       TEXT PRIMARY KEY,
                    platform    TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS content_publications (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_platform TEXT NOT NULL,
                    content_id      TEXT NOT NULL,
                    platform        TEXT NOT NULL,
                    status          TEXT NOT NULL,
                    title           TEXT,
                    caption         TEXT,
                    privacy         TEXT,
                    external_id     TEXT,
                    external_url    TEXT,
                    error_message   TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    published_at    TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_publications_content
                    ON content_publications(source_platform, content_id, platform, id DESC);
                """
            )

    def set_account(self, platform: str, credentials: dict[str, Any]) -> None:
        if platform not in {"youtube"}:
            raise PublishValidationError(f"Unsupported stored account platform: {platform}")
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO publish_accounts(platform, credentials_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                    credentials_json=excluded.credentials_json,
                    updated_at=excluded.updated_at
                """,
                (platform, json.dumps(credentials), now),
            )

    def get_account(self, platform: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT credentials_json, updated_at FROM publish_accounts WHERE platform=?",
                (platform,),
            ).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row["credentials_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        data["_updated_at"] = row["updated_at"]
        return data

    def delete_account(self, platform: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM publish_accounts WHERE platform=?", (platform,))

    def create_oauth_state(self, platform: str) -> str:
        if platform != "youtube":
            raise PublishValidationError("Only YouTube OAuth is supported")
        state = secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO publish_oauth_states(state, platform, created_at) VALUES (?, ?, ?)",
                (state, platform, utc_now()),
            )
        return state

    def consume_oauth_state(self, state: str, max_age_minutes: int = 15) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT platform, created_at FROM publish_oauth_states WHERE state=?",
                (state,),
            ).fetchone()
            conn.execute("DELETE FROM publish_oauth_states WHERE state=?", (state,))
        if not row or row["platform"] != "youtube":
            return False
        try:
            created = datetime.fromisoformat(row["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return False
        return datetime.now(timezone.utc) - created <= timedelta(minutes=max_age_minutes)

    def create_publication(
        self,
        source_platform: str,
        content_id: str,
        platform: str,
        *,
        title: str | None,
        caption: str | None,
        privacy: str | None = None,
        status: str = "PUBLISHING",
    ) -> int:
        now = utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO content_publications(
                    source_platform, content_id, platform, status,
                    title, caption, privacy,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_platform,
                    content_id,
                    platform,
                    status,
                    title,
                    caption,
                    privacy,
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def finish_publication(
        self,
        publication_id: int,
        *,
        status: str,
        external_id: str | None = None,
        external_url: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = utc_now()
        published_at = now if status == "PUBLISHED" else None
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE content_publications
                SET status=?, external_id=?, external_url=?, error_message=?,
                    updated_at=?, published_at=?
                WHERE id=?
                """,
                (
                    status,
                    external_id,
                    external_url,
                    error_message,
                    now,
                    published_at,
                    publication_id,
                ),
            )

    def list_publications(self, source_platform: str, content_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, platform, status, title, caption, privacy,
                       external_id, external_url, error_message,
                       created_at, updated_at, published_at
                FROM content_publications
                WHERE source_platform=? AND content_id=?
                ORDER BY id DESC
                """,
                (source_platform, content_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_publication(
        self, source_platform: str, content_id: str, platform: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, platform, status, title, caption, privacy,
                       external_id, external_url, error_message,
                       created_at, updated_at, published_at
                FROM content_publications
                WHERE source_platform=? AND content_id=? AND platform=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (source_platform, content_id, platform),
            ).fetchone()
        return dict(row) if row else None
