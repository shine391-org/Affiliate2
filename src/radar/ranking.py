"""
ranking.py — Candidate Ranking V0 (Phase 2.2)

Deterministic, explainable scoring from radar.db content + metric_snapshots.

Scoring pipeline (per platform first, then merge):
  1. Freshness   — exponential decay from publish_time
  2. Engagement  — log-normalized within platform, then percentile
  3. Velocity    — delta metrics / delta time, only when interval >= 30min
  4. Creator     — z-score vs creator baseline, only when creator has >=5 content

Platform normalization:
  All sub-scores converted to 0-100 percentile rank within platform,
  then merged. This avoids comparing raw Douyin likes vs Bilibili views.

Missing signals:
  Marked unavailable (null), not 0. Remaining signals are reweighted.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .db import get_conn

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Freshness decay: half-life in seconds (7 days)
FRESHNESS_HALF_LIFE = 7 * 24 * 3600

# Minimum time gap (seconds) between two snapshots to compute velocity
VELOCITY_MIN_INTERVAL_S = 30 * 60  # 30 minutes

# Minimum content count per creator to compute creator baseline
CREATOR_BASELINE_MIN_CONTENT = 5

# Default signal weights (reweighted when signals missing)
WEIGHT_FRESHNESS = 0.30
WEIGHT_ENGAGEMENT = 0.50
WEIGHT_VELOCITY = 0.15
WEIGHT_CREATOR = 0.05


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CandidateScore:
    """One scored candidate for ranking output."""
    platform: str
    content_id: str
    title: str
    creator_name: str
    creator_id: str
    canonical_url: str
    publish_time: Optional[int]
    latest_metrics: dict
    snapshot_count: int
    # Sub-scores (0-100 scale, or None if unavailable)
    freshness_score: Optional[float] = None
    engagement_score: Optional[float] = None
    velocity_score: Optional[float] = None
    creator_score: Optional[float] = None
    # Final
    total_score: float = 0.0
    confidence: float = 0.0
    breakdown: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

def _fetch_content_with_latest_snapshot(conn: sqlite3.Connection) -> list[dict]:
    """
    Fetch each content row joined with its latest snapshot.
    Returns list of dicts with all needed fields.
    """
    query = """
        SELECT
            c.id AS cid,
            c.platform,
            c.content_id,
            c.canonical_url,
            c.creator_id,
            c.creator_name,
            c.title,
            c.publish_time,
            s.views,
            s.likes,
            s.comments,
            s.shares,
            s.favorites,
            s.captured_at,
            (SELECT COUNT(*) FROM metric_snapshots ms WHERE ms.content_id = c.content_id) AS snap_count
        FROM content c
        JOIN metric_snapshots s ON s.platform = c.platform AND s.content_id = c.content_id
        WHERE s.captured_at = (
            SELECT MAX(ms2.captured_at)
            FROM metric_snapshots ms2
            WHERE ms2.platform = c.platform AND ms2.content_id = c.content_id
        )
        ORDER BY c.id
    """
    rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def _fetch_snapshots_for_content(
    conn: sqlite3.Connection, platform: str, content_id: str
) -> list[dict]:
    """Fetch all snapshots for a content, ordered by captured_at."""
    rows = conn.execute(
        """SELECT captured_at, views, likes, comments, shares, favorites
           FROM metric_snapshots
           WHERE platform = ? AND content_id = ?
           ORDER BY captured_at ASC""",
        (platform, content_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_creator_content_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Fetch content count per creator_id across all platforms."""
    rows = conn.execute(
        "SELECT creator_id, COUNT(*) as cnt FROM content GROUP BY creator_id"
    ).fetchall()
    return {r["creator_id"]: r["cnt"] for r in rows if r["creator_id"]}


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------


def score_freshness(publish_time: Optional[int], now_ts: float) -> Optional[float]:
    """
    Exponential decay: score = 100 * 0.5^(age / half_life).
    Returns 0-100, or None if publish_time is missing.
    """
    if publish_time is None:
        return None
    age_s = now_ts - publish_time
    if age_s < 0:
        age_s = 0
    score = 100.0 * (0.5 ** (age_s / FRESHNESS_HALF_LIFE))
    return round(score, 2)


def _engagement_value(row: dict) -> float:
    """
    Raw engagement proxy (before normalization).
    Douyin: likes + comments + shares (no views available)
    Bilibili: views + likes + comments
    Uses log1p to prevent mega-hits from dominating.
    """
    platform = row["platform"]
    if platform == "douyin":
        likes = row.get("likes") or 0
        comments = row.get("comments") or 0
        shares = row.get("shares") or 0
        return math.log1p(likes + comments + shares)
    else:  # bilibili
        views = row.get("views") or 0
        likes = row.get("likes") or 0
        comments = row.get("comments") or 0
        return math.log1p(views + likes + comments)


