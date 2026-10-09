# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
import json
import logging
import os
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

    # Re-arm localization batch items admitted by a DEAD process, BEFORE the
    # zombie sweep. Order is load-bearing: the sweep fails every stale
    # running/pending row, so running it first destroyed exactly the admitted
    # batch items recovery exists to re-arm. Recovery claims its rows (fresh
    # owner pid + recovery marker) so the sweep that follows leaves them
    # alone. Never raises into startup.
    try:
        await recover_pending_localization_batch_items()
    except Exception as exc:   # never kill startup on recovery
        logger.warning("Localization batch recovery failed: %s", exc)

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
                    still_live.append(job.id)      # fresh heartbeat - leave alone
                    continue
                if _process_owns_batch_item(job):
                    # Stale heartbeat, but the recorded OWNER process is still
                    # running: this is a QUEUED item of a LIVE sequential
                    # coordinator, not a corpse. A queued item sits `pending`
                    # and gets no progress tick for as long as the item ahead of
                    # it takes (Whisper/Demucs on a full file), so the
                    # heartbeat alone cannot be trusted for batch rows.
                    still_live.append(job.id)
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


# ══════════════════════════════════════════════════════════════════════════
# F — durable localization BATCH admission + restart recovery
# ══════════════════════════════════════════════════════════════════════════
#
# POST /localization/batch admits N items by creating one Job row per file,
# saving each upload, and only then writing the batch marker set and
# dispatching ONE sequential coordinator. A crash in that window leaves rows
# `pending` with no runner attached; recovery re-arms them from durable state
# instead of only flipping them to `failed` and dropping the queue.
#
# Four properties make this safe, and each one is a defect that used to be
# here:
#
#   * OWNERSHIP. Every admitted/claimed row records the pid that owns it
#     (`batch_owner_pid`). A queued item of a LIVE coordinator has no progress
#     tick for as long as the item ahead of it takes, so neither the heartbeat
#     nor a stale sweep can distinguish "queued" from "corpse". Recovery
#     refuses to claim a row whose owner process is still alive, and the
#     zombie sweep refuses to fail it. Liveness is a standard-library check —
#     no new service, no schema change, no dependency.
#   * COMPLETENESS. `batch_admitted` + `batch_size` are written in ONE
#     transaction after every upload landed. Recovery only resumes a batch
#     whose recorded size matches the rows actually found, so a batch caught
#     mid-admission is failed loudly instead of half-resumed.
#   * SNAPSHOT. The resolved `profile_override`, `emoji_style`, and the stable
#     `batch_index` are durable at admission. A recovered item replays what
#     the user asked for; it never re-resolves against live Settings.
#   * CLAIM. The claim is a CONDITIONAL UPDATE guarded on `status='pending'`
#     AND the observed `task_id`, so two concurrent passes (or a pass racing
#     the live runner) cannot both believe they own the row.


# Windows liveness constants. PROCESS_QUERY_LIMITED_INFORMATION is the least
# privilege that answers "is this process still running"; SYNCHRONIZE is only
# added when the caller needs WaitForSingleObject.
_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WIN_SYNCHRONIZE = 0x00100000
_WIN_STILL_ACTIVE = 259
_WIN_WAIT_OBJECT_0 = 0x00000000
_WIN_WAIT_TIMEOUT = 0x00000102
_WIN_WAIT_FAILED = 0xFFFFFFFF
_WIN_ERROR_ACCESS_DENIED = 5
_WIN_ERROR_INVALID_PARAMETER = 87
_WIN_INVALID_HANDLE_VALUE = -1


