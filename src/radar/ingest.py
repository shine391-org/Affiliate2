"""
ingest.py — Write SourceRunResult into SQLite (Phase 2.1)

Handles:
  - content UPSERT (dedupe on platform, content_id)
  - metric_snapshots INSERT (always append)
  - source_runs INSERT + UPDATE
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .db import get_conn
from .models import MetricSnapshot, NormalizedContent, SourceRunResult


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_content(conn: sqlite3.Connection, c: NormalizedContent) -> str:
    """
    Insert or update content record.
    Returns 'new' or 'updated'.
    """
    now = _now_iso()
    cur = conn.execute(
        "SELECT id, first_seen_at FROM content WHERE platform=? AND content_id=?",
        (c.platform, c.content_id),
    )
    existing = cur.fetchone()

    if existing is None:
        conn.execute(
            """INSERT INTO content
               (platform, content_id, canonical_url, creator_id, creator_name,
                title, description, publish_time, source_keyword,
                first_seen_at, last_seen_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                c.platform, c.content_id, c.canonical_url, c.creator_id,
                c.creator_name, c.title, c.description, c.publish_time,
                c.source_keyword, now, now, now, now,
            ),
        )
        return "new"
    else:
        conn.execute(
            """UPDATE content SET
               canonical_url=?, creator_id=?, creator_name=?,
               title=?, description=?, publish_time=?,
               last_seen_at=?, updated_at=?
               WHERE platform=? AND content_id=?""",
            (
                c.canonical_url, c.creator_id, c.creator_name,
                c.title, c.description, c.publish_time,
                now, now,
                c.platform, c.content_id,
            ),
        )
        return "updated"


def insert_snapshot(conn: sqlite3.Connection, s: MetricSnapshot) -> None:
    """Always insert a new snapshot row — snapshots are immutable observations."""
    conn.execute(
        """INSERT INTO metric_snapshots
           (platform, content_id, captured_at, views, likes, comments, shares, favorites)
           VALUES (?,?,?,?,?,?,?,?)""",
        (s.platform, s.content_id, s.captured_at,
         s.views, s.likes, s.comments, s.shares, s.favorites),
    )


def start_run(conn: sqlite3.Connection, platform: str, keyword: str) -> int:
    """Insert a source_run row and return its id."""
    now = _now_iso()
    cur = conn.execute(
        """INSERT INTO source_runs (platform, keyword, started_at, status)
           VALUES (?,?,?,'RUNNING')""",
        (platform, keyword, now),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    result: SourceRunResult,
) -> None:
    """Update source_run with final counts and status."""
    now = _now_iso()
    conn.execute(
        """UPDATE source_runs SET
           finished_at=?, status=?,
           records_raw=?, records_normalized=?,
           records_new=?, records_updated=?, error=?
           WHERE id=?""",
        (
            now, result.status,
            result.records_raw, result.records_normalized,
            result.records_new, result.records_updated,
            result.error,
            run_id,
        ),
    )
    conn.commit()


def ingest(result: SourceRunResult) -> SourceRunResult:
    """
    Persist a SourceRunResult into SQLite.

    Mutates result.records_new / records_updated in place.
    Returns the same result object.
    """
    conn = get_conn()
    run_id = start_run(conn, result.platform, result.keyword)

    if result.status not in ("SUCCESS",):
        finish_run(conn, run_id, result)
        return result

    new_count = 0
    updated_count = 0

    try:
        for c, s in zip(result.contents, result.snapshots):
            action = upsert_content(conn, c)
            insert_snapshot(conn, s)
            if action == "new":
                new_count += 1
            else:
                updated_count += 1
        conn.commit()
    except Exception as exc:
        conn.rollback()
        result.status = "ERROR"
        result.error = str(exc)
        finish_run(conn, run_id, result)
        return result

    result.records_new = new_count
    result.records_updated = updated_count
    finish_run(conn, run_id, result)
    return result
