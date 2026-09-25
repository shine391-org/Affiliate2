"""Phase-G migration safety, parity, and identity semantics."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from backend.migrations.phase_g import PhaseGMigrator, inspect_phase_g


def _legacy_schema(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE content (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            content_id TEXT NOT NULL,
            canonical_url TEXT,
            creator_id TEXT,
            creator_name TEXT,
            title TEXT,
            description TEXT,
            publish_time INTEGER,
            source_keyword TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE metric_snapshots (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            content_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            favorites INTEGER
        );
        CREATE TABLE source_runs (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            keyword TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            records_raw INTEGER,
            records_normalized INTEGER,
            records_new INTEGER,
            records_updated INTEGER,
            error TEXT
        );
        CREATE TABLE content_reviews (
            id INTEGER PRIMARY KEY,
            platform TEXT NOT NULL,
            content_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            note TEXT,
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE content_actions (
            id INTEGER PRIMARY KEY,
            job_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            content_id TEXT NOT NULL,
            action TEXT NOT NULL,
            params_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE content_publications (
            id INTEGER PRIMARY KEY,
            source_platform TEXT NOT NULL,
            content_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            job_id TEXT,
            status TEXT NOT NULL,
            title TEXT,
            caption TEXT,
            privacy TEXT,
            external_id TEXT,
            external_url TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT
        );
        CREATE TABLE publication_metrics (
            id INTEGER PRIMARY KEY,
            publication_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            collected_at TEXT NOT NULL,
            note TEXT,
            raw_json TEXT
        );
        CREATE TABLE publish_accounts (
            platform TEXT PRIMARY KEY,
            credentials_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE publish_oauth_states (
            state TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    return conn


def _seed_complete_legacy(root: Path, *, duplicate_content: bool = False) -> tuple[Path, Path]:
    legacy = root / "data" / "radar.db"
    legacy.parent.mkdir(parents=True)
    conn = _legacy_schema(legacy)
    now = "2026-09-22T05:00:00+00:00"
    content_rows = [
        (1, "bilibili", "asset-1", "https://bilibili/asset-1", "creator-1", "Creator", "Legacy title", "Desc", 1_790_000_000, "food", now, now, now, now),
        (2, "douyin", "asset-2", "https://douyin/asset-2", "creator-2", "Creator 2", "Second", None, None, "fitness", now, now, now, now),
    ]
    if duplicate_content:
        content_rows.append((3, "bilibili", "asset-1", "https://duplicate", "creator-x", "Other", "Duplicate", None, None, "other", now, now, now, now))
    conn.executemany("INSERT INTO content VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", content_rows)
    conn.executemany(
        "INSERT INTO metric_snapshots VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (10, "bilibili", "asset-1", "2026-09-22T01:00:00+00:00", 100, 10, 1, None, 4),
            (11, "bilibili", "asset-1", "2026-09-22T02:00:00+00:00", 200, 20, 2, 3, 5),
            (12, "douyin", "asset-2", "2026-09-22T03:00:00+00:00", None, 8, None, 1, None),
        ],
    )
    conn.execute(
        "INSERT INTO source_runs VALUES (20,?,?,?,?,?,?,?,?,?,?)",
        ("douyin", "fitness", now, now, "AUTH_REQUIRED", 0, 0, 0, 0, "login required"),
    )
    conn.executemany(
        "INSERT INTO content_reviews VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (30, "bilibili", "asset-1", "candidate", "APPROVED_FOR_REMAKE", "human candidate decision", now, now, now),
            (31, "bilibili", "asset-1", "remake", "APPROVED", "human final decision", now, now, now),
        ],
    )
    conn.execute(
        "INSERT INTO content_actions VALUES (40,?,?,?,?,?,?)",
        ("missing-legacy-job", "bilibili", "asset-1", "rerender", '{"voice":"vi"}', now),
    )
    conn.executemany(
        "INSERT INTO content_publications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (50, "bilibili", "asset-1", "tiktok", None, "PUBLISHED", "Title", "Caption", None, None, "https://www.tiktok.com/@u/video/7654321098765432100", None, now, now, now),
            (51, "bilibili", "asset-1", "youtube", None, "FAILED", "Bad", "Caption", "private", None, None, "provider failed", now, now, None),
        ],
    )
    conn.execute(
        "INSERT INTO publication_metrics VALUES (60,?,?,?,?,?,?,?,?,?)",
        (50, "manual", 123, 9, 2, None, now, "kept note", None),
    )
    conn.commit()
    conn.close()

    candidate = root / "data" / "candidates" / "bilibili_asset-1"
    candidate.mkdir(parents=True)
    (candidate / "source.mp4").write_bytes(b"source-media")
    (candidate / "metadata.json").write_text(json.dumps({"title": "Candidate", "duration": 12}), encoding="utf-8")
    (candidate / "transcript.json").write_text(
        json.dumps({"language": "zh", "text": "hello", "segments": [{"start": 0, "end": 1, "text": "hello"}]}),
        encoding="utf-8",
    )
    (candidate / "intelligence.json").write_text(json.dumps({"hook": "raw intelligence"}), encoding="utf-8")

    remake = root / "data" / "remakes" / "bilibili_asset-1"
    remake.mkdir(parents=True)
    (remake / "final.mp4").write_bytes(b"legacy-remake-bytes")

    incomplete = root / "data" / "candidates" / "bilibili_incomplete"
    incomplete.mkdir(parents=True)
    (incomplete / "metadata.json").write_text('{"status":"failed"}', encoding="utf-8")
    return legacy, remake / "final.mp4"


def _rows(path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_legacy_only_migration_preserves_relationships_nulls_and_identifier_semantics(tmp_path):
    legacy, remake_path = _seed_complete_legacy(tmp_path)
    canonical = tmp_path / "viralmint.db"

    report = PhaseGMigrator(canonical, legacy, tmp_path).run()

    assert report["status"] == "completed"
    assert report["counts"]["legacy_content"] == 2
    assert report["counts"]["migrated_observations"] == 3
    assert report["counts"]["migrated_reviews"] == 2
    assert report["counts"]["migrated_publications"] == 2
    assert report["counts"]["migrated_metrics"] == 1
    assert report["orphans"] == 0
    assert report["skipped"] == 1
    assert report["conflicts"] == 0

    scout = _rows(canonical, "SELECT * FROM scout_results WHERE platform='bilibili' AND video_id='asset-1'")[0]
    downloaded = _rows(canonical, "SELECT * FROM downloaded_videos WHERE scout_result_id=?", (scout["id"],))[0]
    generated = _rows(canonical, "SELECT * FROM generated_videos WHERE source_scout_result_id=? AND source_type='legacy_remake'", (scout["id"],))[0]
    assert downloaded["source_sha256"] == hashlib.sha256(b"source-media").hexdigest()
    expected_remake_hash = hashlib.sha256(remake_path.read_bytes()).hexdigest()
    assert generated["artifact_sha256"] == expected_remake_hash
    assert generated["publish_approval_artifact_sha256"] == expected_remake_hash
    assert generated["publish_approved_at"] is not None

    null_observation = _rows(canonical, "SELECT * FROM radar_observations WHERE legacy_snapshot_id=10")[0]
    assert null_observation["shares"] is None
    unknown_views = _rows(canonical, "SELECT * FROM radar_observations WHERE legacy_snapshot_id=12")[0]
    assert unknown_views["views"] is None

    publication = _rows(canonical, "SELECT * FROM publication_records WHERE legacy_publication_id=50")[0]
    assert publication["generated_video_id"] == generated["id"]
    assert publication["public_content_id"] == "7654321098765432100"
    assert publication["provider_transaction_id"] is None
    assert generated["tiktok_publish_id"] is None

    metric = _rows(canonical, "SELECT * FROM video_metrics WHERE legacy_metric_id=60")[0]
    assert metric["generated_video_id"] == generated["id"]
    assert metric["publication_record_id"] == publication["id"]
    assert metric["shares"] is None
    assert metric["note"] == "kept note"

    action = _rows(canonical, "SELECT * FROM content_actions WHERE legacy_action_id=40")[0]
    assert action["canonical_job_id"] is None
    assert action["legacy_job_id"] == "missing-legacy-job"
    assert action["mapping_status"] == "mapped_without_job"


def test_rerun_is_idempotent_and_partial_run_resumes(tmp_path):
    legacy, _ = _seed_complete_legacy(tmp_path)
    canonical = tmp_path / "viralmint.db"
    migrator = PhaseGMigrator(canonical, legacy, tmp_path)

    partial = migrator.run(through_version="G003_INTELLIGENCE")
    assert partial["status"] == "partial"
    first_counts = inspect_phase_g(canonical)
    assert first_counts["radar_observations"] == 3
    assert first_counts["review_decisions"] == 0

    completed = migrator.run()
    before_rerun = inspect_phase_g(canonical)
    rerun = migrator.run()
    after_rerun = inspect_phase_g(canonical)
    assert completed["status"] == "completed"
    assert rerun["status"] == "completed"
    assert before_rerun == after_rerun
    versions = _rows(canonical, "SELECT version, status FROM schema_migrations ORDER BY version")
    assert [tuple(row) for row in versions] == [
        ("G001_SCHEMA", "completed"),
        ("G002_RADAR", "completed"),
        ("G003_INTELLIGENCE", "completed"),
        ("G004_REVIEW", "completed"),
        ("G005_PUBLICATION", "completed"),
        ("G006_RECONCILE", "completed"),
    ]


def test_canonical_current_values_win_over_legacy_while_missing_history_is_filled(tmp_path):
    legacy, _ = _seed_complete_legacy(tmp_path)
    canonical = tmp_path / "viralmint.db"
    PhaseGMigrator(canonical, legacy, tmp_path).run(through_version="G001_SCHEMA")
    conn = sqlite3.connect(canonical)
    conn.execute(
        """INSERT INTO scout_results
        (id,user_id,platform,video_id,video_url,title,created_at)
        VALUES ('canonical-id','local','bilibili','asset-1','https://canonical','Canonical current title','2026-09-25 00:00:00')"""
    )
    conn.commit()
    conn.close()

    report = PhaseGMigrator(canonical, legacy, tmp_path).run()
    row = _rows(canonical, "SELECT * FROM scout_results WHERE id='canonical-id'")[0]
    assert row["title"] == "Canonical current title"
    assert row["video_url"] == "https://canonical"
    assert row["creator_id"] == "creator-1"
    assert row["first_seen_at"] is not None
    assert report["conflicts"] >= 1


def test_orphan_review_is_retained_and_never_attached_to_a_near_match(tmp_path):
    legacy = tmp_path / "data" / "radar.db"
    legacy.parent.mkdir(parents=True)
    conn = _legacy_schema(legacy)
    now = "2026-09-22T05:00:00+00:00"
    conn.execute(
        "INSERT INTO content_reviews VALUES (1,'bilibili','missing','remake','APPROVED','do not lose',?,?,?)",
        (now, now, now),
    )
    conn.commit()
    conn.close()
    canonical = tmp_path / "viralmint.db"

    report = PhaseGMigrator(canonical, legacy, tmp_path).run()
    row = _rows(canonical, "SELECT * FROM review_decisions WHERE legacy_review_id=1")[0]
    assert row["entity_id"] is None
    assert row["mapping_status"] == "orphan"
    assert row["note"] == "do not lose"
    assert report["orphans"] == 1


def test_duplicate_legacy_identity_is_reported_without_duplicate_candidate(tmp_path):
    legacy, _ = _seed_complete_legacy(tmp_path, duplicate_content=True)
    canonical = tmp_path / "viralmint.db"

    report = PhaseGMigrator(canonical, legacy, tmp_path).run()

    rows = _rows(canonical, "SELECT id FROM scout_results WHERE platform='bilibili' AND video_id='asset-1'")
    assert len(rows) == 1
    assert report["duplicates"] >= 1


def test_legacy_database_is_opened_read_only(tmp_path):
    legacy, _ = _seed_complete_legacy(tmp_path)
    before = legacy.read_bytes()
    PhaseGMigrator(tmp_path / "viralmint.db", legacy, tmp_path).run()
    assert legacy.read_bytes() == before
