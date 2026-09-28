# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CANONICAL_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"

# Strict pattern for canonical format: YYYY-MM-DD HH:MM:SS.ffffff
_CANONICAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}$")


def _backup_sqlite_database(source: sqlite3.Connection, destination: Path) -> None:
    """Create a transactionally consistent SQLite snapshot, including WAL data."""
    backup_conn = sqlite3.connect(str(destination))
    try:
        source.backup(backup_conn)
    finally:
        backup_conn.close()


def to_canonical_datetime_str(value: Any) -> str | None:
    """Normalize any datetime/epoch/ISO string representation into canonical

    UTC-naive format: YYYY-MM-DD HH:MM:SS.ffffff. Returns None if value is None
    or unparseable.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 1e11:  # Milliseconds
                ts /= 1000.0
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
            return dt.strftime(CANONICAL_DATETIME_FORMAT)
        except (ValueError, OSError):
            return None

    text = str(value).strip()
    if not text:
        return None

    if _CANONICAL_RE.match(text):
        return text

    # Handle numeric string
    if text.isdigit() or (text.replace(".", "", 1).isdigit() and "." in text):
        try:
            return to_canonical_datetime_str(float(text))
        except ValueError:
            pass

    # Try ISO parsing
    try:
        clean_text = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_text)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.strftime(CANONICAL_DATETIME_FORMAT)
    except Exception:
        pass

    # Try common formats without microseconds
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime(CANONICAL_DATETIME_FORMAT)
        except ValueError:
            pass

    return None


class MalformedDatetimeError(ValueError):
    """A non-null datetime value could not be parsed into canonical format.

    Raised INSTEAD of mutating the value. Automatic startup repair must never
    invent data: a value this module cannot understand is far more likely to be
    a legacy import format (or a genuine corruption needing human eyes) than
    something safe to discard. Nullable-ness is irrelevant — silently writing
    NULL over a populated column is data loss, and a backup nobody knows to
    restore is not a safety net.
    """

    def __init__(self, table: str, column: str, rowid: int, raw_value: Any) -> None:
        self.table = table
        self.column = column
        self.rowid = rowid
        self.raw_value = raw_value
        super().__init__(
            "Malformed datetime value; repair aborted to preserve data: "
            f"table={table!r}, column={column!r}, rowid={rowid!r}, raw_value={raw_value!r}. "
            "No rows were modified. Normalize or remove this value manually "
            "(the backup mechanism does not run because nothing was changed), "
            "then restart."
        )


def repair_database_datetimes(db_path: Path | str, backup: bool = True) -> dict[str, int]:
    """Inspect and repair non-canonical datetime values across all SQLite tables.

    Fail-closed and two-phase:

      SCAN  — plan every update and raise MalformedDatetimeError on the first
              value that cannot be parsed. No mutation has happened yet, so the
              abort leaves the database byte-for-byte unchanged.
      APPLY — back up once, then write every planned update in a single
              transaction that rolls back entirely on any failure.

    Only mutates rows that actually deviate from the canonical format.
    Idempotent: running on a clean database does 0 updates and creates no backup.
    """
    db_path = Path(db_path)
    if not db_path.exists() or db_path.stat().st_size == 0:
        return {"repaired_rows": 0, "tables_scanned": 0}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]

        columns_to_check: list[tuple[str, str, bool]] = []
        for table in tables:
            cur.execute(f"PRAGMA table_info({table})")
            cols = cur.fetchall()
            for col in cols:
                cid, col_name, col_type, notnull, dflt_value, pk = col
                c_upper = col_type.upper()
                name_lower = col_name.lower()
                is_dt = (
                    "DATETIME" in c_upper
                    or "TIMESTAMP" in c_upper
                    or name_lower.endswith("_at")
                    or name_lower.endswith("_date")
                    or name_lower in ("upload_date", "publish_time", "expiry")
                )
                # Skip non-datetime fields that happen to end in _at
                if is_dt and name_lower not in ("format", "concat"):
                    columns_to_check.append((table, col_name, bool(notnull)))

        # ---- PHASE 1: SCAN / PLAN -------------------------------------------------
        # Read-only. Raising here guarantees zero mutations, so a malformed value
        # discovered late in the scan can never leave earlier rows half-repaired.
        rows_to_update: list[tuple[str, str, Any, str]] = []
        for table, col_name, notnull in columns_to_check:
            cur.execute(f"SELECT rowid, {col_name}, typeof({col_name}) FROM {table}")
            rows = cur.fetchall()
            for r in rows:
                rowid, val, col_typeof = r[0], r[1], r[2]
                if col_typeof == "null":
                    continue
                if col_typeof in ("integer", "real"):
                    canonical = to_canonical_datetime_str(val)
                    if canonical is None:
                        raise MalformedDatetimeError(table, col_name, rowid, val)
                    rows_to_update.append((table, col_name, rowid, canonical))
                elif col_typeof == "text":
                    text_val = str(val).strip()
                    if not _CANONICAL_RE.match(text_val):
                        canonical = to_canonical_datetime_str(text_val)
                        if canonical is None:
                            raise MalformedDatetimeError(table, col_name, rowid, val)
                        rows_to_update.append((table, col_name, rowid, canonical))

        if not rows_to_update:
            return {"repaired_rows": 0, "tables_scanned": len(tables)}

        # ---- PHASE 2: APPLY -------------------------------------------------------
        # Reached only when the whole scan parsed cleanly, so the backup is taken
        # exactly once and only for a repair that will actually happen.
        if backup:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_path = db_path.with_name(f"{db_path.name}.backup_before_repair_{ts}")
            try:
                _backup_sqlite_database(conn, backup_path)
                logger.info("Created safety backup before datetime repair: %s", backup_path)
            except Exception as exc:
                logger.error("Failed to create database backup before repair: %s", exc)
                raise RuntimeError(f"Database backup failed before datetime repair: {exc}") from exc

        try:
            cur.execute("BEGIN TRANSACTION")
            for table, col_name, rowid, canonical in rows_to_update:
                cur.execute(f"UPDATE {table} SET {col_name} = ? WHERE rowid = ?", (canonical, rowid))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        logger.info("Repaired %d datetime rows across %d tables in %s", len(rows_to_update), len(tables), db_path)
        return {"repaired_rows": len(rows_to_update), "tables_scanned": len(tables)}
    finally:
        conn.close()
