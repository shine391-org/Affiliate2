# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
import logging
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CANONICAL_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"

# Strict pattern for canonical format: YYYY-MM-DD HH:MM:SS.ffffff
_CANONICAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}$")


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


def repair_database_datetimes(db_path: Path | str, backup: bool = True) -> dict[str, int]:
    """Inspect and repair non-canonical datetime values across all SQLite tables.

    Only mutates rows that actually deviate from the canonical format.
    Idempotent: running on a clean database does 0 updates.
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

        rows_to_update: list[tuple[str, str, Any, str | None]] = []
        for table, col_name, notnull in columns_to_check:
            cur.execute(f"SELECT rowid, {col_name}, typeof({col_name}) FROM {table}")
            rows = cur.fetchall()
            for r in rows:
                rowid, val, col_typeof = r[0], r[1], r[2]
                if col_typeof == "null":
                    continue
                if col_typeof in ("integer", "real"):
                    canonical = to_canonical_datetime_str(val)
                    if not canonical and notnull:
                        canonical = datetime.fromtimestamp(0, tz=timezone.utc).replace(tzinfo=None).strftime(CANONICAL_DATETIME_FORMAT)
                    rows_to_update.append((table, col_name, rowid, canonical))
                elif col_typeof == "text":
                    text_val = str(val).strip()
                    if not _CANONICAL_RE.match(text_val):
                        canonical = to_canonical_datetime_str(text_val)
                        if canonical is None:
                            if notnull:
                                canonical = datetime.fromtimestamp(0, tz=timezone.utc).replace(tzinfo=None).strftime(CANONICAL_DATETIME_FORMAT)
                            else:
                                canonical = None
                        rows_to_update.append((table, col_name, rowid, canonical))

        if not rows_to_update:
            return {"repaired_rows": 0, "tables_scanned": len(tables)}

        if backup:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_path = db_path.with_name(f"{db_path.name}.backup_before_repair_{ts}")
            try:
                shutil.copy2(db_path, backup_path)
                logger.info("Created safety backup before datetime repair: %s", backup_path)
            except Exception as exc:
                logger.warning("Could not create DB backup before repair: %s", exc)

        for table, col_name, rowid, canonical in rows_to_update:
            cur.execute(f"UPDATE {table} SET {col_name} = ? WHERE rowid = ?", (canonical, rowid))

        conn.commit()
        logger.info("Repaired %d datetime rows across %d tables in %s", len(rows_to_update), len(tables), db_path)
        return {"repaired_rows": len(rows_to_update), "tables_scanned": len(tables)}
    finally:
        conn.close()
