# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
import logging
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text
from backend.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False},  # SQLite only
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable WAL mode for concurrent reads + faster writes."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")   # Wait up to 5s on lock contention
    cursor.execute("PRAGMA cache_size=-64000")   # 64MB cache
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()



async def _run_phase_g_data_migration_if_needed() -> None:
    """Migrate retired Radar/overlay data before canonical runtime starts.

    The legacy DB is read-only to the migrator. Before the first real cutover we
    take SQLite-consistent backups of both databases. A failed migration aborts
    startup instead of silently launching against a canonical DB that is missing
    historical review/publication/performance state.
    """
    import asyncio
    import os
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    from backend.config import DATA_DIR

    legacy = DATA_DIR / "data" / "radar.db"
    canonical = DATA_DIR / "viralmint.db"
    if not legacy.is_file():
        return

    def _completed() -> bool:
        if not canonical.is_file():
            return False
        conn = sqlite3.connect(
            f"file:{canonical.resolve().as_posix()}?mode=ro", uri=True, timeout=10
        )
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if not table:
                return False
            row = conn.execute(
                "SELECT status FROM schema_migrations WHERE version='G006_RECONCILE'"
            ).fetchone()
            return bool(row and row[0] == "completed")
        finally:
            conn.close()

    if await asyncio.to_thread(_completed):
        return

    project_root = Path(os.getenv("CONTENT_ENGINE_ROOT") or DATA_DIR).resolve()

    def _backup_sqlite(source: Path, destination: Path) -> None:
        if not source.is_file():
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(
            f"file:{source.resolve().as_posix()}?mode=ro", uri=True, timeout=30
        )
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

    def _migrate() -> None:
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        backup_dir = DATA_DIR / ".runtime" / "phase-g-backups" / stamp
        _backup_sqlite(canonical, backup_dir / "viralmint.db")
        _backup_sqlite(legacy, backup_dir / "radar.db")

        from backend.migrations.phase_g import PhaseGMigrator

        report = PhaseGMigrator(canonical, legacy, project_root).run()
        if report.get("status") != "completed":
            raise RuntimeError(f"Phase G migration did not complete: {report}")

    logger.info("Phase G legacy data detected; running one-time canonical migration")
    try:
        await asyncio.to_thread(_migrate)
    except Exception:
        logger.exception(
            "Phase G migration failed; refusing to start with split persistence"
        )
        raise

