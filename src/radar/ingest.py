"""Compatibility adapter from legacy Radar dataclasses to canonical storage.

The CLI keeps its historical dataclass surface, but all rows are written into
canonical application tables in ``viralmint.db``.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from .db import get_conn
from .models import MetricSnapshot, NormalizedContent, SourceRunResult


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upload_date(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def upsert_content(conn: sqlite3.Connection, content: NormalizedContent) -> str:
    """Upsert one source identity in canonical ``scout_results``."""
    now = _now_iso()
    existing = conn.execute(
        """SELECT id FROM scout_results
        WHERE user_id='local' AND platform=? AND video_id=?""",
        (content.platform, content.content_id),
    ).fetchone()
    if existing is None:
        conn.execute(
            """INSERT INTO scout_results(
                id,user_id,platform,video_id,video_url,creator_id,author,title,
                description,upload_date,niche,first_seen_at,last_seen_at,created_at,
                is_downloaded,is_analyzed
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)""",
            (
                str(uuid4()), "local", content.platform, content.content_id,
                content.canonical_url or f"{content.platform}:{content.content_id}",
                content.creator_id, content.creator_name, content.title,
                content.description, _upload_date(content.publish_time),
                content.source_keyword, now, now, now,
            ),
        )
        return "new"
    conn.execute(
        """UPDATE scout_results SET
            video_url=COALESCE(?,video_url), creator_id=COALESCE(?,creator_id),
            author=COALESCE(?,author), title=COALESCE(?,title),
            description=COALESCE(?,description), upload_date=COALESCE(?,upload_date),
            last_seen_at=?
        WHERE id=?""",
        (
            content.canonical_url, content.creator_id, content.creator_name,
            content.title, content.description, _upload_date(content.publish_time),
            now, existing["id"],
        ),
    )
    return "updated"


def insert_snapshot(conn: sqlite3.Connection, snapshot: MetricSnapshot) -> None:
    """Append one immutable observation linked to the exact ScoutResult."""
    scout = conn.execute(
        """SELECT id FROM scout_results
        WHERE user_id='local' AND platform=? AND video_id=?""",
        (snapshot.platform, snapshot.content_id),
    ).fetchone()
    if scout is None:
        raise ValueError(
            f"Cannot store observation without ScoutResult: "
            f"{snapshot.platform}:{snapshot.content_id}"
        )
    conn.execute(
        """INSERT INTO radar_observations(
            id,scout_result_id,captured_at,views,likes,comments,shares,favorites,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid4()), scout["id"], snapshot.captured_at, snapshot.views,
            snapshot.likes, snapshot.comments, snapshot.shares, snapshot.favorites,
            _now_iso(),
        ),
    )


def _record_run(conn: sqlite3.Connection, result: SourceRunResult) -> None:
    now = _now_iso()
    conn.execute(
        """INSERT INTO radar_source_runs(
            id,platform,keyword,started_at,finished_at,status,records_raw,
            records_normalized,records_new,records_updated,error
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid4()), result.platform, result.keyword, now, now, result.status,
            result.records_raw, result.records_normalized, result.records_new,
            result.records_updated, result.error,
        ),
    )


def ingest(result: SourceRunResult) -> SourceRunResult:
    """Persist one source result through the canonical application DB."""
    conn = get_conn()
    try:
        if result.status == "SUCCESS":
            new_count = 0
            updated_count = 0
            for content, snapshot in zip(result.contents, result.snapshots):
                action = upsert_content(conn, content)
                insert_snapshot(conn, snapshot)
                new_count += action == "new"
                updated_count += action == "updated"
            result.records_new = new_count
            result.records_updated = updated_count
        _record_run(conn, result)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        result.status = "ERROR"
        result.error = str(exc)
        _record_run(conn, result)
        conn.commit()
    return result