def percentile_rank(values: list[float], target: float) -> float:
    """
    Percentile rank of `target` within `values` (0-100).
    Uses linear interpolation for ties.
    """
    n = len(values)
    if n == 0:
        return 50.0
    if n == 1:
        return 50.0
    # Count strictly below + equal
    below = sum(1 for v in values if v < target)
    equal = sum(1 for v in values if v == target)
    # Percentile = (below + 0.5 * equal) / n * 100
    pct = (below + 0.5 * equal) / n * 100.0
    return round(pct, 2)


def score_velocity(
    snapshots: list[dict],
) -> Optional[float]:
    """
    Compute velocity as engagement delta / time delta.
    Only valid when oldest and newest snapshot are >= VELOCITY_MIN_INTERVAL_S apart.
    Returns log-scaled engagement growth rate, or None if unavailable.
    """
    if len(snapshots) < 2:
        return None

    first = snapshots[0]
    last = snapshots[-1]

    t1 = datetime.fromisoformat(first["captured_at"].replace("Z", "+00:00"))
    t2 = datetime.fromisoformat(last["captured_at"].replace("Z", "+00:00"))
    elapsed_s = (t2 - t1).total_seconds()

    if elapsed_s < VELOCITY_MIN_INTERVAL_S:
        return None

    # Engagement proxy at first and last snapshot
    def eng(s):
        views = s.get("views") or 0
        likes = s.get("likes") or 0
        comments = s.get("comments") or 0
        shares = s.get("shares") or 0
        return views + likes + comments + shares

    e1 = eng(first)
    e2 = eng(last)

    if e1 <= 0:
        # Can't compute growth rate from zero base; use absolute delta
        delta = e2 - e1
        if delta <= 0:
            return 0.0
        return math.log1p(delta) / elapsed_s * 3600  # per hour

    growth_rate = (e2 - e1) / e1 / elapsed_s * 3600  # per hour
    # Clamp and log-scale
    if growth_rate <= 0:
        return 0.0
    return math.log1p(growth_rate)


def score_creator_baseline(
    creator_id: str,
    content_id: str,
    creator_counts: dict[str, int],
    creator_avg_engagement: dict[str, float],
    all_engagement_values: list[float],
) -> Optional[float]:
    """
    Compute creator outlier score.
    Only when creator has >= CREATOR_BASELINE_MIN_CONTENT content.
    Uses percentile of creator's average engagement vs all creators.
    """
    if creator_id not in creator_counts:
        return None
    if creator_counts[creator_id] < CREATOR_BASELINE_MIN_CONTENT:
        return None
    if creator_id not in creator_avg_engagement:
        return None
    avg_eng = creator_avg_engagement[creator_id]
    return percentile_rank(all_engagement_values, avg_eng)


# ---------------------------------------------------------------------------
# Main ranking pipeline
# ---------------------------------------------------------------------------


