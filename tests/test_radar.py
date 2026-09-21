"""
tests/test_radar.py — Unit tests for Radar Ingestion (Phase 2.1)

Tests:
  1. Douyin normalization — field mapping
  2. Bilibili normalization — field mapping
  3. Content dedupe — second upsert does NOT create duplicate
  4. Snapshot append — second ingest creates new snapshot row
  5. Missing metric → NULL (not 0)
  6. source_run status written correctly
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from radar.db import get_conn
from radar.ingest import ingest, insert_snapshot, upsert_content
from radar.models import MetricSnapshot, NormalizedContent, SourceRunResult
from radar.normalize import normalize_bilibili, normalize_douyin

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CAPTURED_AT = "2026-09-21T01:00:00Z"


@pytest.fixture()
def mem_conn():
    """In-memory SQLite connection for isolated tests."""
    from radar.db import DDL
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. Douyin normalization
# ---------------------------------------------------------------------------

class TestDouyinNormalize:
    RAW = {
        "aweme_id": "7558451761959226665",
        "aweme_url": "https://www.douyin.com/video/7558451761959226665",
        "creator_hash": "abc123",
        "nickname": "TestUser",
        "create_time": 1759839202,
        "title": "Test diet recipe",
        "desc": "Some description",
        "liked_count": "574211",
        "comment_count": "7299",
        "share_count": "233851",
        "collected_count": "226614",
        "source_keyword": "减肥食谱",
    }

    def test_content_id(self):
        c, _ = normalize_douyin(self.RAW, CAPTURED_AT)
        assert c.content_id == "7558451761959226665"

    def test_canonical_url(self):
        c, _ = normalize_douyin(self.RAW, CAPTURED_AT)
        assert "7558451761959226665" in c.canonical_url

    def test_platform(self):
        c, _ = normalize_douyin(self.RAW, CAPTURED_AT)
        assert c.platform == "douyin"

    def test_metrics(self):
        _, s = normalize_douyin(self.RAW, CAPTURED_AT)
        assert s.likes == 574211
        assert s.comments == 7299
        assert s.shares == 233851
        assert s.favorites == 226614

    def test_views_always_none(self):
        """Douyin search API never returns view count — must be NULL."""
        _, s = normalize_douyin(self.RAW, CAPTURED_AT)
        assert s.views is None

    def test_source_keyword(self):
        c, _ = normalize_douyin(self.RAW, CAPTURED_AT)
        assert c.source_keyword == "减肥食谱"


# ---------------------------------------------------------------------------
# 2. Bilibili normalization
# ---------------------------------------------------------------------------

class TestBilibiliNormalize:
    RAW = {
        "video_id": "116227955497963",
        "video_url": "https://www.bilibili.com/video/av116227955497963",
        "user_id": "1815948385",
        "nickname": "马克的技术工作坊",
        "create_time": 1773498176,
        "title": "从 LLM 到 Agent Skill",
        "desc": "Full description here",
        "video_play_count": "1964236",
        "liked_count": "96318",
        "video_danmaku": "2623",
        "collect_count": "169219",
        "source_keyword": "AI",
    }

    def test_platform(self):
        c, _ = normalize_bilibili(self.RAW, CAPTURED_AT)
        assert c.platform == "bilibili"

    def test_content_id(self):
        c, _ = normalize_bilibili(self.RAW, CAPTURED_AT)
        assert c.content_id == "116227955497963"

    def test_views_present(self):
        _, s = normalize_bilibili(self.RAW, CAPTURED_AT)
        assert s.views == 1964236

    def test_likes(self):
        _, s = normalize_bilibili(self.RAW, CAPTURED_AT)
        assert s.likes == 96318

    def test_comments_from_danmaku(self):
        _, s = normalize_bilibili(self.RAW, CAPTURED_AT)
        assert s.comments == 2623

    def test_favorites(self):
        _, s = normalize_bilibili(self.RAW, CAPTURED_AT)
        assert s.favorites == 169219

    def test_source_keyword(self):
        c, _ = normalize_bilibili(self.RAW, CAPTURED_AT)
        assert c.source_keyword == "AI"


# ---------------------------------------------------------------------------
# 3. Content dedupe
# ---------------------------------------------------------------------------

class TestContentDedupe:
    def test_no_duplicate_on_second_upsert(self, mem_conn):
        c = NormalizedContent(
            platform="douyin",
            content_id="TESTID001",
            canonical_url="https://www.douyin.com/video/TESTID001",
            creator_name="User A",
            title="Original title",
        )
        upsert_content(mem_conn, c)
        upsert_content(mem_conn, c)
        mem_conn.commit()

        count = mem_conn.execute(
            "SELECT COUNT(*) FROM content WHERE platform='douyin' AND content_id='TESTID001'"
        ).fetchone()[0]
        assert count == 1

    def test_upsert_returns_new_then_updated(self, mem_conn):
        c = NormalizedContent(platform="bilibili", content_id="BV999", title="Title")
        r1 = upsert_content(mem_conn, c)
        r2 = upsert_content(mem_conn, c)
        assert r1 == "new"
        assert r2 == "updated"

    def test_different_platforms_not_deduped(self, mem_conn):
        c1 = NormalizedContent(platform="douyin", content_id="SHARED001", title="T1")
        c2 = NormalizedContent(platform="bilibili", content_id="SHARED001", title="T2")
        upsert_content(mem_conn, c1)
        upsert_content(mem_conn, c2)
        mem_conn.commit()
        count = mem_conn.execute(
            "SELECT COUNT(*) FROM content WHERE content_id='SHARED001'"
        ).fetchone()[0]
        assert count == 2


# ---------------------------------------------------------------------------
# 4. Snapshot append
# ---------------------------------------------------------------------------

class TestSnapshotAppend:
    def test_second_scan_adds_new_snapshot(self, mem_conn):
        c = NormalizedContent(platform="douyin", content_id="SNAP001", title="T")
        upsert_content(mem_conn, c)

        s1 = MetricSnapshot(
            platform="douyin", content_id="SNAP001",
            captured_at="2026-09-21T01:00:00Z", likes=100,
        )
        s2 = MetricSnapshot(
            platform="douyin", content_id="SNAP001",
            captured_at="2026-09-21T02:00:00Z", likes=200,
        )
        insert_snapshot(mem_conn, s1)
        insert_snapshot(mem_conn, s2)
        mem_conn.commit()

        count = mem_conn.execute(
            "SELECT COUNT(*) FROM metric_snapshots WHERE content_id='SNAP001'"
        ).fetchone()[0]
        assert count == 2


# ---------------------------------------------------------------------------
# 5. NULL metric preservation
# ---------------------------------------------------------------------------

class TestNullMetrics:
    def test_missing_views_is_none_not_zero(self):
        raw = {
            "aweme_id": "NULLTEST",
            "liked_count": "500",
            # no view count field
        }
        _, s = normalize_douyin(raw, CAPTURED_AT)
        assert s.views is None

    def test_missing_shares_is_none(self):
        raw = {"aweme_id": "NULLTEST2", "liked_count": "100"}
        _, s = normalize_douyin(raw, CAPTURED_AT)
        assert s.shares is None

    def test_empty_string_metric_is_none(self):
        raw = {"aweme_id": "NULLTEST3", "liked_count": "", "comment_count": "None"}
        _, s = normalize_douyin(raw, CAPTURED_AT)
        assert s.likes is None
        assert s.comments is None

    def test_null_stored_in_db(self, mem_conn):
        s = MetricSnapshot(
            platform="douyin", content_id="NULLDB",
            captured_at=CAPTURED_AT,
            views=None, likes=100, comments=None,
        )
        c = NormalizedContent(platform="douyin", content_id="NULLDB", title="T")
        upsert_content(mem_conn, c)
        insert_snapshot(mem_conn, s)
        mem_conn.commit()

        row = mem_conn.execute(
            "SELECT views, likes, comments FROM metric_snapshots WHERE content_id='NULLDB'"
        ).fetchone()
        assert row["views"] is None
        assert row["likes"] == 100
        assert row["comments"] is None


# ---------------------------------------------------------------------------
# 6. source_run status
# ---------------------------------------------------------------------------

class TestSourceRunStatus:
    def _make_result(self, status: str) -> SourceRunResult:
        return SourceRunResult(
            platform="douyin",
            keyword="test",
            status=status,
            records_raw=5,
            records_normalized=5,
        )

    def test_success_status_written(self, mem_conn, monkeypatch):
        from radar import ingest as ingest_module
        monkeypatch.setattr(ingest_module, "get_conn", lambda: mem_conn)

        result = SourceRunResult(
            platform="douyin", keyword="kw1", status="SUCCESS",
            records_raw=3, records_normalized=3,
            contents=[NormalizedContent(platform="douyin", content_id=f"ID{i}", title="T") for i in range(3)],
            snapshots=[MetricSnapshot(platform="douyin", content_id=f"ID{i}", captured_at=CAPTURED_AT) for i in range(3)],
        )
        ingest_module.ingest(result)

        row = mem_conn.execute(
            "SELECT status, records_new FROM source_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["status"] == "SUCCESS"
        assert row["records_new"] == 3

    def test_error_status_written(self, mem_conn, monkeypatch):
        from radar import ingest as ingest_module
        monkeypatch.setattr(ingest_module, "get_conn", lambda: mem_conn)

        result = SourceRunResult(
            platform="douyin", keyword="kw2", status="ERROR",
            error="Some error",
        )
        ingest_module.ingest(result)

        row = mem_conn.execute(
            "SELECT status, error FROM source_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["status"] == "ERROR"
        assert "Some error" in row["error"]
