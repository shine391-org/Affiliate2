"""Focused tests for Scout payload enrichment from the transitional Radar DB."""
from __future__ import annotations

import sqlite3

from backend.affiliate.radar.service import enrich_scout_payload, observe_scout_rows
from backend.affiliate.radar.store import DDL


def test_enrichment_keeps_non_native_rows_untouched(monkeypatch):
    rows = [{"platform": "youtube", "video_id": "yt1", "virality_score": 50}]
    assert enrich_scout_payload(rows) == rows


def test_enrichment_adds_radar_fields(monkeypatch):
    # Exercise the service against an in-memory DB by patching its connection factory.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)

    row = {
        "platform": "bilibili",
        "video_id": "b1",
        "video_url": "https://www.bilibili.com/video/av1",
        "creator_id": "c1",
        "author": "Creator",
        "title": "Test",
        "views": 100,
        "likes": 10,
        "comments": 1,
        "shares": 0,
        "favorites": 0,
    }
    observe_scout_rows([row], "test", conn)

    from backend.affiliate.radar import service
    monkeypatch.setattr(service, "get_conn", lambda: conn)

    payload = [{"platform": "bilibili", "video_id": "b1"}]
    enriched = enrich_scout_payload(payload)
    assert enriched[0]["radar_score"] is not None
    assert enriched[0]["radar_snapshot_count"] == 1
