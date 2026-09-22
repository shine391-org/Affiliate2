"""Focused tests for the Phase-B app-native Radar bridge."""
from __future__ import annotations

import sqlite3

from backend.affiliate.radar.service import (
    CREATOR_BASELINE_MIN_HISTORY,
    get_signals_for_keys,
    observe_scout_rows,
    record_source_run,
)
from backend.affiliate.radar.store import DDL


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    return conn


def _row(platform, video_id, creator_id="creator", views=100, likes=10):
    return {
        "platform": platform,
        "video_id": video_id,
        "video_url": f"https://example/{platform}/{video_id}",
        "creator_id": creator_id,
        "author": creator_id,
        "title": video_id,
        "views": views,
        "likes": likes,
        "comments": 1,
        "shares": 0,
        "favorites": 0,
        "radar_metrics": {
            "views": views,
            "likes": likes,
            "comments": 1,
            "shares": 0,
            "favorites": 0,
        },
    }


def test_observation_identity_is_platform_scoped():
    conn = _conn()
    observe_scout_rows(
        [_row("douyin", "same"), _row("bilibili", "same")],
        "test",
        conn,
    )
    count = conn.execute(
        "SELECT COUNT(*) FROM content WHERE content_id='same'"
    ).fetchone()[0]
    assert count == 2


def test_snapshot_count_is_platform_scoped():
    conn = _conn()
    observe_scout_rows([_row("douyin", "same")], "one", conn)
    observe_scout_rows([_row("bilibili", "same")], "one", conn)
    observe_scout_rows([_row("douyin", "same", views=200)], "two", conn)

    signals = get_signals_for_keys(
        [("douyin", "same"), ("bilibili", "same")],
        conn,
    )
    assert signals[("douyin", "same")]["radar_snapshot_count"] == 2
    assert signals[("bilibili", "same")]["radar_snapshot_count"] == 1


def test_unknown_metric_remains_null_in_radar_snapshot():
    conn = _conn()
    row = _row("douyin", "nullviews")
    row["views"] = 0
    row["radar_metrics"]["views"] = None
    observe_scout_rows([row], "test", conn)
    stored = conn.execute(
        "SELECT views FROM metric_snapshots WHERE platform='douyin' AND content_id='nullviews'"
    ).fetchone()[0]
    assert stored is None


def test_creator_outlier_uses_same_creator_same_platform_history():
    conn = _conn()
    history = [
        _row("bilibili", f"old{i}", creator_id="creator", views=100, likes=10)
        for i in range(CREATOR_BASELINE_MIN_HISTORY)
    ]
    history += [
        _row("douyin", f"dy{i}", creator_id="creator", views=100000, likes=10000)
        for i in range(3)
    ]
    current = _row("bilibili", "breakout", creator_id="creator", views=1000, likes=100)
    observe_scout_rows(history + [current], "baseline", conn)

    signal = get_signals_for_keys([("bilibili", "breakout")], conn)[
        ("bilibili", "breakout")
    ]
    assert signal["radar_creator_outlier"] is not None
    assert signal["radar_creator_outlier"] > 5


def test_creator_baseline_unavailable_without_enough_other_content():
    conn = _conn()
    rows = [
        _row("bilibili", f"old{i}", creator_id="small")
        for i in range(CREATOR_BASELINE_MIN_HISTORY - 1)
    ]
    rows.append(_row("bilibili", "current", creator_id="small", views=10000))
    observe_scout_rows(rows, "small", conn)
    signal = get_signals_for_keys([("bilibili", "current")], conn)[
        ("bilibili", "current")
    ]
    assert signal["radar_creator_outlier"] is None


def test_provider_non_success_run_is_persisted():
    conn = _conn()
    record_source_run(
        "douyin",
        "健身",
        "AUTH_REQUIRED",
        error="CDP login required",
        conn=conn,
    )
    row = conn.execute(
        "SELECT platform, keyword, status, records_raw, error FROM source_runs"
    ).fetchone()
    assert tuple(row[:4]) == ("douyin", "健身", "AUTH_REQUIRED", 0)
    assert "login required" in row["error"]


def test_velocity_uses_controlled_snapshot_interval_without_real_wait():
    conn = _conn()
    observe_scout_rows([_row("bilibili", "velocity", views=100, likes=10)], "v1", conn)
    observe_scout_rows([_row("bilibili", "velocity", views=300, likes=30)], "v2", conn)

    ids = [
        row["id"] for row in conn.execute(
            "SELECT id FROM metric_snapshots WHERE platform='bilibili' AND content_id='velocity' ORDER BY id"
        ).fetchall()
    ]
    assert len(ids) == 2
    conn.execute(
        "UPDATE metric_snapshots SET captured_at='2026-09-22T00:00:00Z' WHERE id=?",
        (ids[0],),
    )
    conn.execute(
        "UPDATE metric_snapshots SET captured_at='2026-09-22T00:31:00Z' WHERE id=?",
        (ids[1],),
    )
    conn.commit()

    signal = get_signals_for_keys([("bilibili", "velocity")], conn)[("bilibili", "velocity")]
    assert signal["radar_velocity_per_hour"] is not None
    assert signal["radar_velocity_per_hour"] > 0