async def init_db():
    """Create all tables. Called once at startup from run.py."""
    import asyncio
    from backend.config import DATA_DIR
    from backend.migrations.repair_datetime_types import repair_database_datetimes

    canonical = DATA_DIR / "viralmint.db"
    if canonical.is_file():
        await asyncio.to_thread(repair_database_datetimes, canonical)

    await _run_phase_g_data_migration_if_needed()

    # Import all models so Base knows about them
    from backend.models import (  # noqa: F401
        user_settings, user_behavior, feature_flag,
        job, scout_result, downloaded_video, generated_video,
        messaging_config, chat_session, user_profile,
        video_metrics, viral_formula,
        connected_channel, dynamic_template, caption_style,
        phase_g,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Read each table's columns ONCE and skip ALTERs for columns that
        # already exist, instead of firing an ALTER-and-catch per column on
        # every boot. _add_column_if_missing fills this lazily per table.
        _cols: dict[str, set[str]] = {}
        # Idempotent column additions for SQLite (no Alembic)
        await _add_column_if_missing(conn, "downloaded_videos", "transcript_segments_json", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "downloaded_videos", "media_metadata_json", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "downloaded_videos", "source_sha256", "VARCHAR(64)", existing=_cols)
        await _add_column_if_missing(conn, "downloaded_videos", "intelligence_source", "VARCHAR(50)", existing=_cols)
        # Job heartbeat (zombie-sweep staleness signal — see models/job.py)
        await _add_column_if_missing(conn, "jobs", "updated_at", "DATETIME", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "source_type", "VARCHAR(30)", existing=_cols)
        # Clip extraction fields
        await _add_column_if_missing(conn, "generated_videos", "clip_start_seconds", "FLOAT", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "clip_end_seconds", "FLOAT", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "clip_virality_score", "FLOAT", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "clip_hook_score", "FLOAT", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "clip_hook_type", "VARCHAR(30)", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "clip_virality_reason", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "clip_score_breakdown_json", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "caption_status", "VARCHAR(20)", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "metadata_status", "VARCHAR(20)", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "publish_approved_at", "DATETIME", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "artifact_sha256", "VARCHAR(64)", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "publish_approval_artifact_sha256", "VARCHAR(64)", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "review_status", "VARCHAR(50)", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "review_note", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "generated_videos", "reviewed_at", "DATETIME", existing=_cols)
        await _add_column_if_missing(conn, "scout_results", "creator_id", "VARCHAR(200)", existing=_cols)
        await _add_column_if_missing(conn, "scout_results", "first_seen_at", "DATETIME", existing=_cols)
        await _add_column_if_missing(conn, "scout_results", "last_seen_at", "DATETIME", existing=_cols)
        await _add_column_if_missing(conn, "radar_observations", "job_id", "VARCHAR(36)", existing=_cols)
        await _add_column_if_missing(conn, "video_metrics", "publication_record_id", "VARCHAR(36)", existing=_cols)
        await _add_column_if_missing(conn, "video_metrics", "legacy_metric_id", "INTEGER", existing=_cols)
        await _add_column_if_missing(conn, "video_metrics", "source", "VARCHAR(30)", existing=_cols)
        await _add_column_if_missing(conn, "video_metrics", "note", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "video_metrics", "raw_json", "TEXT", existing=_cols)
        # BYOK: per-user encrypted keys (override .env at runtime)
        await _add_column_if_missing(conn, "user_settings", "ai_provider", "VARCHAR(20)", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "ai_model", "VARCHAR(100)", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "ai_base_url", "VARCHAR(500)", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "ai_api_key_encrypted", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "ai_provider_profiles_json", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "ai_connection_id", "VARCHAR(96)", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "ai_connections_json", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "localization_profiles_json", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "tts_model", "VARCHAR(200)", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "tts_base_url", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "tts_region", "VARCHAR(100)", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "tts_api_key_encrypted", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "tts_provider_profiles_json", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "tts_connection_id", "VARCHAR(96)", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "tts_connections_json", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "youtube_api_key_encrypted", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "huggingface_token_encrypted", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "download_cookie_browser", "VARCHAR(20)", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "download_cookie_profile", "VARCHAR(500)", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "download_cookie_executable_path", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "douyin_browser", "VARCHAR(20)", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "douyin_executable_path", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "douyin_cdp_url", "VARCHAR(500)", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "meta_page_access_token_encrypted", "TEXT", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "meta_page_id", "VARCHAR(100)", existing=_cols)
        await _add_column_if_missing(conn, "user_settings", "meta_page_name", "VARCHAR(300)", existing=_cols)

    # Drift sentinel — compare every model's columns against the live DB
    # schema and log a loud warning for any column the model declares but
    # the DB lacks. This converts a future "endpoint 500" silent regression
    # (a model column added without a matching `_add_column_if_missing` line)
    # into a clear startup log line. Runs once, after migrations, before
    # zombie cleanup. Pure observability — never raises.
    await _warn_on_schema_drift()

    # Clean up zombie jobs — any jobs stuck at "running"/"pending" from a previous crash
    await _cleanup_zombie_jobs()


async def _warn_on_schema_drift():
    """Compare model schema vs live DB; log WARN for missing columns.

    Catches the class of regression where a column is added to a model
    but the matching `_add_column_if_missing` call is forgotten in
    init_db. Upgrade-install users hit a 500 on the first endpoint that
    SELECTs the new column; fresh installs work because the table is
    created from scratch. This sentinel makes the drift visible at
    startup so the next regression is caught before users file tickets.
    """
    try:
        async with engine.connect() as conn:
            for table_name, mapper in Base.metadata.tables.items():
                try:
                    rows = await conn.exec_driver_sql(f"PRAGMA table_info({table_name})")
                    db_cols = {r[1] for r in rows.fetchall()}
                except Exception:
                    # Table doesn't exist yet — create_all just made it, no drift possible.
                    continue
                if not db_cols:
                    continue
                model_cols = {c.name for c in mapper.columns}
                missing = model_cols - db_cols
                if missing:
                    logger.warning(
                        "Schema drift detected on table %r: model declares columns "
                        "missing from DB: %s. Add `_add_column_if_missing(conn, %r, ...)` "
                        "lines for each in backend/database.py init_db().",
                        table_name, sorted(missing), table_name,
                    )
    except Exception as e:
        logger.warning("Schema-drift sentinel failed (non-fatal): %s", e)


# A running/pending job whose heartbeat is younger than this is treated as
# ALIVE — possibly owned by a PREDECESSOR backend draining through a restart
# handoff. uvicorn frees its listening socket at the START of graceful
# shutdown and then keeps finishing background work, so a replacement instance
# can boot while the old one is still running a job: "port free" never meant
# "process gone". Progress ticks refresh the heartbeat every few seconds (see
# ws_manager.send_progress), so 180s tolerates the occasional long silent step
# (Whisper on a big file) without leaving genuinely dead jobs stuck "running"
# for more than a couple of minutes.
ZOMBIE_GRACE_SECONDS = 180

# Job ids that were still non-terminal, with a FRESH heartbeat, when the boot
# sweep ran. The lifespan handoff watcher (backend/main.py) watches exactly this
# set. It deliberately does NOT re-query "everything running" later: by then this
# instance may have created jobs of its own, and a job with a long silent step
# (Whisper on a big file can exceed the grace period without a progress tick)
# would be failed while it is perfectly alive — the very bug this machinery
# exists to prevent. Mutated in place so `from … import` references stay valid.
BOOT_FRESH_JOB_IDS: list[str] = []


async def sweep_stale_jobs(grace_seconds: int = ZOMBIE_GRACE_SECONDS,
                           only_ids: list[str] | None = None) -> tuple[int, list[str]]:
    """Fail running/pending jobs whose heartbeat is STALE; leave fresh ones.

    Returns ``(swept_count, still_nonterminal_ids)``. A NULL heartbeat (rows
    written before the ``updated_at`` column existed) counts as stale — the same
    outcome those rows always got.

    ``only_ids`` scopes a pass to a known watch-set (the handoff watcher's boot
    snapshot) so jobs created by the CURRENT instance are never candidates.

    The write is a CONDITIONAL UPDATE, not a read-then-mutate. A plain ORM
    mutation is a lost update: if a draining predecessor commits "success"
    between our SELECT and our COMMIT, we would overwrite it with "failed" and it
    would never write again — producing exactly the failure-toast-for-a-
    completed-video bug in a narrower window.

    The staleness DECISION is made in Python from the parsed timestamp; the SQL
    guard is `status IN ('running','pending')` and nothing more. That is what
    closes the harmful race — a predecessor that COMPLETES between our SELECT and
    our UPDATE is no longer running/pending, so we match zero rows and leave its
    result intact.

    `updated_at` is deliberately absent from the WHERE. SQLite stores DATETIME as
    TEXT, so any comparison there silently depends on the stored separator:
    '2026-07-26T13:47:04' sorts ABOVE '2026-07-26 13:54:16' because 'T' > ' ',
    which made a genuinely 10-minute-stale row look fresh (caught live). An
    equality guard has the same flaw — SQLAlchemy re-binds the value with its own
    separator and matches nothing. The residual cost is that a progress tick
    landing inside the SELECT→UPDATE window (microseconds, on a job already
    silent for the whole grace period) does not spare the job; status is the
    guard that matters. Never raises.
    """
    from datetime import datetime, timedelta

    swept, still_live = 0, []
    try:
        async with AsyncSessionLocal() as db:
            from backend.models.job import Job
            from sqlalchemy import select, update
            q = select(Job).where(Job.status.in_(["running", "pending"]))
            if only_ids is not None:
                if not only_ids:
                    return 0, []
                q = q.where(Job.id.in_(only_ids))
            rows = (await db.execute(q)).scalars().all()
            cutoff = datetime.utcnow() - timedelta(seconds=grace_seconds)
            for job in rows:
                if not (job.updated_at is None or job.updated_at < cutoff):
                    still_live.append(job.id)      # fresh heartbeat — leave alone
                    continue

                marker = str(job.task_id or "")
                recovery_claim = (
                    job.status == "pending"
                    and marker.startswith("recovery_claim:")
                )
                res = await db.execute(
                    update(Job)
                    .where(
                        Job.id == job.id,
                        Job.status.in_(["running", "pending"]),
                    )
                    .values(
                        status="failed",
                        task_id=None,
                        error_message=(
                            "Server restarted during a recovery action — retry from the persisted stage"
                            if recovery_claim
                            else "Server restarted — job did not complete"
                        ),
                        completed_at=datetime.utcnow(),
                    )
                )
                if res.rowcount:
                    swept += 1
                else:
                    # Raced: it went terminal since our SELECT (the drainer
                    # finished). Its result stands. Report it back so a caller's
                    # next pass drops it for free.
                    still_live.append(job.id)
            await db.commit()
    except Exception as e:
        logger.warning(f"Zombie job sweep failed: {e}")
    return swept, still_live


async def _cleanup_zombie_jobs():
    """Boot-time sweep: fail STALE running/pending jobs from a previous session.

    Fresh ones are deliberately left alone and recorded in BOOT_FRESH_JOB_IDS for
    the lifespan handoff watcher (backend/main.py) — they may belong to a
    predecessor instance that is still draining, and the watcher sweeps them the
    moment they go stale.
    """
    swept, fresh = await sweep_stale_jobs()
    BOOT_FRESH_JOB_IDS[:] = fresh
    if swept:
        logger.warning(f"Marked {swept} zombie jobs as failed from previous session")
    if fresh:
        logger.info(
            "Boot sweep: %d running/pending job(s) still have a fresh heartbeat "
            "— possibly a draining predecessor; the handoff watcher will decide.",
            len(fresh),
        )


async def _table_columns(conn, table: str) -> set[str]:
    """Column names currently on `table`; empty set if the table doesn't exist."""
    try:
        rows = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        return {r[1] for r in rows.fetchall()}
    except Exception:
        return set()


async def _add_column_if_missing(conn, table: str, column: str, col_type: str, *, existing=None):
    """SQLite-safe column addition — no-op if the column already exists.

    Every one of these used to fire an `ALTER TABLE … ADD COLUMN` on every boot
    and swallow the "duplicate column" error — a throwaway failed statement per
    column per startup once the DB is up to date, which is the normal case.

    Pass `existing`, a ``{table: set(columns)}`` cache, and the ALTER is skipped
    entirely for columns already present: one PRAGMA read per table, then zero
    ALTERs on a current DB. The cache is filled lazily per table and updated
    after a successful ALTER, so a later call for the same table sees a column
    this one just added. Without `existing` the old try/except path is kept, so
    the helper stays correct if called standalone.
    """
    if existing is not None:
        cols = existing.get(table)
        if cols is None:
            cols = await _table_columns(conn, table)
            existing[table] = cols
        if column in cols:
            return
    try:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        if existing is not None:
            existing.setdefault(table, set()).add(column)
    except Exception:
        # Never treat every ALTER failure as "column already exists". A locked,
        # read-only or corrupt upgrade DB must fail loudly; otherwise the ORM
        # model moves ahead while SQLite silently stays on the old schema.
        cols_after = await _table_columns(conn, table)
        if column in cols_after:
            if existing is not None:
                existing[table] = cols_after
            return
        logger.exception(
            "Failed to add required column %s.%s (%s)",
            table, column, col_type,
        )
        raise