def _win32_process_api():
    """A minimal ``kernel32`` shim with correct argtypes/restypes.

    ``HANDLE`` is 64-bit on x64: without an explicit ``restype`` ctypes assumes
    ``c_int`` and TRUNCATES the handle, so every later call operates on a
    garbage handle. Loading is separated from the probe so tests can inject a
    fake API instead of needing a real unowned process.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _windows_process_is_alive(pid: int, *, _api=None) -> bool:
    """Windows liveness probe that CANNOT signal, terminate, or suspend.

    ``os.kill(pid, 0)`` is NOT a POSIX existence probe on Windows. CPython's
    ``os_kill_impl`` branches on ``sig``: ``CTRL_C_EVENT``/``CTRL_BREAK_EVENT``
    (the literal values 0 and 1) go to ``GenerateConsoleCtrlEvent``, and EVERY
    other value goes to ``OpenProcess(PROCESS_ALL_ACCESS)`` +
    ``TerminateProcess(handle, sig)``. Neither branch can be a probe: signal 0
    emits a console Ctrl+C event aimed at the PROCESS GROUP whose id equals the
    pid we were asked about, and any other signal terminates the process outright.
    The ownership gate calls this on a pid recorded in the database - i.e. the
    pid of the LIVE coordinator it exists to protect.

    Instead: open the process read-only and ask whether its process OBJECT has
    been signalled. ``WaitForSingleObject(handle, 0)`` is the primary test -
    ``WAIT_TIMEOUT`` means still running, ``WAIT_OBJECT_0`` means exited. It is
    never used to signal, and needs only ``SYNCHRONIZE`` plus
    ``PROCESS_QUERY_LIMITED_INFORMATION``.

    ``GetExitCodeProcess`` is NOT the primary test: ``STILL_ACTIVE`` is the
    literal number 259, so a process that exited with exit code 259 reads as
    running. It is consulted only after ``WAIT_FAILED``, where nothing has been
    proven and a wrong answer must not cost another process its row.

    Fails SAFE toward "alive": ownership must be provable before a row is taken
    from another process, so an answer we could not obtain is never used to steal
    a row. The one conclusive negative is ``ERROR_INVALID_PARAMETER`` (no such
    pid) and a signalled process object.
    """
    if _api is None:
        _api = _win32_process_api()
    import ctypes

    # SYNCHRONIZE is required for WaitForSingleObject and is the ONLY reason to
    # ask for it; PROCESS_QUERY_LIMITED_INFORMATION is the least privilege that
    # answers the question, and PROCESS_ALL_ACCESS is never requested.
    access = _WIN_PROCESS_QUERY_LIMITED_INFORMATION | _WIN_SYNCHRONIZE
    try:
        handle = _api.OpenProcess(access, False, pid)
    except OSError:
        return True              # could not ask -> do not steal the row
    if not handle or handle == _WIN_INVALID_HANDLE_VALUE:
        # An existing-but-protected process denies the open; a pid that does not
        # exist fails with ERROR_INVALID_PARAMETER.
        return ctypes.get_last_error() == _WIN_ERROR_ACCESS_DENIED

    try:
        # PRIMARY test, and read-only by construction: a zero timeout only ASKS
        # whether the process object has been signalled (i.e. exited). It never
        # signals anything.
        #     WAIT_TIMEOUT -> still running, WAIT_OBJECT_0 -> has exited.
        # Deliberately primary rather than GetExitCodeProcess: STILL_ACTIVE is
        # the literal value 259, so a process that exited with exit code 259 is
        # indistinguishable from a RUNNING one by exit code alone (Microsoft
        # documents this on GetExitCodeProcess). Object state has no collision.
        waited = _api.WaitForSingleObject(handle, 0)
        if waited == _WIN_WAIT_TIMEOUT:
            return True
        if waited == _WIN_WAIT_OBJECT_0:
            return False           # signalled -> has exited
        if waited != _WIN_WAIT_FAILED:
            return True            # unexpected code: nothing proved it dead

        # WAIT_FAILED: fall back to the exit code, treating STILL_ACTIVE as
        # alive only because nothing proved otherwise.
        from ctypes import wintypes

        code = wintypes.DWORD()
        if _api.GetExitCodeProcess(handle, ctypes.byref(code)):
            if code.value == _WIN_STILL_ACTIVE:
                return True
            return False
        return ctypes.get_last_error() != _WIN_ERROR_INVALID_PARAMETER
    finally:
        _api.CloseHandle(handle)


def _process_is_alive(pid) -> bool:
    """True when *pid* names a live process on this host.

    Standard library only. Unknown/unparseable pids are treated as NOT alive:
    ownership must be provable before a row is taken from someone else.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        # NEVER os.kill() here: on Windows a non-console-event signal is
        # TerminateProcess, so the probe would kill the very coordinator the
        # ownership gate is protecting.
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, owned by someone else
    except OSError:
        return False
    return True


