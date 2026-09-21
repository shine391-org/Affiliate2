"""
test_ranking.py — Tests for ranking.py (Phase 2.2)

Tests:
- deterministic score
- missing views Douyin
- missing publish_time
- platform normalization
- no divide-by-zero
- velocity with valid interval
- velocity unavailable with too-short interval
- creator baseline >=5
- creator baseline unavailable <5
- ranking stable
- score breakdown sums/reweights correctly
"""
from __future__ import annotations

import math
import sqlite3
import tempfile
import os
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from radar.ranking import (
    CandidateScore,
    CREATOR_BASELINE_MIN_CONTENT,
    VELOCITY_MIN_INTERVAL_S,
    WEIGHT_CREATOR,
    WEIGHT_ENGAGEMENT,
    WEIGHT_FRESHNESS,
    WEIGHT_VELOCITY,
    _engagement_value,
    percentile_rank,
    rank_candidates,
    score_creator_baseline,
    score_freshness,
    score_velocity,
)
from radar.db import get_conn, DDL


# ---------------------------------------------------------------------------
# Helpers: in-memory test DB
# ---------------------------------------------------------------------------

def make_test_conn() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with radar schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    return conn


def insert_content(
    conn: sqlite3.Connection,
    platform: str,
    content_id: str,
    title: str = "Test",
    creator_id: str = "creator1",
    creator_name: str = "Test Creator",
    publish_time: int | None = 1700000000,
    source_keyword: str = "test",
) -> None:
    conn.execute(
        """INSERT INTO content
           (platform, content_id, creator_id, creator_name, title,
            publish_time, source_keyword, first_seen_at, last_seen_at,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (platform, content_id, creator_id, creator_name, title,
         publish_time, source_keyword,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )


def insert_snapshot(
    conn: sqlite3.Connection,
    platform: str,
    content_id: str,
    captured_at: str,
    views: int | None = None,
    likes: int | None = None,
    comments: int | None = None,
    shares: int | None = None,
    favorites: int | None = None,
) -> None:
    conn.execute(
        """INSERT INTO metric_snapshots
           (platform, content_id, captured_at, views, likes, comments, shares, favorites)
           VALUES (?,?,?,?,?,?,?,?)""",
        (platform, content_id, captured_at, views, likes, comments, shares, favorites),
    )


# ---------------------------------------------------------------------------
# Tests: score_freshness
# ---------------------------------------------------------------------------

def test_freshness_decay():
    now = 1700000000.0
    # Just published
    score = score_freshness(1700000000, now)
    assert score == 100.0
    # After 1 week (half-life), score should be 50
    score = score_freshness(1700000000 - 7 * 24 * 3600, now)
    assert abs(score - 50.0) < 0.01
    # After 2 weeks, score should be 25
    score = score_freshness(1700000000 - 14 * 24 * 3600, now)
    assert abs(score - 25.0) < 0.01


def test_freshness_missing():
    score = score_freshness(None, 1700000000.0)
    assert score is None


def test_future_publish_time():
    # Future publish time should clamp to 0 age → score 100
    now = 1700000000.0
    score = score_freshness(1700000000 + 3600, now)
    assert score == 100.0


# ---------------------------------------------------------------------------
# Tests: percentile_rank
# ---------------------------------------------------------------------------

def test_percentile_basic():
    values = [1, 2, 3, 4, 5]
    assert percentile_rank(values, 1) == 10.0  # (0 + 0.5) / 5 * 100
    assert percentile_rank(values, 3) == 50.0  # (2 + 0.5) / 5 * 100
    assert percentile_rank(values, 5) == 90.0  # (4 + 0.5) / 5 * 100


def test_percentile_single():
    assert percentile_rank([42], 42) == 50.0


def test_percentile_empty():
    assert percentile_rank([], 42) == 50.0


def test_percentile_ties():
    values = [5, 5, 5, 10, 10]
    # 5: below=0, equal=3, pct = (0 + 1.5)/5*100 = 30
    assert percentile_rank(values, 5) == 30.0
    # 10: below=3, equal=2, pct = (3 + 1.0)/5*100 = 80
    assert percentile_rank(values, 10) == 80.0


# ---------------------------------------------------------------------------
# Tests: score_velocity
# ---------------------------------------------------------------------------

def test_velocity_valid_interval():
    # Two snapshots 1 hour apart
    snapshots = [
        {"captured_at": "2026-01-01T00:00:00Z", "views": 100, "likes": 10, "comments": 5, "shares": 0, "favorites": 0},
        {"captured_at": "2026-01-01T01:00:00Z", "views": 200, "likes": 20, "comments": 10, "shares": 0, "favorites": 0},
    ]
    v = score_velocity(snapshots)
    assert v is not None
    assert v > 0


def test_velocity_too_short_interval():
    snapshots = [
        {"captured_at": "2026-01-01T00:00:00Z", "views": 100, "likes": 10, "comments": 5, "shares": 0, "favorites": 0},
        {"captured_at": "2026-01-01T00:10:00Z", "views": 200, "likes": 20, "comments": 10, "shares": 0, "favorites": 0},
    ]
    v = score_velocity(snapshots)
    assert v is None


def test_velocity_single_snapshot():
    snapshots = [
        {"captured_at": "2026-01-01T00:00:00Z", "views": 100, "likes": 10, "comments": 5, "shares": 0, "favorites": 0},
    ]
    v = score_velocity(snapshots)
    assert v is None


def test_velocity_negative_growth():
    snapshots = [
        {"captured_at": "2026-01-01T00:00:00Z", "views": 200, "likes": 20, "comments": 10, "shares": 0, "favorites": 0},
        {"captured_at": "2026-01-01T01:00:00Z", "views": 100, "likes": 10, "comments": 5, "shares": 0, "favorites": 0},
    ]
    v = score_velocity(snapshots)
    assert v == 0.0


def test_velocity_zero_base():
    snapshots = [
        {"captured_at": "2026-01-01T00:00:00Z", "views": 0, "likes": 0, "comments": 0, "shares": 0, "favorites": 0},
        {"captured_at": "2026-01-01T01:00:00Z", "views": 100, "likes": 10, "comments": 5, "shares": 0, "favorites": 0},
    ]
    v = score_velocity(snapshots)
    assert v is not None
    assert v >= 0


# ---------------------------------------------------------------------------
# Tests: score_creator_baseline
# ---------------------------------------------------------------------------

def test_creator_baseline_unavailable_under_threshold():
    creator_counts = {"c1": 3}
    creator_avg_engagement = {"c1": 5.0}
    all_engagement = [5.0, 6.0, 7.0]
    score = score_creator_baseline("c1", "content1", creator_counts, creator_avg_engagement, all_engagement)
    assert score is None


def test_creator_baseline_available():
    creator_counts = {"c1": 6, "c2": 5}
    creator_avg_engagement = {"c1": 8.0, "c2": 3.0}
    all_engagement = [8.0, 3.0]
    score = score_creator_baseline("c1", "content1", creator_counts, creator_avg_engagement, all_engagement)
    assert score is not None
    assert 0 <= score <= 100


def test_creator_baseline_missing_creator():
    score = score_creator_baseline("unknown", "content1", {}, {}, [])
    assert score is None


# ---------------------------------------------------------------------------
# Tests: rank_candidates (integration)
# ---------------------------------------------------------------------------

def test_rank_deterministic():
    """Same input → same output every time."""
    conn = make_test_conn()
    insert_content(conn, "douyin", "dy1", "Video A", publish_time=1700000000)
    insert_snapshot(conn, "douyin", "dy1", "2026-01-01T00:00:00Z", likes=1000, comments=100, shares=50)
    insert_content(conn, "bilibili", "bl1", "Video B", publish_time=1700000000)
    insert_snapshot(conn, "bilibili", "bl1", "2026-01-01T00:00:00Z", views=5000, likes=500, comments=50)

    r1 = rank_candidates(conn)
    r2 = rank_candidates(conn)
    assert len(r1) == len(r2)
    for a, b in zip(r1, r2):
        assert a.content_id == b.content_id
        assert a.total_score == b.total_score


def test_rank_douyin_no_views():
    """Douyin content with views=None should still be ranked."""
    conn = make_test_conn()
    insert_content(conn, "douyin", "dy1", "Douyin Video")
    insert_snapshot(conn, "douyin", "dy1", "2026-01-01T00:00:00Z", likes=10000, comments=500, shares=200)
    conn.commit()

    results = rank_candidates(conn)
    assert len(results) == 1
    assert results[0].platform == "douyin"
    assert results[0].latest_metrics["views"] is None
    assert results[0].total_score > 0


def test_rank_missing_publish_time():
    """Content with publish_time=None should still be ranked (freshness unavailable)."""
    conn = make_test_conn()
    insert_content(conn, "douyin", "dy1", "No Pub Time", publish_time=None)
    insert_snapshot(conn, "douyin", "dy1", "2026-01-01T00:00:00Z", likes=5000, comments=200, shares=100)
    conn.commit()

    results = rank_candidates(conn)
    assert len(results) == 1
    assert results[0].freshness_score is None
    assert results[0].total_score > 0


def test_rank_platform_normalization():
    """Platforms should be normalized separately before merge."""
    conn = make_test_conn()
    # Douyin: very high likes (should be high in douyin percentile)
    insert_content(conn, "douyin", "dy1", "Top Douyin", publish_time=1700000000)
    insert_snapshot(conn, "douyin", "dy1", "2026-01-01T00:00:00Z", likes=100000, comments=5000, shares=2000)
    insert_content(conn, "douyin", "dy2", "Low Douyin", publish_time=1700000000)
    insert_snapshot(conn, "douyin", "dy2", "2026-01-01T00:00:00Z", likes=10, comments=1, shares=0)
    # Bilibili: very high views (should be high in bilibili percentile)
    insert_content(conn, "bilibili", "bl1", "Top Bili", publish_time=1700000000)
    insert_snapshot(conn, "bilibili", "bl1", "2026-01-01T00:00:00Z", views=1000000, likes=50000, comments=5000)
    insert_content(conn, "bilibili", "bl2", "Low Bili", publish_time=1700000000)
    insert_snapshot(conn, "bilibili", "bl2", "2026-01-01T00:00:00Z", views=100, likes=10, comments=1)
    conn.commit()

    results = rank_candidates(conn)
    assert len(results) == 4
    # Top douyin should beat low douyin
    top_dy = next(c for c in results if c.content_id == "dy1")
    low_dy = next(c for c in results if c.content_id == "dy2")
    assert top_dy.total_score > low_dy.total_score
    # Top bilibili should beat low bilibili
    top_bl = next(c for c in results if c.content_id == "bl1")
    low_bl = next(c for c in results if c.content_id == "bl2")
    assert top_bl.total_score > low_bl.total_score


def test_rank_no_divide_by_zero():
    """Zero metrics should not cause division by zero."""
    conn = make_test_conn()
    insert_content(conn, "douyin", "dy1", "Zero")
    insert_snapshot(conn, "douyin", "dy1", "2026-01-01T00:00:00Z", likes=0, comments=0, shares=0)
    conn.commit()

    results = rank_candidates(conn)
    assert len(results) == 1
    assert results[0].total_score >= 0


def test_rank_breakdown_reweights():
    """When signals are missing, total_score is based on available signals only."""
    conn = make_test_conn()
    # Content with engagement only (no velocity, no creator baseline)
    insert_content(conn, "douyin", "dy1", "Test")
    insert_snapshot(conn, "douyin", "dy1", "2026-01-01T00:00:00Z", likes=1000, comments=100, shares=50)
    conn.commit()

    results = rank_candidates(conn)
    c = results[0]
    # Should have freshness + engagement, no velocity, no creator
    assert "freshness" in c.breakdown
    assert "engagement" in c.breakdown
    assert "velocity" not in c.breakdown
    assert "creator" not in c.breakdown
    # Confidence should be (0.3 + 0.5) / 1.0 = 0.8
    assert abs(c.confidence - 0.8) < 0.01


def test_rank_with_valid_velocity():
    """Velocity should be computed when snapshots are far enough apart."""
    conn = make_test_conn()
    insert_content(conn, "bilibili", "bl1", "Velocity Test")
    insert_snapshot(conn, "bilibili", "bl1", "2026-01-01T00:00:00Z", views=100, likes=10, comments=5)
    insert_snapshot(conn, "bilibili", "bl1", "2026-01-01T02:00:00Z", views=1000, likes=100, comments=50)
    conn.commit()

    results = rank_candidates(conn)
    assert len(results) == 1
    assert results[0].velocity_score is not None
    assert results[0].velocity_score > 0
    # Confidence should include velocity now
    assert results[0].confidence > 0.8


def test_rank_empty_db():
    conn = make_test_conn()
    results = rank_candidates(conn)
    assert results == []


def test_rank_platform_filter():
    conn = make_test_conn()
    insert_content(conn, "douyin", "dy1", "Douyin")
    insert_snapshot(conn, "douyin", "dy1", "2026-01-01T00:00:00Z", likes=1000)
    insert_content(conn, "bilibili", "bl1", "Bili")
    insert_snapshot(conn, "bilibili", "bl1", "2026-01-01T00:00:00Z", views=5000)
    conn.commit()

    results = rank_candidates(conn, platform_filter="douyin")
    assert len(results) == 1
    assert results[0].platform == "douyin"


def test_rank_stable_ties():
    """Tied scores should maintain stable ordering (input order)."""
    conn = make_test_conn()
    insert_content(conn, "douyin", "dy1", "A")
    insert_snapshot(conn, "douyin", "dy1", "2026-01-01T00:00:00Z", likes=100, comments=10)
    insert_content(conn, "douyin", "dy2", "B")
    insert_snapshot(conn, "douyin", "dy2", "2026-01-01T00:00:00Z", likes=100, comments=10)
    conn.commit()

    results = rank_candidates(conn)
    assert results[0].content_id == "dy1"
    assert results[1].content_id == "dy2"


# ---------------------------------------------------------------------------
# Tests: CLI integration
# ---------------------------------------------------------------------------

def test_cli_top_terminal():
    """CLI top command should work on real radar.db."""
    # Import CLI formatters from radar.py at project root
    import importlib.util
    spec = importlib.util.spec_from_file_location("radar_cli", str(Path(__file__).resolve().parent.parent / "radar.py"))
    radar_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(radar_cli)

    conn = make_test_conn()
    insert_content(conn, "douyin", "dy1", "Terminal Test")
    insert_snapshot(conn, "douyin", "dy1", "2026-01-01T00:00:00Z", likes=5000)
    conn.commit()
    results = rank_candidates(conn)

    term = radar_cli._format_terminal(results, 20)
    assert "RADAR TOP" in term
    assert "Terminal Test" in term

    js = radar_cli._format_json(results, 20)
    assert '"generated_at"' in js

    md = radar_cli._format_markdown(results, 20)
    assert "# Radar" in md


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_freshness_decay,
        test_freshness_missing,
        test_future_publish_time,
        test_percentile_basic,
        test_percentile_single,
        test_percentile_empty,
        test_percentile_ties,
        test_velocity_valid_interval,
        test_velocity_too_short_interval,
        test_velocity_single_snapshot,
        test_velocity_negative_growth,
        test_velocity_zero_base,
        test_creator_baseline_unavailable_under_threshold,
        test_creator_baseline_available,
        test_creator_baseline_missing_creator,
        test_rank_deterministic,
        test_rank_douyin_no_views,
        test_rank_missing_publish_time,
        test_rank_platform_normalization,
        test_rank_no_divide_by_zero,
        test_rank_breakdown_reweights,
        test_rank_with_valid_velocity,
        test_rank_empty_db,
        test_rank_platform_filter,
        test_rank_stable_ties,
        test_cli_top_terminal,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")

    print(f"\n{passed}/{passed + failed} tests passed")
    sys.exit(0 if failed == 0 else 1)