def rank_candidates(
    conn: Optional[sqlite3.Connection] = None,
    platform_filter: Optional[str] = None,
) -> list[CandidateScore]:
    """
    Score and rank all candidates.

    Args:
        conn: SQLite connection (default: get_conn()).
        platform_filter: Optional 'douyin' or 'bilibili' filter.

    Returns:
        Sorted list of CandidateScore (highest total_score first).
    """
    if conn is None:
        conn = get_conn()

    now_ts = datetime.now(timezone.utc).timestamp()

    # Fetch data
    rows = _fetch_content_with_latest_snapshot(conn)

    if platform_filter:
        rows = [r for r in rows if r["platform"] == platform_filter]

    if not rows:
        return []

    # Group by platform for per-platform normalization
    by_platform: dict[str, list[dict]] = {}
    for r in rows:
        by_platform.setdefault(r["platform"], []).append(r)

    # Compute per-platform engagement percentiles
    platform_engagement: dict[str, dict[str, float]] = {}
    for plat, prows in by_platform.items():
        values = [_engagement_value(r) for r in prows]
        platform_engagement[plat] = {
            r["content_id"]: percentile_rank(values, _engagement_value(r))
            for r in prows
        }

    # Compute per-platform freshness percentiles
    platform_freshness: dict[str, dict[str, float]] = {}
    for plat, prows in by_platform.items():
        fresh_scores = []
        for r in prows:
            fs = score_freshness(r["publish_time"], now_ts)
            if fs is not None:
                fresh_scores.append(fs)
        # Freshness percentile within platform
        if fresh_scores:
            fresh_sorted = sorted(fresh_scores)
            platform_freshness[plat] = {}
            for r in prows:
                fs = score_freshness(r["publish_time"], now_ts)
                if fs is not None:
                    platform_freshness[plat][r["content_id"]] = percentile_rank(
                        fresh_sorted, fs
                    )

    # Compute per-platform velocity percentiles
    platform_velocity: dict[str, dict[str, float]] = {}
    for plat, prows in by_platform.items():
        vel_scores = []
        vel_map = {}
        for r in prows:
            snaps = _fetch_snapshots_for_content(conn, r["platform"], r["content_id"])
            v = score_velocity(snaps)
            vel_map[r["content_id"]] = v
            if v is not None:
                vel_scores.append(v)
        if vel_scores:
            vel_sorted = sorted(vel_scores)
            platform_velocity[plat] = {}
            for r in prows:
                v = vel_map[r["content_id"]]
                if v is not None:
                    platform_velocity[plat][r["content_id"]] = percentile_rank(
                        vel_sorted, v
                    )

    # Creator baseline
    creator_counts = _fetch_creator_content_counts(conn)
    # Average engagement per creator
    creator_eng_sums: dict[str, float] = {}
    creator_eng_cnts: dict[str, int] = {}
    for r in rows:
        cid = r["creator_id"]
        if cid:
            eng = _engagement_value(r)
            creator_eng_sums[cid] = creator_eng_sums.get(cid, 0) + eng
            creator_eng_cnts[cid] = creator_eng_cnts.get(cid, 0) + 1
    creator_avg_engagement = {
        cid: creator_eng_sums[cid] / creator_eng_cnts[cid]
        for cid in creator_eng_sums
    }
    all_avg_eng_values = list(creator_avg_engagement.values())

    # Build CandidateScore objects
    results: list[CandidateScore] = []
    for r in rows:
        plat = r["platform"]
        cid = r["content_id"]

        # Sub-scores (0-100 percentile or None)
        fresh = platform_freshness.get(plat, {}).get(cid)
        eng = platform_engagement.get(plat, {}).get(cid)
        vel = platform_velocity.get(plat, {}).get(cid)
        cre = score_creator_baseline(
            r["creator_id"],
            cid,
            creator_counts,
            creator_avg_engagement,
            all_avg_eng_values,
        )

        # Compute total score with available signals
        available_weights = {}
        if fresh is not None:
            available_weights["freshness"] = WEIGHT_FRESHNESS
        if eng is not None:
            available_weights["engagement"] = WEIGHT_ENGAGEMENT
        if vel is not None:
            available_weights["velocity"] = WEIGHT_VELOCITY
        if cre is not None:
            available_weights["creator"] = WEIGHT_CREATOR

        total_weight = sum(available_weights.values()) if available_weights else 1.0
        total_score = 0.0
        if fresh is not None:
            total_score += fresh * available_weights["freshness"] / total_weight
        if eng is not None:
            total_score += eng * available_weights["engagement"] / total_weight
        if vel is not None:
            total_score += vel * available_weights["velocity"] / total_weight
        if cre is not None:
            total_score += cre * available_weights["creator"] / total_weight

        # Confidence: fraction of default weight that was available
        confidence = min(total_weight / (
            WEIGHT_FRESHNESS + WEIGHT_ENGAGEMENT + WEIGHT_VELOCITY + WEIGHT_CREATOR
        ), 1.0)

        breakdown = {}
        if fresh is not None:
            breakdown["freshness"] = fresh
        if eng is not None:
            breakdown["engagement"] = eng
        if vel is not None:
            breakdown["velocity"] = vel
        if cre is not None:
            breakdown["creator"] = cre

        cand = CandidateScore(
            platform=plat,
            content_id=cid,
            title=r["title"],
            creator_name=r["creator_name"],
            creator_id=r["creator_id"],
            canonical_url=r["canonical_url"],
            publish_time=r["publish_time"],
            latest_metrics={
                "views": r["views"],
                "likes": r["likes"],
                "comments": r["comments"],
                "shares": r["shares"],
                "favorites": r["favorites"],
            },
            snapshot_count=r["snap_count"],
            freshness_score=fresh,
            engagement_score=eng,
            velocity_score=vel,
            creator_score=cre,
            total_score=round(total_score, 2),
            confidence=round(confidence, 2),
            breakdown=breakdown,
        )
        results.append(cand)

    # Sort by total_score descending (stable sort preserves input order for ties)
    results.sort(key=lambda c: c.total_score, reverse=True)
    return results