def _batch_owner_pid(job) -> int | None:
    """The recorded owning pid of an admitted batch item, if any."""
    try:
        payload = json.loads(str(job.input_json or "{}"))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not payload.get("batch"):
        return None
    pid = payload.get("batch_owner_pid")
    try:
        return int(pid)
    except (TypeError, ValueError):
        return None


def _process_owns_batch_item(job) -> bool:
    """True when a still-running process owns this admitted batch item."""
    pid = _batch_owner_pid(job)
    return pid is not None and _process_is_alive(pid)


async def _fail_batch_item(db, job_id: str, message: str) -> None:
    from datetime import datetime as _dt

    from sqlalchemy import update as _update

    from backend.models.job import Job as _Job

    await db.execute(
        _update(_Job)
        .where(_Job.id == job_id, _Job.status.in_(["pending", "running"]))
        .values(status="failed", error_message=message, completed_at=_dt.utcnow())
    )


async def recover_pending_localization_batch_items() -> list[str]:
    """Re-dispatch admitted-but-undispatched localization BATCH items.

    Returns the ids of the items re-queued, in stable ``batch_index`` order.
    Runs at boot from :func:`init_db` BEFORE the zombie sweep. Safe to call
    more than once, claims each row at most once, and never raises.

    Deliberately refuses three things, each of which used to happen:

    * a batch whose recorded ``batch_size`` does not match the rows found
      (partial admission — no half-batch resume, and no legacy
      ``batch``-without-marker auto-resume),
    * a row whose recorded owner process is still ALIVE,
    * a row already claimed by an earlier pass.
    """
    import json
    from datetime import datetime
    from pathlib import Path

    recovered: list[str] = []
    try:
        from sqlalchemy import select, update

        from backend.models.job import Job

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(Job).where(Job.job_type == "tool:translate")
            )).scalars().all()

            # Group by batch id first: completeness is a property of the whole
            # batch, not of an individual row.
            groups: dict[str, list] = {}
            for job in rows:
                try:
                    payload = json.loads(str(job.input_json or "{}"))
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload, dict) or not payload.get("batch"):
                    continue      # single job / rerun - not ours to claim
                groups.setdefault(str(payload.get("batch_id") or job.id), []).append(
                    (job, payload)
                )

            owner_pid = os.getpid()
            specs_by_batch: dict[str, list[dict]] = {}
            order: list[str] = []

            for batch_id, members in groups.items():
                # Stable index: the queue must run in admission order, not in
                # whatever order the rows came back from SQLite.
                members.sort(key=lambda item: (
                    item[1].get("batch_index")
                    if isinstance(item[1].get("batch_index"), int) else 1 << 30
                ))

                admitted = [m for m in members if m[1].get("batch_admitted") is True]
                claimed = [
                    m for m in admitted
                    if str(m[0].task_id or "").startswith("recovery_claim:batch:")
                ]
                # Terminal/running siblings prove original membership, never
                # authorize redispatch. Claims and live owners remain protected.
                candidates = [m for m in admitted
                              if m[0].status == "pending" and m not in claimed]
                if not candidates:
                    continue

                # Completeness: every member of the batch must carry the marker
                # set, and the recorded size must match what exists. A batch
                # caught mid-admission is not resumable in any meaningful sense
                # - fail it loudly rather than silently running part of it.
                declared = {m[1].get("batch_size") for m in admitted}
                size = next(iter(declared)) if len(declared) == 1 else None
                incomplete = (
                    len(admitted) != len(members)
                    or size is None
                    or not isinstance(size, int)
                    or size != len(members)
                )
                if incomplete:
                    for job, _payload_ in members:
                        if (job.status != "pending"
                                or str(job.task_id or "").startswith("recovery_claim:batch:")):
                            continue
                        if _process_owns_batch_item(job):
                            continue      # a live owner's row is not ours
                        await _fail_batch_item(
                            db, str(job.id),
                            "Batch admission was incomplete when the server "
                            "stopped - this item was never fully admitted. "
                            "Re-upload the file.",
                        )
                    continue

                for job, payload in candidates:
                    if _process_owns_batch_item(job):
                        continue      # LIVE coordinator owns this queue
                    in_path = payload.get("in_path")
                    if not in_path or not Path(str(in_path)).is_file():
                        # Nothing to resume from. Failed loudly rather than
                        # left `pending` forever, but NEVER dispatched.
                        await _fail_batch_item(
                            db, str(job.id),
                            "Batch upload is no longer on disk - cannot resume "
                            "after a server restart. Re-upload the file.",
                        )
                        continue

                    claim = f"recovery_claim:batch:{batch_id}"
                    res = await db.execute(
                        update(Job)
                        .where(Job.id == job.id, Job.status == "pending",
                               Job.task_id == job.task_id)
                        .values(task_id=claim)
                    )
                    if not res.rowcount:
                        continue      # someone else owns it now

                    # Take ownership in the SAME pass, so the zombie sweep that
                    # follows this recovery sees a live owner and leaves it be.
                    try:
                        snapshot = json.loads(str(job.input_json or "{}"))
                    except (TypeError, ValueError):
                        snapshot = {}
                    snapshot["batch_owner_pid"] = owner_pid
                    await db.execute(
                        update(Job)
                        .where(Job.id == job.id, Job.task_id == claim)
                        .values(input_json=json.dumps(snapshot))
                    )

                    recovered.append(str(job.id))
                    order.append(str(job.id))
                    specs_by_batch.setdefault(batch_id, []).append({
                        "job_id": str(job.id),
                        "in_path": Path(str(in_path)),
                        # The claim we hold, so the coordinator's pending->
                        # running attach is a CAS against THIS claim rather
                        # than a blind write.
                        "claim": claim,
                        "target_language": payload.get("target_language") or "vi",
                        "mode": payload.get("mode") or "full_dub",
                        "voice_id": payload.get("voice_id") or "",
                        "caption_style": payload.get("caption_style") or "viral",
                        "emoji_style": payload.get("emoji_style") or "moderate",
                        "target_aspect": payload.get("target_aspect"),
                        "profile_id": payload.get("profile_id"),
                        # The admission snapshot is immutable: a recovered item
                        # must not silently re-resolve against live Settings.
                        "profile_override": payload.get("profile_override"),
                        "auto_repair": bool(payload.get("auto_repair", False)),
                        "cleanup_input": bool(payload.get("cleanup_input", True)),
                    })
            await db.commit()

        if specs_by_batch:
            # One coordinator per original batch keeps the recovered queue as
            # sequential as the live one - no Demucs fan-out on a cold boot.
            from backend.api import localization as loc_api
            from backend.core.task_runner import dispatch

            for batch_id, specs in specs_by_batch.items():
                dispatch(loc_api._run_localization_batch_sequential(specs))
            logger.info(
                "Boot recovery re-queued %d localization batch item(s) across "
                "%d batch(es)", len(recovered), len(specs_by_batch),
            )
    except Exception as e:
        logger.warning(f"Localization batch recovery pass failed: {e}")
    return recovered
