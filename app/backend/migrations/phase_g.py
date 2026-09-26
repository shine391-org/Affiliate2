# SPDX-License-Identifier: AGPL-3.0-only
"""Phase-G one-way convergence from legacy Radar/overlay persistence.

The legacy SQLite database is always opened read-only. Each version commits
atomically to the canonical application DB and records a durable checkpoint.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import create_engine

from backend.database import Base


VERSIONS = (
    "G001_SCHEMA",
    "G002_RADAR",
    "G003_INTELLIGENCE",
    "G004_REVIEW",
    "G005_PUBLICATION",
    "G006_RECONCILE",
)

_PHASE_G_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "scout_results": (
        ("creator_id", "VARCHAR(200)"),
        ("first_seen_at", "DATETIME"),
        ("last_seen_at", "DATETIME"),
    ),
    "radar_observations": (
        ("job_id", "VARCHAR(36)"),
    ),
    "downloaded_videos": (
        ("source_sha256", "VARCHAR(64)"),
        ("intelligence_source", "VARCHAR(50)"),
    ),
    "generated_videos": (
        ("artifact_sha256", "VARCHAR(64)"),
        ("publish_approval_artifact_sha256", "VARCHAR(64)"),
        ("review_status", "VARCHAR(50)"),
        ("review_note", "TEXT"),
        ("reviewed_at", "DATETIME"),
        ("publish_approved_at", "DATETIME"),
    ),
    "video_metrics": (
        ("publication_record_id", "VARCHAR(36)"),
        ("legacy_metric_id", "INTEGER"),
        ("source", "VARCHAR(30)"),
        ("note", "TEXT"),
        ("raw_json", "TEXT"),
    ),
}

_TIKTOK_PUBLIC_ID = re.compile(r"/video/(\d+)(?:[/?#]|$)")


def _id(kind: str, *parts: object) -> str:
    return str(uuid5(NAMESPACE_URL, "affiliate2:" + kind + ":" + ":".join(str(p) for p in parts)))


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def _dt(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.isoformat(sep=" ")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _legacy_rows(conn: sqlite3.Connection, table: str, order: str = "id") -> list[sqlite3.Row]:
    if not _table_exists(conn, table):
        return []
    return conn.execute(f'SELECT * FROM "{table}" ORDER BY {order}').fetchall()


def _register_models() -> None:
    # Explicit imports keep Base.metadata deterministic for standalone migration
    # commands without changing the application's existing model registry style.
    from backend.models import (  # noqa: F401
        caption_style,
        chat_session,
        connected_channel,
        downloaded_video,
        dynamic_template,
        feature_flag,
        generated_video,
        job,
        messaging_config,
        phase_g,
        scout_result,
        user_behavior,
        user_profile,
        user_settings,
        video_metrics,
        viral_formula,
    )


def inspect_phase_g(canonical_db: str | Path) -> dict[str, int]:
    """Return stable row counts used for idempotency/parity assertions."""
    path = Path(canonical_db)
    if not path.exists():
        return {}
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        names = (
            "schema_migrations",
            "scout_results",
            "radar_observations",
            "radar_source_runs",
            "downloaded_videos",
            "generated_videos",
            "review_decisions",
            "content_actions",
            "publication_records",
            "video_metrics",
        )
        return {
            name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for name in names
            if _table_exists(conn, name)
        }
    finally:
        conn.close()


class PhaseGMigrator:
    """Versioned, rerunnable, read-only-from-legacy migration runner."""

    def __init__(self, canonical_db: str | Path, legacy_db: str | Path, project_root: str | Path):
        self.canonical_db = Path(canonical_db).expanduser().resolve()
        self.legacy_db = Path(legacy_db).expanduser().resolve()
        self.project_root = Path(project_root).expanduser().resolve()
        self.canonical_db.parent.mkdir(parents=True, exist_ok=True)

    def _canonical(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.canonical_db, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _legacy(self) -> sqlite3.Connection:
        if not self.legacy_db.is_file():
            raise FileNotFoundError(f"Legacy database not found: {self.legacy_db}")
        conn = sqlite3.connect(
            f"file:{self.legacy_db.as_posix()}?mode=ro", uri=True, timeout=30
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def _ensure_schema(self) -> None:
        _register_models()
        engine = create_engine(f"sqlite:///{self.canonical_db.as_posix()}")
        try:
            Base.metadata.create_all(engine)
        finally:
            engine.dispose()

        conn = self._canonical()
        try:
            for table, additions in _PHASE_G_COLUMNS.items():
                existing = _columns(conn, table)
                for column, declaration in additions:
                    if column not in existing:
                        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {declaration}')
                        existing.add(column)
            duplicate = conn.execute(
                """SELECT 1 FROM scout_results
                GROUP BY user_id, platform, video_id HAVING COUNT(*) > 1 LIMIT 1"""
            ).fetchone()
            if duplicate is None:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_scout_source_identity "
                    "ON scout_results(user_id, platform, video_id)"
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_video_metrics_legacy_id "
                "ON video_metrics(legacy_metric_id) WHERE legacy_metric_id IS NOT NULL"
            )
            conn.commit()
        finally:
            conn.close()

    def run(self, through_version: str | None = None) -> dict[str, Any]:
        if through_version is not None and through_version not in VERSIONS:
            raise ValueError(f"Unknown Phase-G migration version: {through_version}")
        self._ensure_schema()
        limit = VERSIONS.index(through_version) if through_version else len(VERSIONS) - 1
        steps: dict[str, Callable[[sqlite3.Connection, sqlite3.Connection], dict[str, Any]]] = {
            "G001_SCHEMA": self._g001_schema,
            "G002_RADAR": self._g002_radar,
            "G003_INTELLIGENCE": self._g003_intelligence,
            "G004_REVIEW": self._g004_review,
            "G005_PUBLICATION": self._g005_publication,
            "G006_RECONCILE": self._g006_reconcile,
        }

        canonical = self._canonical()
        legacy = self._legacy()
        try:
            for index, version in enumerate(VERSIONS):
                if index > limit:
                    break
                row = canonical.execute(
                    "SELECT status FROM schema_migrations WHERE version=?", (version,)
                ).fetchone()
                if row and row["status"] == "completed":
                    continue
                canonical.execute(
                    """INSERT INTO schema_migrations(version,status,started_at,completed_at,report_json,error_message)
                    VALUES (?, 'running', ?, NULL, NULL, NULL)
                    ON CONFLICT(version) DO UPDATE SET
                      status='running', started_at=excluded.started_at,
                      completed_at=NULL, error_message=NULL""",
                    (version, _now()),
                )
                canonical.commit()
                try:
                    with canonical:
                        result = steps[version](canonical, legacy)
                    canonical.execute(
                        """UPDATE schema_migrations SET status='completed', completed_at=?,
                        report_json=?, error_message=NULL WHERE version=?""",
                        (_now(), _json(result), version),
                    )
                    canonical.commit()
                except Exception as exc:
                    canonical.rollback()
                    canonical.execute(
                        "UPDATE schema_migrations SET status='failed', error_message=? WHERE version=?",
                        (str(exc), version),
                    )
                    canonical.commit()
                    raise
            report = self._build_report(canonical, legacy)
            report["status"] = "completed" if limit == len(VERSIONS) - 1 else "partial"
            return report
        finally:
            legacy.close()
            canonical.close()

    def _g001_schema(self, canonical: sqlite3.Connection, legacy: sqlite3.Connection) -> dict[str, Any]:
        return {"created": True}

    def _g002_radar(self, canonical: sqlite3.Connection, legacy: sqlite3.Connection) -> dict[str, Any]:
        stats = {"duplicates": 0, "conflicts": 0, "orphans": 0, "skipped": 0}
        content_rows = _legacy_rows(legacy, "content")
        snapshots = _legacy_rows(legacy, "metric_snapshots")
        latest: dict[tuple[str, str], sqlite3.Row] = {}
        for row in snapshots:
            key = (str(row["platform"]).lower(), str(row["content_id"]))
            current = latest.get(key)
            if current is None or (str(row["captured_at"]), int(row["id"])) > (
                str(current["captured_at"]), int(current["id"])
            ):
                latest[key] = row

        identity: dict[tuple[str, str], str] = {}
        seen: set[tuple[str, str]] = set()
        for row in content_rows:
            platform = str(row["platform"]).lower()
            content_id = str(row["content_id"])
            key = (platform, content_id)
            if key in seen:
                stats["duplicates"] += 1
                continue
            seen.add(key)
            existing = canonical.execute(
                "SELECT * FROM scout_results WHERE user_id='local' AND platform=? AND video_id=?",
                key,
            ).fetchone()
            recent = latest.get(key)
            if existing is None:
                scout_id = _id("scout", platform, content_id)
                canonical.execute(
                    """INSERT INTO scout_results(
                    id,user_id,job_id,platform,video_id,video_url,embed_url,title,description,
                    author,author_url,creator_id,thumbnail_url,views,likes,comments,shares,
                    duration_seconds,upload_date,virality_score,views_per_hour,outlier_score,
                    subscriber_count,channel_avg_views,niche,is_downloaded,is_analyzed,
                    created_at,first_seen_at,last_seen_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        scout_id, "local", None, platform, content_id,
                        row["canonical_url"] or f"legacy://{platform}/{content_id}", None,
                        row["title"], row["description"], row["creator_name"], None,
                        row["creator_id"], None,
                        recent["views"] if recent else None,
                        recent["likes"] if recent else None,
                        recent["comments"] if recent else None,
                        recent["shares"] if recent else None,
                        None, _dt(row["publish_time"]), 0.0, None, None, None, None,
                        row["source_keyword"], 0, 0,
                        _dt(row["created_at"]) or _dt(row["first_seen_at"]) or _now(),
                        _dt(row["first_seen_at"]), _dt(row["last_seen_at"]),
                    ),
                )
            else:
                scout_id = existing["id"]
                comparisons = {
                    "video_url": row["canonical_url"],
                    "title": row["title"],
                    "description": row["description"],
                    "author": row["creator_name"],
                    "niche": row["source_keyword"],
                }
                if any(
                    existing[name] not in (None, "") and value not in (None, "")
                    and str(existing[name]) != str(value)
                    for name, value in comparisons.items()
                ):
                    stats["conflicts"] += 1
                canonical.execute(
                    """UPDATE scout_results SET
                    video_url=COALESCE(NULLIF(video_url,''),?),
                    title=COALESCE(title,?), description=COALESCE(description,?),
                    author=COALESCE(author,?), creator_id=COALESCE(creator_id,?),
                    niche=COALESCE(niche,?), upload_date=COALESCE(upload_date,?),
                    first_seen_at=COALESCE(first_seen_at,?),
                    last_seen_at=CASE
                      WHEN last_seen_at IS NULL THEN ?
                      WHEN ? IS NULL THEN last_seen_at
                      WHEN last_seen_at < ? THEN ? ELSE last_seen_at END
                    WHERE id=?""",
                    (
                        row["canonical_url"], row["title"], row["description"],
                        row["creator_name"], row["creator_id"], row["source_keyword"],
                        _dt(row["publish_time"]), _dt(row["first_seen_at"]),
                        _dt(row["last_seen_at"]), _dt(row["last_seen_at"]),
                        _dt(row["last_seen_at"]), _dt(row["last_seen_at"]), scout_id,
                    ),
                )
            identity[key] = scout_id

        for row in snapshots:
            key = (str(row["platform"]).lower(), str(row["content_id"]))
            scout_id = identity.get(key)
            if scout_id is None:
                existing = canonical.execute(
                    "SELECT id FROM scout_results WHERE user_id='local' AND platform=? AND video_id=?",
                    key,
                ).fetchone()
                scout_id = existing["id"] if existing else None
            if scout_id is None:
                stats["orphans"] += 1
                continue
            canonical.execute(
                """INSERT OR IGNORE INTO radar_observations(
                id,scout_result_id,legacy_snapshot_id,captured_at,views,likes,comments,shares,favorites,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    _id("radar-observation", row["id"]), scout_id, row["id"],
                    _dt(row["captured_at"]), row["views"], row["likes"],
                    row["comments"], row["shares"], row["favorites"], _now(),
                ),
            )

        for row in _legacy_rows(legacy, "source_runs"):
            canonical.execute(
                """INSERT OR IGNORE INTO radar_source_runs(
                id,legacy_source_run_id,platform,keyword,started_at,finished_at,status,
                records_raw,records_normalized,records_new,records_updated,error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _id("radar-source-run", row["id"]), row["id"],
                    str(row["platform"]).lower(), row["keyword"], _dt(row["started_at"]),
                    _dt(row["finished_at"]), row["status"], row["records_raw"],
                    row["records_normalized"], row["records_new"], row["records_updated"],
                    row["error"],
                ),
            )
        stats["content"] = len(seen)
        stats["observations"] = canonical.execute(
            "SELECT COUNT(*) FROM radar_observations WHERE legacy_snapshot_id IS NOT NULL"
        ).fetchone()[0]
        stats["source_runs"] = canonical.execute(
            "SELECT COUNT(*) FROM radar_source_runs WHERE legacy_source_run_id IS NOT NULL"
        ).fetchone()[0]
        return stats

    @staticmethod
    def _candidate_identity(path: Path) -> tuple[str, str] | None:
        if "_" not in path.name:
            return None
        platform, content_id = path.name.split("_", 1)
        if platform not in {"bilibili", "douyin"} or not content_id:
            return None
        return platform, content_id

    def _g003_intelligence(self, canonical: sqlite3.Connection, legacy: sqlite3.Connection) -> dict[str, Any]:
        stats = {"imported": 0, "skipped": 0, "orphans": 0, "conflicts": 0}
        root = self.project_root / "data" / "candidates"
        if not root.is_dir():
            return stats
        for directory in sorted(root.iterdir()):
            if not directory.is_dir():
                continue
            identity = self._candidate_identity(directory)
            if identity is None:
                stats["skipped"] += 1
                continue
            source = directory / "source.mp4"
            transcript_path = directory / "transcript.json"
            intelligence_path = directory / "intelligence.json"
            if not (source.is_file() and transcript_path.is_file() and intelligence_path.is_file()):
                stats["skipped"] += 1
                continue
            scout = canonical.execute(
                "SELECT * FROM scout_results WHERE user_id='local' AND platform=? AND video_id=?",
                identity,
            ).fetchone()
            if scout is None:
                stats["orphans"] += 1
                continue
            transcript = _read_json(transcript_path)
            intelligence = _read_json(intelligence_path)
            metadata = _read_json(directory / "metadata.json") or {}
            if transcript is None or intelligence is None:
                stats["skipped"] += 1
                continue
            if isinstance(transcript, dict):
                segments = transcript.get("segments") or []
                transcript_text = transcript.get("text") or " ".join(
                    str(item.get("text") or "") for item in segments if isinstance(item, dict)
                ).strip()
                language = transcript.get("language") or transcript.get("detected_language")
            elif isinstance(transcript, list):
                segments = transcript
                transcript_text = " ".join(
                    str(item.get("text") or "") for item in segments if isinstance(item, dict)
                ).strip()
                language = None
            else:
                segments, transcript_text, language = [], str(transcript), None
            existing = canonical.execute(
                "SELECT * FROM downloaded_videos WHERE scout_result_id=? ORDER BY created_at,id LIMIT 1",
                (scout["id"],),
            ).fetchone()
            source_hash = _sha256(source)
            if existing is None:
                canonical.execute(
                    """INSERT INTO downloaded_videos(
                    id,user_id,scout_result_id,title,platform,video_path,audio_path,thumbnail_path,
                    transcript,transcript_language,transcript_source,transcript_segments_json,
                    insights_json,segment_analysis_json,improvement_suggestions_json,comments_json,
                    comment_insights_json,chapters_json,tags_json,category,media_metadata_json,
                    source_sha256,intelligence_source,duration_seconds,file_size_mb,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        _id("candidate-download", *identity), "local", scout["id"],
                        metadata.get("title") or scout["title"], identity[0], str(source.resolve()),
                        None, None, transcript_text or None, language, "legacy_candidate_intelligence",
                        _json(segments), _json(intelligence), None, None, None, None, None, None,
                        None, _json(metadata), source_hash, "legacy_candidate_intelligence",
                        metadata.get("duration") or metadata.get("duration_seconds"),
                        round(source.stat().st_size / (1024 * 1024), 6), _now(),
                    ),
                )
            else:
                if existing["source_sha256"] and existing["source_sha256"] != source_hash:
                    stats["conflicts"] += 1
                canonical.execute(
                    """UPDATE downloaded_videos SET
                    video_path=COALESCE(video_path,?), transcript=COALESCE(transcript,?),
                    transcript_language=COALESCE(transcript_language,?),
                    transcript_source=COALESCE(transcript_source,?),
                    transcript_segments_json=COALESCE(transcript_segments_json,?),
                    insights_json=COALESCE(insights_json,?),
                    media_metadata_json=COALESCE(media_metadata_json,?),
                    source_sha256=COALESCE(source_sha256,?),
                    intelligence_source=COALESCE(intelligence_source,?) WHERE id=?""",
                    (
                        str(source.resolve()), transcript_text or None, language,
                        "legacy_candidate_intelligence", _json(segments), _json(intelligence),
                        _json(metadata), source_hash, "legacy_candidate_intelligence", existing["id"],
                    ),
                )
            stats["imported"] += 1
        return stats

    def _ensure_legacy_remake(
        self, canonical: sqlite3.Connection, platform: str, content_id: str, final_path: Path
    ) -> sqlite3.Row | None:
        scout = canonical.execute(
            "SELECT * FROM scout_results WHERE user_id='local' AND platform=? AND video_id=?",
            (platform, content_id),
        ).fetchone()
        if scout is None:
            return None
        existing = canonical.execute(
            """SELECT * FROM generated_videos
            WHERE source_scout_result_id=? AND source_type='legacy_remake'
            ORDER BY created_at,id LIMIT 1""",
            (scout["id"],),
        ).fetchone()
        artifact_hash = _sha256(final_path)
        downloaded = canonical.execute(
            "SELECT id FROM downloaded_videos WHERE scout_result_id=? ORDER BY created_at,id LIMIT 1",
            (scout["id"],),
        ).fetchone()
        if existing is None:
            generated_id = _id("legacy-remake", platform, content_id)
            canonical.execute(
                """INSERT INTO generated_videos(
                id,user_id,source_scout_result_id,source_downloaded_video_id,title,script,niche,
                video_path,artifact_sha256,audio_path,thumbnail_path,aspect_ratio,duration_seconds,
                voice_id,gen_tier,source_type,uploaded_platforms_json,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    generated_id, "local", scout["id"], downloaded["id"] if downloaded else None,
                    scout["title"], None, scout["niche"], str(final_path.resolve()), artifact_hash,
                    None, None, "9:16", None, None, "legacy", "legacy_remake", "[]", "ready",
                    _now(), _now(),
                ),
            )
            existing = canonical.execute(
                "SELECT * FROM generated_videos WHERE id=?", (generated_id,)
            ).fetchone()
        else:
            canonical.execute(
                """UPDATE generated_videos SET
                video_path=COALESCE(video_path,?), artifact_sha256=COALESCE(artifact_sha256,?),
                source_downloaded_video_id=COALESCE(source_downloaded_video_id,?) WHERE id=?""",
                (str(final_path.resolve()), artifact_hash, downloaded["id"] if downloaded else None, existing["id"]),
            )
            existing = canonical.execute(
                "SELECT * FROM generated_videos WHERE id=?", (existing["id"],)
            ).fetchone()
        return existing

    def _g004_review(self, canonical: sqlite3.Connection, legacy: sqlite3.Connection) -> dict[str, Any]:
        stats = {"remakes": 0, "reviews": 0, "actions": 0, "orphans": 0, "conflicts": 0}
        remakes_root = self.project_root / "data" / "remakes"
        if remakes_root.is_dir():
            for directory in sorted(remakes_root.iterdir()):
                if not directory.is_dir():
                    continue
                identity = self._candidate_identity(directory)
                final_path = directory / "final.mp4"
                if identity is None or not final_path.is_file():
                    continue
                if self._ensure_legacy_remake(canonical, *identity, final_path) is None:
                    stats["orphans"] += 1
                else:
                    stats["remakes"] += 1

        for row in _legacy_rows(legacy, "content_reviews"):
            platform, content_id = str(row["platform"]).lower(), str(row["content_id"])
            entity_type = "scout_result" if row["stage"] == "candidate" else "generated_video"
            if entity_type == "scout_result":
                entity = canonical.execute(
                    "SELECT id FROM scout_results WHERE user_id='local' AND platform=? AND video_id=?",
                    (platform, content_id),
                ).fetchone()
            else:
                entity = canonical.execute(
                    """SELECT gv.id FROM generated_videos gv
                    JOIN scout_results sr ON sr.id=gv.source_scout_result_id
                    WHERE sr.user_id='local' AND sr.platform=? AND sr.video_id=?
                      AND gv.source_type='legacy_remake'
                    ORDER BY gv.created_at,gv.id LIMIT 1""",
                    (platform, content_id),
                ).fetchone()
            mapping_status = "mapped" if entity else "orphan"
            if entity is None:
                stats["orphans"] += 1
            canonical.execute(
                """INSERT OR IGNORE INTO review_decisions(
                id,legacy_review_id,entity_type,entity_id,source_platform,source_content_id,
                stage,status,note,workflow_source,mapping_status,reviewed_at,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _id("legacy-review", row["id"]), row["id"], entity_type,
                    entity["id"] if entity else None, platform, content_id, row["stage"],
                    row["status"], row["note"], "legacy_overlay", mapping_status,
                    _dt(row["reviewed_at"]), _dt(row["created_at"]) or _now(),
                    _dt(row["updated_at"]) or _now(),
                ),
            )
            if entity and entity_type == "generated_video":
                current = canonical.execute(
                    "SELECT * FROM generated_videos WHERE id=?", (entity["id"],)
                ).fetchone()
                if current["review_status"] and current["review_status"] != row["status"]:
                    stats["conflicts"] += 1
                canonical.execute(
                    """UPDATE generated_videos SET
                    review_status=COALESCE(review_status,?), review_note=COALESCE(review_note,?),
                    reviewed_at=COALESCE(reviewed_at,?) WHERE id=?""",
                    (row["status"], row["note"], _dt(row["reviewed_at"]), entity["id"]),
                )
                if row["status"] == "APPROVED":
                    approved = _dt(row["reviewed_at"]) or _dt(row["updated_at"]) or _now()
                    canonical.execute(
                        """UPDATE generated_videos SET
                        publish_approved_at=COALESCE(publish_approved_at,?),
                        publish_approval_artifact_sha256=COALESCE(publish_approval_artifact_sha256,artifact_sha256)
                        WHERE id=?""",
                        (approved, entity["id"]),
                    )
            stats["reviews"] += 1

        for row in _legacy_rows(legacy, "content_actions"):
            platform, content_id = str(row["platform"]).lower(), str(row["content_id"])
            scout = canonical.execute(
                "SELECT id FROM scout_results WHERE user_id='local' AND platform=? AND video_id=?",
                (platform, content_id),
            ).fetchone()
            generated = canonical.execute(
                """SELECT gv.id FROM generated_videos gv JOIN scout_results sr
                ON sr.id=gv.source_scout_result_id WHERE sr.user_id='local' AND sr.platform=?
                AND sr.video_id=? AND gv.source_type='legacy_remake'
                ORDER BY gv.created_at,gv.id LIMIT 1""",
                (platform, content_id),
            ).fetchone()
            job = canonical.execute("SELECT id FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
            if scout is None:
                mapping_status = "orphan"
                stats["orphans"] += 1
            elif job is None:
                mapping_status = "mapped_without_job"
            else:
                mapping_status = "mapped"
            canonical.execute(
                """INSERT OR IGNORE INTO content_actions(
                id,legacy_action_id,source_platform,source_content_id,scout_result_id,
                generated_video_id,canonical_job_id,legacy_job_id,action,params_json,
                mapping_status,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _id("legacy-action", row["id"]), row["id"], platform, content_id,
                    scout["id"] if scout else None, generated["id"] if generated else None,
                    job["id"] if job else None, None if job else row["job_id"], row["action"],
                    row["params_json"], mapping_status, _dt(row["created_at"]) or _now(),
                ),
            )
            stats["actions"] += 1
        return stats

    @staticmethod
    def _public_identifier(platform: str, external_id: Any, external_url: Any) -> tuple[str | None, str | None]:
        platform = platform.lower()
        if platform == "tiktok" and external_url:
            match = _TIKTOK_PUBLIC_ID.search(str(external_url))
            if match:
                return match.group(1), "public_post_id"
        if external_id:
            kind = "public_video_id" if platform in {"youtube", "instagram"} else "legacy_external_id"
            return str(external_id), kind
        return None, None

    def _g005_publication(self, canonical: sqlite3.Connection, legacy: sqlite3.Connection) -> dict[str, Any]:
        stats = {"publications": 0, "metrics": 0, "orphans": 0, "conflicts": 0, "skipped": 0}
        publications: dict[int, sqlite3.Row] = {}
        for row in _legacy_rows(legacy, "content_publications"):
            platform = str(row["source_platform"]).lower()
            content_id = str(row["content_id"])
            generated = canonical.execute(
                """SELECT gv.* FROM generated_videos gv JOIN scout_results sr
                ON sr.id=gv.source_scout_result_id WHERE sr.user_id='local' AND sr.platform=?
                AND sr.video_id=? AND gv.source_type='legacy_remake'
                ORDER BY gv.created_at,gv.id LIMIT 1""",
                (platform, content_id),
            ).fetchone()
            mapping_status = "mapped" if generated else "unmapped"
            if generated is None:
                stats["orphans"] += 1
            public_id, identifier_kind = self._public_identifier(
                str(row["platform"]), row["external_id"], row["external_url"]
            )
            record_id = _id("legacy-publication", row["id"])
            canonical.execute(
                """INSERT OR IGNORE INTO publication_records(
                id,legacy_publication_id,generated_video_id,source_platform,source_content_id,
                platform,job_id,status,title,caption,privacy,provider_transaction_id,
                public_content_id,identifier_kind,external_url,error_message,mapping_status,
                created_at,updated_at,published_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record_id, row["id"], generated["id"] if generated else None,
                    platform, content_id, str(row["platform"]).lower(),
                    row["job_id"] if canonical.execute("SELECT 1 FROM jobs WHERE id=?", (row["job_id"],)).fetchone() else None,
                    row["status"], row["title"], row["caption"], row["privacy"], None,
                    public_id, identifier_kind, row["external_url"], row["error_message"],
                    mapping_status, _dt(row["created_at"]) or _now(),
                    _dt(row["updated_at"]) or _now(), _dt(row["published_at"]),
                ),
            )
            publications[int(row["id"])] = canonical.execute(
                "SELECT * FROM publication_records WHERE legacy_publication_id=?", (row["id"],)
            ).fetchone()
            if generated and row["status"] == "PUBLISHED":
                try:
                    destinations = json.loads(generated["uploaded_platforms_json"] or "[]")
                except (TypeError, json.JSONDecodeError):
                    destinations = []
                destination = str(row["platform"]).lower()
                if destination not in destinations:
                    destinations.append(destination)
                updates = {"youtube_video_id": None, "instagram_media_id": None}
                if destination == "youtube":
                    updates["youtube_video_id"] = public_id
                elif destination == "instagram":
                    updates["instagram_media_id"] = public_id
                canonical.execute(
                    """UPDATE generated_videos SET uploaded_platforms_json=?, status='uploaded',
                    youtube_video_id=COALESCE(youtube_video_id,?),
                    instagram_media_id=COALESCE(instagram_media_id,?) WHERE id=?""",
                    (_json(destinations), updates["youtube_video_id"], updates["instagram_media_id"], generated["id"]),
                )
            stats["publications"] += 1

        for row in _legacy_rows(legacy, "publication_metrics"):
            publication = publications.get(int(row["publication_id"]))
            if publication is None:
                publication = canonical.execute(
                    "SELECT * FROM publication_records WHERE legacy_publication_id=?",
                    (row["publication_id"],),
                ).fetchone()
            if publication is None:
                stats["orphans"] += 1
                stats["skipped"] += 1
                continue
            canonical.execute(
                """INSERT OR IGNORE INTO video_metrics(
                id,generated_video_id,publication_record_id,legacy_metric_id,platform,source,note,
                raw_json,views,likes,comments,shares,watch_time_hours,avg_view_duration,ctr,fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _id("legacy-publication-metric", row["id"]), publication["generated_video_id"],
                    publication["id"], row["id"], publication["platform"], row["source"],
                    row["note"], row["raw_json"], row["views"], row["likes"], row["comments"],
                    row["shares"], None, None, None, _dt(row["collected_at"]) or _now(),
                ),
            )
            stats["metrics"] += 1
        return stats

    def _g006_reconcile(self, canonical: sqlite3.Connection, legacy: sqlite3.Connection) -> dict[str, Any]:
        return self._build_report(canonical, legacy)

    def _step_reports(self, canonical: sqlite3.Connection) -> list[dict[str, Any]]:
        reports = []
        for row in canonical.execute(
            """SELECT version,report_json FROM schema_migrations
            WHERE status='completed' AND version!='G006_RECONCILE'
            ORDER BY version"""
        ):
            if not row["report_json"]:
                continue
            try:
                value = json.loads(row["report_json"])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                reports.append(value)
        return reports

    def _build_report(self, canonical: sqlite3.Connection, legacy: sqlite3.Connection) -> dict[str, Any]:
        def count(table: str, where: str = "", params: tuple = ()) -> int:
            if not _table_exists(canonical, table):
                return 0
            return canonical.execute(
                f'SELECT COUNT(*) FROM "{table}" {where}', params
            ).fetchone()[0]

        def legacy_count(table: str) -> int:
            if not _table_exists(legacy, table):
                return 0
            return legacy.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

        reports = self._step_reports(canonical)
        duplicates = sum(int(item.get("duplicates", 0) or 0) for item in reports)
        conflicts = sum(int(item.get("conflicts", 0) or 0) for item in reports)
        skipped = sum(int(item.get("skipped", 0) or 0) for item in reports)
        mapping_orphans = sum(
            count(table, "WHERE mapping_status IN ('orphan','unmapped')")
            for table in ("review_decisions", "content_actions", "publication_records")
        )
        observation_orphans = canonical.execute(
            """SELECT COUNT(*) FROM radar_observations ro
            LEFT JOIN scout_results sr ON sr.id=ro.scout_result_id WHERE sr.id IS NULL"""
        ).fetchone()[0]
        metric_orphans = canonical.execute(
            """SELECT COUNT(*) FROM video_metrics vm
            LEFT JOIN publication_records pr ON pr.id=vm.publication_record_id
            WHERE vm.legacy_metric_id IS NOT NULL AND pr.id IS NULL"""
        ).fetchone()[0]
        counts = {
            "legacy_content": legacy_count("content"),
            "legacy_observations": legacy_count("metric_snapshots"),
            "legacy_source_runs": legacy_count("source_runs"),
            "legacy_reviews": legacy_count("content_reviews"),
            "legacy_actions": legacy_count("content_actions"),
            "legacy_publications": legacy_count("content_publications"),
            "legacy_metrics": legacy_count("publication_metrics"),
            "migrated_candidates": count("scout_results"),
            "migrated_observations": count("radar_observations", "WHERE legacy_snapshot_id IS NOT NULL"),
            "migrated_source_runs": count("radar_source_runs", "WHERE legacy_source_run_id IS NOT NULL"),
            "migrated_reviews": count("review_decisions", "WHERE legacy_review_id IS NOT NULL"),
            "migrated_actions": count("content_actions", "WHERE legacy_action_id IS NOT NULL"),
            "migrated_publications": count("publication_records", "WHERE legacy_publication_id IS NOT NULL"),
            "migrated_metrics": count("video_metrics", "WHERE legacy_metric_id IS NOT NULL"),
        }
        return {
            "status": "completed",
            "canonical_db": str(self.canonical_db),
            "legacy_db": str(self.legacy_db),
            "counts": counts,
            "duplicates": duplicates,
            "orphans": mapping_orphans + observation_orphans + metric_orphans,
            "conflicts": conflicts,
            "skipped": skipped,
            "relationship_checks": {
                "observation_orphans": observation_orphans,
                "metric_publication_orphans": metric_orphans,
                "unmapped_reviews_actions_publications": mapping_orphans,
            },
        }
