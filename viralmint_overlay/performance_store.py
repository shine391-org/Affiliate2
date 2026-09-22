"""Performance metric persistence for the MVP feedback loop."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .review_store import find_project_root, utc_now


class PerformanceValidationError(ValueError):
    pass


class PerformanceStore:
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
                CREATE TABLE IF NOT EXISTS publication_metrics (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    publication_id  INTEGER NOT NULL,
                    source          TEXT NOT NULL,
                    views           INTEGER,
                    likes           INTEGER,
                    comments        INTEGER,
                    shares          INTEGER,
                    collected_at    TEXT NOT NULL,
                    note            TEXT,
                    raw_json        TEXT,
                    FOREIGN KEY(publication_id) REFERENCES content_publications(id)
                );

                CREATE INDEX IF NOT EXISTS idx_publication_metrics_pub
                    ON publication_metrics(publication_id, id DESC);
                """
            )

    @staticmethod
    def _metric(value: int | None, name: str) -> int | None:
        if value is None:
            return None
        try:
            ivalue = int(value)
        except (TypeError, ValueError) as exc:
            raise PerformanceValidationError(f"{name} must be an integer") from exc
        if ivalue < 0:
            raise PerformanceValidationError(f"{name} must be >= 0")
        return ivalue

    def add_snapshot(
        self,
        publication_id: int,
        *,
        source: str,
        views: int | None,
        likes: int | None,
        comments: int | None,
        shares: int | None,
        note: str | None = None,
        raw: dict[str, Any] | None = None,
        collected_at: str | None = None,
    ) -> dict[str, Any]:
        if source not in {"manual", "youtube_api"}:
            raise PerformanceValidationError(f"Unsupported metric source: {source}")

        with self._connect() as conn:
            pub = conn.execute(
                """
                SELECT id, source_platform, content_id, platform, status
                FROM content_publications
                WHERE id=?
                """,
                (publication_id,),
            ).fetchone()
            if not pub:
                raise PerformanceValidationError("Publication not found")
            if pub["status"] != "PUBLISHED":
                raise PerformanceValidationError("Metrics can only be added to PUBLISHED posts")

        values = {
            "views": self._metric(views, "views"),
            "likes": self._metric(likes, "likes"),
            "comments": self._metric(comments, "comments"),
            "shares": self._metric(shares, "shares"),
        }
        if all(v is None for v in values.values()):
            raise PerformanceValidationError("At least one metric is required")

        when = collected_at or utc_now()
        try:
            parsed = datetime.fromisoformat(when)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            when = parsed.astimezone(timezone.utc).isoformat()
        except ValueError as exc:
            raise PerformanceValidationError("collected_at must be ISO 8601") from exc

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO publication_metrics(
                    publication_id, source, views, likes, comments, shares,
                    collected_at, note, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    publication_id,
                    source,
                    values["views"],
                    values["likes"],
                    values["comments"],
                    values["shares"],
                    when,
                    (note or "").strip() or None,
                    json.dumps(raw or {}, ensure_ascii=False) if raw is not None else None,
                ),
            )
            row = conn.execute(
                """
                SELECT id, publication_id, source, views, likes, comments, shares,
                       collected_at, note
                FROM publication_metrics
                WHERE id=?
                """,
                (cur.lastrowid,),
            ).fetchone()
        return dict(row)

    def get_publication(self, publication_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, source_platform, content_id, platform, job_id, status,
                       title, caption, privacy, external_id, external_url,
                       error_message, created_at, updated_at, published_at
                FROM content_publications
                WHERE id=?
                """,
                (publication_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_snapshots(self, publication_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, publication_id, source, views, likes, comments, shares,
                       collected_at, note
                FROM publication_metrics
                WHERE publication_id=?
                ORDER BY collected_at, id
                """,
                (publication_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_snapshot(self, publication_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, publication_id, source, views, likes, comments, shares,
                       collected_at, note
                FROM publication_metrics
                WHERE publication_id=?
                ORDER BY collected_at DESC, id DESC
                LIMIT 1
                """,
                (publication_id,),
            ).fetchone()
        return dict(row) if row else None

    def published_rows(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, source_platform, content_id, platform, status,
                       title, external_id, external_url, published_at
                FROM content_publications
                WHERE status='PUBLISHED'
                ORDER BY published_at DESC, id DESC
                """
            ).fetchall()
        return [dict(r) for r in rows]
