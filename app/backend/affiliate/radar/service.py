"""Affiliate2 Radar observations + deterministic longitudinal signals."""
from __future__ import annotations

import math
import sqlite3
import statistics
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

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
    """Legacy Radar engagement proxy before log normalization."""
    platform = str(row.get("platform") or "").lower()
    if platform == "douyin":
        return float(
            (row.get("likes") or 0)
            + (row.get("comments") or 0)
            + (row.get("shares") or 0)
        )
    # Legacy Bilibili ranking: views + likes + comments.
    return float(
        (row.get("views") or 0)
        + (row.get("likes") or 0)
        + (row.get("comments") or 0)
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
    """Legacy velocity metric set: views + likes + comments + shares."""
    return float(
        (row.get("views") or 0)
        + (row.get("likes") or 0)
        + (row.get("comments") or 0)
        + (row.get("shares") or 0)
    )


def _velocity_per_hour(snapshots: list[dict]) -> float | None:
    """Human-readable growth/hour used in the Scout payload."""
    if len(snapshots) < 2:
        return None
    first, last = snapshots[0], snapshots[-1]
    t1 = datetime.fromisoformat(first["captured_at"].replace("Z", "+00:00"))
    t2 = datetime.fromisoformat(last["captured_at"].replace("Z", "+00:00"))
    elapsed = (t2 - t1).total_seconds()
    if elapsed < VELOCITY_MIN_INTERVAL_SECONDS:
        return None

    first_engagement = _snapshot_engagement(first)
    last_engagement = _snapshot_engagement(last)
    delta = max(last_engagement - first_engagement, 0.0)
    elapsed_hours = elapsed / 3600.0
    if first_engagement > 0:
        return round((delta / first_engagement) / elapsed_hours, 6)
    return round(delta / elapsed_hours, 3)


def _legacy_velocity_score(snapshots: list[dict]) -> float | None:
    """Exact legacy Radar velocity signal used for percentile ranking."""
    if len(snapshots) < 2:
        return None
    first, last = snapshots[0], snapshots[-1]
    t1 = datetime.fromisoformat(first["captured_at"].replace("Z", "+00:00"))
    t2 = datetime.fromisoformat(last["captured_at"].replace("Z", "+00:00"))
    elapsed = (t2 - t1).total_seconds()
    if elapsed < VELOCITY_MIN_INTERVAL_SECONDS:
        return None

    e1 = _snapshot_engagement(first)
    e2 = _snapshot_engagement(last)
    if e1 <= 0:
        delta = e2 - e1
        if delta <= 0:
            return 0.0
        return math.log1p(delta) / elapsed * 3600.0

    growth_rate = (e2 - e1) / e1 / elapsed * 3600.0
    if growth_rate <= 0:
        return 0.0
    return math.log1p(growth_rate)


def record_source_run(
    platform: str,
    keyword: str,
    status: str,
    *,
    records_raw: int = 0,
    error: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Persist provider EMPTY/AUTH_REQUIRED/ERROR in the application DB."""
    own_conn = conn is None
    conn = conn or get_conn()
    now = _now_iso()
    try:
        conn.execute(
            """INSERT INTO radar_source_runs
            (id, legacy_source_run_id, platform, keyword, started_at, finished_at, status,
             records_raw, records_normalized, records_new, records_updated, error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid4()),
                None,
                str(platform).lower(),
                keyword,
                now,
                now,
                status,
                max(int(records_raw or 0), 0),
                0,
                0,
                0,
                (error or "")[:1000] or None,
            ),
        )
        conn.commit()
    finally:
        if own_conn:
            conn.close()


