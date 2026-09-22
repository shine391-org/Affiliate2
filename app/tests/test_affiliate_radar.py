"""Focused tests for the Phase-B app-native Radar bridge."""
from __future__ import annotations

import math
import sqlite3

from backend.affiliate.radar.service import (
    CREATOR_BASELINE_MIN_HISTORY,
    _engagement_total,
    _legacy_velocity_score,
    _velocity_per_hour,
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

def test_velocity_preserves_legacy_scoring_semantics_exactly():
    positive = [
        {"captured_at": "2026-09-22T00:00:00Z", "views": 100},
        {"captured_at": "2026-09-22T01:00:00Z", "views": 300},
    ]
    assert _velocity_per_hour(positive) == 2.0
    assert _legacy_velocity_score(positive) == math.log1p(2.0)

    zero_baseline = [
        {"captured_at": "2026-09-22T00:00:00Z", "views": 0},
        {"captured_at": "2026-09-22T02:00:00Z", "views": 120},
    ]
    assert _velocity_per_hour(zero_baseline) == 60.0
    assert _legacy_velocity_score(zero_baseline) == math.log1p(120) / 2

    no_growth = [
        {"captured_at": "2026-09-22T00:00:00Z", "views": 100},
        {"captured_at": "2026-09-22T01:00:00Z", "views": 90},
    ]
    assert _velocity_per_hour(no_growth) == 0.0
    assert _legacy_velocity_score(no_growth) == 0.0

    favorites_only = [
        {"captured_at": "2026-09-22T00:00:00Z", "views": 100, "favorites": 0},
        {"captured_at": "2026-09-22T01:00:00Z", "views": 100, "favorites": 99999},
    ]
    assert _velocity_per_hour(favorites_only) == 0.0
    assert _legacy_velocity_score(favorites_only) == 0.0


def test_refined_native_success_records_actual_source_keyword_per_platform():
    conn = _conn()
    record_source_run("bilibili", "fitness", "EMPTY", conn=conn)

    bili = _row("bilibili", "refined")
    bili["source_keyword"] = "home workout women"
    douyin = _row("douyin", "refined")
    douyin["source_keyword"] = "居家健身"

    observe_scout_rows([bili, douyin], "fitness", conn)

    runs = [
        tuple(row)
        for row in conn.execute(
            "SELECT platform, keyword, status FROM source_runs ORDER BY id"
        ).fetchall()
    ]
    assert runs == [
        ("bilibili", "fitness", "EMPTY"),
        ("bilibili", "home workout women", "SUCCESS"),
        ("douyin", "居家健身", "SUCCESS"),
    ]

def test_engagement_metric_sets_match_legacy_platform_rules():
    douyin = {
        "platform": "douyin",
        "views": 1_000_000,
        "likes": 10,
        "comments": 2,
        "shares": 3,
        "favorites": 500_000,
    }
    bilibili = {
        "platform": "bilibili",
        "views": 100,
        "likes": 10,
        "comments": 2,
        "shares": 999,
        "favorites": 999,
    }
    assert _engagement_total(douyin) == 15
    assert _engagement_total(bilibili) == 112


def test_velocity_ranking_uses_legacy_score_not_log_of_hourly_delta(monkeypatch):
    from backend.affiliate.radar import service

    conn = _conn()
    a = _row("bilibili", "slow-big", views=0, likes=0)
    b = _row("bilibili", "fast-small", views=0, likes=0)
    observe_scout_rows([a, b], "seed", conn)
    observe_scout_rows([
        _row("bilibili", "slow-big", views=1000, likes=0),
        _row("bilibili", "fast-small", views=20, likes=0),
    ], "later", conn)

    ids = {
        row["content_id"]: [x["id"] for x in conn.execute(
            "SELECT id FROM metric_snapshots WHERE platform='bilibili' AND content_id=? ORDER BY id",
            (row["content_id"],),
        ).fetchall()]
        for row in [{"content_id": "slow-big"}, {"content_id": "fast-small"}]
    }
    conn.execute(
        "UPDATE metric_snapshots SET captured_at='2026-09-22T00:00:00Z' WHERE id=?",
        (ids["slow-big"][0],),
    )
    conn.execute(
        "UPDATE metric_snapshots SET captured_at='2026-09-22T10:00:00Z' WHERE id=?",
        (ids["slow-big"][1],),
    )
    conn.execute(
        "UPDATE metric_snapshots SET captured_at='2026-09-22T00:00:00Z' WHERE id=?",
        (ids["fast-small"][0],),
    )
    conn.execute(
        "UPDATE metric_snapshots SET captured_at='2026-09-22T00:30:00Z' WHERE id=?",
        (ids["fast-small"][1],),
    )
    conn.commit()

    monkeypatch.setattr(
        service,
        "WEIGHTS",
        {"freshness": 0.0, "engagement": 0.0, "velocity": 1.0, "creator": 0.0},
    )
    signals = get_signals_for_keys(
        [("bilibili", "slow-big"), ("bilibili", "fast-small")],
        conn,
    )
    assert signals[("bilibili", "fast-small")]["radar_score"] > signals[("bilibili", "slow-big")]["radar_score"]


def test_douyin_engagement_ranking_ignores_views_and_favorites(monkeypatch):
    from backend.affiliate.radar import service

    conn = _conn()
    low = _row("douyin", "high-views-low-eng", views=1_000_000, likes=1)
    low["favorites"] = 1_000_000
    low["radar_metrics"]["favorites"] = 1_000_000
    high = _row("douyin", "low-views-high-eng", views=10, likes=100)
    observe_scout_rows([low, high], "eng", conn)

    monkeypatch.setattr(
        service,
        "WEIGHTS",
        {"freshness": 0.0, "engagement": 1.0, "velocity": 0.0, "creator": 0.0},
    )
    signals = get_signals_for_keys(
        [("douyin", "high-views-low-eng"), ("douyin", "low-views-high-eng")],
        conn,
    )
    assert signals[("douyin", "low-views-high-eng")]["radar_score"] > signals[("douyin", "high-views-low-eng")]["radar_score"]


def test_content_source_keyword_preserves_first_discovery_provenance():
    conn = _conn()
    first = _row("bilibili", "provenance")
    first["source_keyword"] = "first query"
    observe_scout_rows([first], "fallback", conn)

    second = _row("bilibili", "provenance", views=200)
    second["source_keyword"] = "later query"
    observe_scout_rows([second], "fallback", conn)

    content_keyword = conn.execute(
        "SELECT source_keyword FROM content WHERE platform='bilibili' AND content_id='provenance'"
    ).fetchone()[0]
    run_keywords = [
        row[0]
        for row in conn.execute(
            "SELECT keyword FROM source_runs WHERE platform='bilibili' ORDER BY id"
        ).fetchall()
    ]
    assert content_keyword == "first query"
    assert run_keywords == ["first query", "later query"]

