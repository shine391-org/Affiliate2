"""Affiliate2 Radar observations + deterministic longitudinal signals."""
from __future__ import annotations

import math
import sqlite3
import statistics
from datetime import datetime, timezone
from typing import Iterable

from .store import get_conn

SUPPORTED_PLATFORMS = {"douyin", "bilibili"}
VELOCITY_MIN_INTERVAL_SECONDS = 30 * 60
CREATOR_BASELINE_MIN_HISTORY = 5

WEIGHTS = {
    "freshness": 0.30,
    "engagement": 0.50,
    "velocity": 0.15,
    "creator": 0.05,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _publish_timestamp(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _metric_value(row: dict, name: str) -> int | None:
    metrics = row.get("radar_metrics")
    value = metrics.get(name) if isinstance(metrics, dict) and name in metrics else row.get(name)
    if value is None or value == "":
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def _engagement_total(row: dict) -> float:
    return float(
        (row.get("views") or 0)
        + (row.get("likes") or 0)
        + (row.get("comments") or 0)
        + (row.get("shares") or 0)
        + (row.get("favorites") or 0)
    )


def _percentile(values: list[float], target: float) -> float:
    if not values or len(values) == 1:
        return 50.0
    below = sum(1 for value in values if value < target)
    equal = sum(1 for value in values if value == target)
    return round((below + 0.5 * equal) / len(values) * 100, 2)


def _freshness_raw(publish_time: int | None, now_ts: float) -> float | None:
    if publish_time is None:
        return None
    age = max(now_ts - publish_time, 0)
    return 100.0 * (0.5 ** (age / (7 * 24 * 3600)))


def _snapshot_engagement(row: dict) -> float:
    return float(
        (row.get("views") or 0)
        + (row.get("likes") or 0)
        + (row.get("comments") or 0)
        + (row.get("shares") or 0)
        + (row.get("favorites") or 0)
    )


def _velocity_per_hour(snapshots: list[dict]) -> float | None:
    if len(snapshots) < 2:
        return None
    first, last = snapshots[0], snapshots[-1]
    t1 = datetime.fromisoformat(first["captured_at"].replace("Z", "+00:00"))
    t2 = datetime.fromisoformat(last["captured_at"].replace("Z", "+00:00"))
    elapsed = (t2 - t1).total_seconds()
    if elapsed < VELOCITY_MIN_INTERVAL_SECONDS:
        return None
    delta = max(_snapshot_engagement(last) - _snapshot_engagement(first), 0.0)
    return round(delta / (elapsed / 3600.0), 3)


def observe_scout_rows(rows: list[dict], niche: str, conn: sqlite3.Connection | None = None) -> dict:
    """Append one immutable observation per unique native-provider content row."""
    own_conn = conn is None
    conn = conn or get_conn()
    captured_at = _now_iso()
    seen: set[tuple[str, str]] = set()
    per_platform: dict[str, dict[str, int]] = {}

    try:
        for row in rows:
            platform = str(row.get("platform") or "").lower()
            content_id = str(row.get("video_id") or "").strip()
            key = (platform, content_id)
            if platform not in SUPPORTED_PLATFORMS or not content_id or key in seen:
                continue
            seen.add(key)
            stats = per_platform.setdefault(platform, {"observed": 0, "new": 0, "updated": 0})

            existing = conn.execute(
                "SELECT id FROM content WHERE platform=? AND content_id=?",
                key,
            ).fetchone()
            values = (
                row.get("video_url"),
                row.get("creator_id"),
                row.get("author"),
                row.get("title"),
                row.get("description"),
                _publish_timestamp(row.get("upload_date")),
                row.get("source_keyword") or niche,
            )
            if existing is None:
                conn.execute(
                    """INSERT INTO content
                    (platform, content_id, canonical_url, creator_id, creator_name,
                     title, description, publish_time, source_keyword,
                     first_seen_at, last_seen_at, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (platform, content_id, *values, captured_at, captured_at, captured_at, captured_at),
                )
                stats["new"] += 1
            else:
                conn.execute(
                    """UPDATE content SET
                    canonical_url=?, creator_id=?, creator_name=?, title=?,
                    description=?, publish_time=?, source_keyword=?,
                    last_seen_at=?, updated_at=?
                    WHERE platform=? AND content_id=?""",
                    (*values, captured_at, captured_at, platform, content_id),
                )
                stats["updated"] += 1

            conn.execute(
                """INSERT INTO metric_snapshots
                (platform, content_id, captured_at, views, likes, comments, shares, favorites)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    platform,
                    content_id,
                    captured_at,
                    _metric_value(row, "views"),
                    _metric_value(row, "likes"),
                    _metric_value(row, "comments"),
                    _metric_value(row, "shares"),
                    _metric_value(row, "favorites"),
                ),
            )
            stats["observed"] += 1

        for platform, stats in per_platform.items():
            conn.execute(
                """INSERT INTO source_runs
                (platform, keyword, started_at, finished_at, status,
                 records_raw, records_normalized, records_new, records_updated)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    platform,
                    niche,
                    captured_at,
                    captured_at,
                    "SUCCESS",
                    stats["observed"],
                    stats["observed"],
                    stats["new"],
                    stats["updated"],
                ),
            )
        conn.commit()
        return {
            "observed": sum(v["observed"] for v in per_platform.values()),
            "new": sum(v["new"] for v in per_platform.values()),
            "updated": sum(v["updated"] for v in per_platform.values()),
            "captured_at": captured_at,
        }
    finally:
        if own_conn:
            conn.close()


def _latest_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT
            c.platform, c.content_id, c.canonical_url, c.creator_id,
            c.creator_name, c.title, c.publish_time,
            s.views, s.likes, s.comments, s.shares, s.favorites,
            s.captured_at,
            (
                SELECT COUNT(*) FROM metric_snapshots ms
                WHERE ms.platform=c.platform AND ms.content_id=c.content_id
            ) AS snapshot_count
        FROM content c
        JOIN metric_snapshots s
          ON s.platform=c.platform AND s.content_id=c.content_id
        WHERE s.id = (
            SELECT ms2.id FROM metric_snapshots ms2
            WHERE ms2.platform=c.platform AND ms2.content_id=c.content_id
            ORDER BY ms2.captured_at DESC, ms2.id DESC
            LIMIT 1
        )
        ORDER BY c.id"""
    ).fetchall()
    return [dict(row) for row in rows]


def _snapshots(conn: sqlite3.Connection, platform: str, content_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT captured_at, views, likes, comments, shares, favorites
        FROM metric_snapshots
        WHERE platform=? AND content_id=?
        ORDER BY captured_at ASC, id ASC""",
        (platform, content_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _creator_history_values(rows: list[dict], platform: str, creator_id: str | None, current_id: str) -> list[float]:
    if not creator_id:
        return []
    return [
        _engagement_total(row)
        for row in rows
        if row["platform"] == platform
        and row.get("creator_id") == creator_id
        and row["content_id"] != current_id
    ]


def _creator_outlier_ratio(history: list[float], current: float) -> float | None:
    if len(history) < CREATOR_BASELINE_MIN_HISTORY:
        return None
    baseline = statistics.median(history)
    if baseline <= 0:
        return None
    return round(current / baseline, 3)


def _creator_score(ratio: float | None) -> float | None:
    if ratio is None or ratio <= 0:
        return None
    return round(max(0.0, min(100.0, 50.0 + 25.0 * math.log2(ratio))), 2)


def get_signals_for_keys(
    keys: Iterable[tuple[str, str]],
    conn: sqlite3.Connection | None = None,
) -> dict[tuple[str, str], dict]:
    own_conn = conn is None
    conn = conn or get_conn()
    wanted = {(str(p).lower(), str(cid)) for p, cid in keys}
    if not wanted:
        if own_conn:
            conn.close()
        return {}

    try:
        rows = _latest_rows(conn)
        by_platform: dict[str, list[dict]] = {}
        for row in rows:
            by_platform.setdefault(row["platform"], []).append(row)

        engagement_pct: dict[tuple[str, str], float] = {}
        freshness_pct: dict[tuple[str, str], float] = {}
        velocity_pct: dict[tuple[str, str], float] = {}
        velocity_raw: dict[tuple[str, str], float | None] = {}
        now_ts = datetime.now(timezone.utc).timestamp()

        for platform, prows in by_platform.items():
            engagement_values = [math.log1p(_engagement_total(row)) for row in prows]
            fresh_pairs = [(row, _freshness_raw(row.get("publish_time"), now_ts)) for row in prows]
            fresh_values = [value for _, value in fresh_pairs if value is not None]

            velocity_pairs = []
            for row in prows:
                key = (platform, row["content_id"])
                raw = _velocity_per_hour(_snapshots(conn, *key))
                velocity_raw[key] = raw
                if raw is not None:
                    velocity_pairs.append((key, math.log1p(raw)))
            velocity_values = [value for _, value in velocity_pairs]

            for row, value in zip(prows, engagement_values):
                engagement_pct[(platform, row["content_id"])] = _percentile(engagement_values, value)
            for row, value in fresh_pairs:
                if value is not None:
                    freshness_pct[(platform, row["content_id"])] = _percentile(fresh_values, value)
            for key, value in velocity_pairs:
                velocity_pct[key] = _percentile(velocity_values, value)

        signals: dict[tuple[str, str], dict] = {}
        for row in rows:
            key = (row["platform"], row["content_id"])
            if key not in wanted:
                continue

            current_engagement = _engagement_total(row)
            history = _creator_history_values(
                rows, row["platform"], row.get("creator_id"), row["content_id"]
            )
            creator_ratio = _creator_outlier_ratio(history, current_engagement)
            available = {
                name: score
                for name, score in {
                    "freshness": freshness_pct.get(key),
                    "engagement": engagement_pct.get(key),
                    "velocity": velocity_pct.get(key),
                    "creator": _creator_score(creator_ratio),
                }.items()
                if score is not None
            }
            weight_total = sum(WEIGHTS[name] for name in available)
            radar_score = (
                sum(available[name] * WEIGHTS[name] for name in available) / weight_total
                if weight_total
                else 0.0
            )
            signals[key] = {
                "radar_score": round(radar_score, 2),
                "radar_confidence": round(weight_total / sum(WEIGHTS.values()), 2),
                "radar_velocity_per_hour": velocity_raw.get(key),
                "radar_creator_outlier": creator_ratio,
                "radar_snapshot_count": int(row.get("snapshot_count") or 0),
            }
        return signals
    finally:
        if own_conn:
            conn.close()


def enrich_scout_payload(rows: list[dict]) -> list[dict]:
    keys = [
        (str(row.get("platform") or "").lower(), str(row.get("video_id") or ""))
        for row in rows
        if str(row.get("platform") or "").lower() in SUPPORTED_PLATFORMS
        and row.get("video_id")
    ]
    signals = get_signals_for_keys(keys)
    for row in rows:
        key = (str(row.get("platform") or "").lower(), str(row.get("video_id") or ""))
        if key in signals:
            row.update(signals[key])
    return rows


def observe_and_enrich_scout_rows(rows: list[dict], niche: str) -> list[dict]:
    observe_scout_rows(rows, niche)
    return enrich_scout_payload(rows)