def observe_scout_rows(
    rows: list[dict],
    niche: str,
    conn: sqlite3.Connection | None = None,
    *,
    job_id: str | None = None,
) -> dict:
    """Upsert the current Scout projection and append immutable observations."""
    own_conn = conn is None
    conn = conn or get_conn()
    captured_at = _now_iso()
    seen: set[tuple[str, str]] = set()
    per_source: dict[tuple[str, str], dict[str, int]] = {}

    try:
        for row in rows:
            platform = str(row.get("platform") or "").lower()
            content_id = str(row.get("video_id") or "").strip()
            key = (platform, content_id)
            if platform not in SUPPORTED_PLATFORMS or not content_id or key in seen:
                continue
            seen.add(key)
            source_keyword = str(row.get("source_keyword") or niche).strip() or niche
            stats = per_source.setdefault(
                (platform, source_keyword),
                {"observed": 0, "new": 0, "updated": 0},
            )

            existing = conn.execute(
                """SELECT id FROM scout_results
                WHERE user_id='local' AND platform=? AND video_id=?""",
                key,
            ).fetchone()
            values = (
                row.get("video_url") or f"legacy://{platform}/{content_id}",
                row.get("creator_id"),
                row.get("author"),
                row.get("title"),
                row.get("description"),
                _publish_timestamp(row.get("upload_date")),
                source_keyword,
            )
            if existing is None:
                scout_id = str(uuid4())
                conn.execute(
                    """INSERT INTO scout_results
                    (id,user_id,job_id,platform,video_id,video_url,creator_id,author,title,
                     description,upload_date,niche,views,likes,comments,shares,
                     is_downloaded,is_analyzed,first_seen_at,last_seen_at,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        scout_id, "local", job_id, platform, content_id, *values,
                        _metric_value(row, "views"), _metric_value(row, "likes"),
                        _metric_value(row, "comments"), _metric_value(row, "shares"),
                        0, 0, captured_at, captured_at, captured_at,
                    ),
                )
                stats["new"] += 1
            else:
                scout_id = existing["id"]
                conn.execute(
                    """UPDATE scout_results SET
                    video_url=COALESCE(?,video_url), creator_id=COALESCE(?,creator_id),
                    author=COALESCE(?,author), title=COALESCE(?,title),
                    description=COALESCE(?,description), upload_date=COALESCE(?,upload_date),
                    views=?, likes=?, comments=?, shares=?, job_id=COALESCE(job_id,?),
                    last_seen_at=?
                    WHERE id=?""",
                    (
                        *values[:-1],
                        _metric_value(row, "views"), _metric_value(row, "likes"),
                        _metric_value(row, "comments"), _metric_value(row, "shares"),
                        job_id, captured_at, scout_id,
                    ),
                )
                stats["updated"] += 1

            conn.execute(
                """INSERT INTO radar_observations
                (id,scout_result_id,legacy_snapshot_id,job_id,captured_at,views,likes,comments,shares,favorites,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid4()),
                    scout_id,
                    None,
                    job_id,
                    captured_at,
                    _metric_value(row, "views"),
                    _metric_value(row, "likes"),
                    _metric_value(row, "comments"),
                    _metric_value(row, "shares"),
                    _metric_value(row, "favorites"),
                    captured_at,
                ),
            )
            stats["observed"] += 1

        for (platform, keyword), stats in per_source.items():
            conn.execute(
                """INSERT INTO radar_source_runs
                (id,legacy_source_run_id,platform, keyword, started_at, finished_at, status,
                 records_raw, records_normalized, records_new, records_updated)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid4()),
                    None,
                    platform,
                    keyword,
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
            "observed": sum(v["observed"] for v in per_source.values()),
            "new": sum(v["new"] for v in per_source.values()),
            "updated": sum(v["updated"] for v in per_source.values()),
            "captured_at": captured_at,
        }
    finally:
        if own_conn:
            conn.close()


def _latest_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT
            c.platform, c.video_id AS content_id, c.video_url AS canonical_url, c.creator_id,
            c.author AS creator_name, c.title,
            CAST(strftime('%s', c.upload_date) AS INTEGER) AS publish_time,
            s.views, s.likes, s.comments, s.shares, s.favorites,
            s.captured_at,
            (
                SELECT COUNT(*) FROM radar_observations ms
                WHERE ms.scout_result_id=c.id
            ) AS snapshot_count
        FROM scout_results c
        JOIN radar_observations s ON s.scout_result_id=c.id
        WHERE c.user_id='local' AND s.rowid = (
            SELECT ms2.rowid FROM radar_observations ms2
            WHERE ms2.scout_result_id=c.id
            ORDER BY ms2.captured_at DESC, ms2.rowid DESC
            LIMIT 1
        )
        ORDER BY c.rowid"""
    ).fetchall()
    return [dict(row) for row in rows]


def _snapshots(conn: sqlite3.Connection, platform: str, content_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT captured_at, views, likes, comments, shares, favorites
        FROM radar_observations
        WHERE scout_result_id=(
            SELECT id FROM scout_results
            WHERE user_id='local' AND platform=? AND video_id=?
            LIMIT 1
        )
        ORDER BY captured_at ASC, rowid ASC""",
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
                snapshots = _snapshots(conn, *key)
                raw = _velocity_per_hour(snapshots)
                score = _legacy_velocity_score(snapshots)
                velocity_raw[key] = raw
                if score is not None:
                    velocity_pairs.append((key, score))
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


def observe_and_enrich_scout_rows(
    rows: list[dict], niche: str, *, job_id: str | None = None
) -> list[dict]:
    observe_scout_rows(rows, niche, job_id=job_id)
    return enrich_scout_payload(rows)
