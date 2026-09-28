# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Datetime repair must fail closed — never invent data.

`repair_database_datetimes` runs on every `init_db()`. It used to treat a
nullable datetime it could not parse as "convert to NULL":

    if canonical is None:
        if notnull:
            raise ValueError(...)
        else:
            logger.warning("Converting unparseable nullable datetime to NULL")
            canonical = None      # <-- the value is destroyed here

So a populated `created_at` in a legacy import format silently became SQL NULL
on the next boot, and a NOT NULL column in the *same table* raised only by
accident. Nullable-ness decided whether data survived, which is exactly the
wrong axis: the question is "can I understand this value", not "is the column
optional".

These tests pin the fail-closed contract:
  - a non-null unparseable value raises and is preserved byte-for-byte
  - the abort happens during SCAN, so parseable rows are NOT half-repaired
  - no backup is created for a repair that never happens
  - everything that parsed cleanly still normalizes exactly as before
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.migrations.repair_datetime_types import (  # noqa: E402
    CANONICAL_DATETIME_FORMAT,
    MalformedDatetimeError,
    repair_database_datetimes,
    to_canonical_datetime_str,
)


# --- helpers -----------------------------------------------------------------

def _make_db(path: Path, schema: str, rows: list[tuple]) -> Path:
    conn = sqlite3.connect(str(path))
    conn.executescript(schema)
    if rows:
        conn.executemany("INSERT INTO t VALUES (?, ?)", rows)
    conn.commit()
    conn.close()
    return path


def _read(path: Path, column: str = "value"):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(f"SELECT id, {column} FROM t ORDER BY id").fetchall()
    finally:
        conn.close()


def _backups(path: Path):
    return list(path.parent.glob(f"{path.name}.backup_before_repair_*"))


ONE_COL = "CREATE TABLE t (id INTEGER PRIMARY KEY, value DATETIME)"
NOT_NULL_COL = "CREATE TABLE t (id INTEGER PRIMARY KEY, value DATETIME NOT NULL)"


# --- 1. values that are fine must be left completely alone --------------------

def test_null_datetime_is_unchanged(tmp_path):
    db = _make_db(tmp_path / "a.db", ONE_COL, [(1, None)])
    result = repair_database_datetimes(db, backup=True)
    assert result["repaired_rows"] == 0
    assert _read(db) == [(1, None)]


def test_canonical_datetime_is_unchanged(tmp_path):
    canonical = datetime_str = "2026-09-28 09:12:13.000000"
    db = _make_db(tmp_path / "b.db", ONE_COL, [(1, canonical)])
    result = repair_database_datetimes(db, backup=True)
    assert result["repaired_rows"] == 0
    assert _read(db) == [(1, canonical)]
    assert canonical == datetime_str


def test_already_canonical_creates_no_backup(tmp_path):
    db = _make_db(tmp_path / "c.db", ONE_COL, [(1, "2026-09-28 09:12:13.000000")])
    repair_database_datetimes(db, backup=True)
    assert _backups(db) == [], "a no-op repair must not litter the data dir with backups"


# --- 2. parseable values still normalize (nullable AND not null) ---------------

def test_parseable_nullable_is_repaired(tmp_path):
    db = _make_db(tmp_path / "d.db", ONE_COL, [(1, "2026-09-28T09:12:13Z")])
    result = repair_database_datetimes(db, backup=True)
    assert result["repaired_rows"] == 1
    assert _read(db) == [(1, "2026-09-28 09:12:13.000000")]
    assert len(_backups(db)) == 1, "a real repair must leave exactly one backup"


def test_parseable_not_null_is_repaired(tmp_path):
    db = _make_db(tmp_path / "e.db", NOT_NULL_COL, [(1, 1700000000)])
    result = repair_database_datetimes(db, backup=True)
    assert result["repaired_rows"] == 1
    assert _read(db) == [(1, "2023-11-14 22:13:20.000000")]


# --- 3. THE BUG: malformed values must never be silently converted ------------

def test_malformed_nullable_raises_and_preserves_raw_value(tmp_path):
    db = _make_db(tmp_path / "f.db", ONE_COL, [(1, "weird-human-date")])

    with pytest.raises(MalformedDatetimeError):
        repair_database_datetimes(db, backup=True)

    assert _read(db) == [(1, "weird-human-date")], "the raw value must survive untouched"


def test_malformed_not_null_raises_and_preserves_raw_value(tmp_path):
    db = _make_db(tmp_path / "g.db", NOT_NULL_COL, [(1, "28/09/2026 14h30")])

    with pytest.raises(MalformedDatetimeError):
        repair_database_datetimes(db, backup=True)

    assert _read(db) == [(1, "28/09/2026 14h30")]


def test_malformed_error_names_the_exact_offending_cell(tmp_path):
    db = _make_db(tmp_path / "h.db", ONE_COL, [(7, "nope")])

    with pytest.raises(MalformedDatetimeError) as excinfo:
        repair_database_datetimes(db, backup=True)

    err = excinfo.value
    assert err.table == "t"
    assert err.column == "value"
    assert err.rowid == 7
    assert err.raw_value == "nope"
    # The message must be actionable and must not truncate the raw value.
    message = str(err)
    assert "table='t'" in message
    assert "column='value'" in message
    assert "rowid=7" in message
    assert "nope" in message
    assert "No rows were modified" in message


def test_malformed_does_not_create_a_backup(tmp_path):
    db = _make_db(tmp_path / "i.db", ONE_COL, [(1, "weird-human-date")])
    with pytest.raises(MalformedDatetimeError):
        repair_database_datetimes(db, backup=True)
    assert _backups(db) == [], "nothing was mutated, so no backup should exist"


# --- 4. atomicity: the abort must precede every write -------------------------

def test_mixed_parseable_and_malformed_leaves_every_row_unchanged(tmp_path):
    """The critical test: proves the repair plans before it writes.

    Row 1 parses fine and is a legitimate repair target. Row 2 is malformed.
    If the implementation wrote as it scanned, row 1 would be canonicalized
    before row 2 raised — a silent partial repair.
    """
    db = _make_db(
        tmp_path / "j.db",
        ONE_COL,
        [(1, "2026-09-28T09:12:13Z"), (2, "weird-human-date")],
    )

    with pytest.raises(MalformedDatetimeError):
        repair_database_datetimes(db, backup=True)

    rows = _read(db)
    assert rows == [
        (1, "2026-09-28T09:12:13Z"),
        (2, "weird-human-date"),
    ], f"partial repair detected: {rows}"
    assert _backups(db) == []


def test_malformed_in_second_column_blocks_repair_in_first(tmp_path):
    """A clean value in column A does not excuse a malformed value in column B."""
    db = tmp_path / "k.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, first_at DATETIME, second_at DATETIME)"
    )
    conn.execute("INSERT INTO t VALUES (1, '2026-09-28T09:12:13Z', 'garbage')")
    conn.commit()
    conn.close()

    with pytest.raises(MalformedDatetimeError):
        repair_database_datetimes(db, backup=True)

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT first_at, second_at FROM t").fetchone()
    finally:
        conn.close()
    assert row == ("2026-09-28T09:12:13Z", "garbage")


def test_malformed_in_second_table_blocks_repair_in_first(tmp_path):
    """Same guarantee across tables: nothing is repaired anywhere."""
    db = tmp_path / "l.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE aaa (id INTEGER PRIMARY KEY, created_at DATETIME);
        CREATE TABLE zzz (id INTEGER PRIMARY KEY, created_at DATETIME);
        """
    )
    conn.execute("INSERT INTO aaa VALUES (1, '2026-09-28T09:12:13Z')")
    conn.execute("INSERT INTO zzz VALUES (1, 'garbage')")
    conn.commit()
    conn.close()

    with pytest.raises(MalformedDatetimeError) as excinfo:
        repair_database_datetimes(db, backup=True)

    assert excinfo.value.table == "zzz"
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT created_at FROM aaa").fetchone() == ("2026-09-28T09:12:13Z",)
    finally:
        conn.close()


# --- 5. format compatibility must not regress ---------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-09-28 09:12:13.000000", "2026-09-28 09:12:13.000000"),  # canonical
        ("2026-09-28T09:12:13", "2026-09-28 09:12:13.000000"),        # ISO naive
        ("2026-09-28T09:12:13Z", "2026-09-28 09:12:13.000000"),       # Z suffix
        ("2026-09-28T09:12:13+00:00", "2026-09-28 09:12:13.000000"),  # ISO tz
        ("2026-09-28T11:12:13+02:00", "2026-09-28 09:12:13.000000"),  # tz -> UTC-naive
        (1787997133, "2026-08-29 09:52:13.000000"),                   # epoch seconds
        (1787997133000, "2026-08-29 09:52:13.000000"),                # epoch ms
        ("1787997133", "2026-08-29 09:52:13.000000"),                 # numeric string
        ("2026-09-28 09:12:13", "2026-09-28 09:12:13.000000"),        # no microseconds
        ("2026-09-28", "2026-09-28 00:00:00.000000"),                 # date only
    ],
)
def test_accepted_formats_still_normalize(raw, expected):
    assert to_canonical_datetime_str(raw) == expected


def test_timezone_aware_values_normalize_to_utc_naive():
    got = to_canonical_datetime_str("2026-09-28T11:12:13+02:00")
    assert got == "2026-09-28 09:12:13.000000"
    assert got.endswith("." + "0" * 6)


# --- 6. idempotency + recovery ------------------------------------------------

def test_rerun_after_clean_repair_is_idempotent(tmp_path):
    db = _make_db(tmp_path / "m.db", ONE_COL, [(1, "2026-09-28T09:12:13Z")])
    first = repair_database_datetimes(db, backup=True)
    assert first["repaired_rows"] == 1

    second = repair_database_datetimes(db, backup=True)
    assert second["repaired_rows"] == 0
    assert _read(db) == [(1, "2026-09-28 09:12:13.000000")]


def test_repair_succeeds_once_the_malformed_row_is_fixed(tmp_path):
    """The operator's recovery path: fix the value, restart, repair proceeds."""
    db = _make_db(
        tmp_path / "n.db",
        ONE_COL,
        [(1, "2026-09-28T09:12:13Z"), (2, "weird-human-date")],
    )
    with pytest.raises(MalformedDatetimeError):
        repair_database_datetimes(db, backup=True)

    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE t SET value='2026-09-28T10:00:00Z' WHERE id=2")
    conn.commit()
    conn.close()

    result = repair_database_datetimes(db, backup=True)
    assert result["repaired_rows"] == 2
    assert _read(db) == [
        (1, "2026-09-28 09:12:13.000000"),
        (2, "2026-09-28 10:00:00.000000"),
    ]


def test_malformed_value_is_still_detected_after_a_successful_repair(tmp_path):
    """A previous clean run must not make a later malformed value pass silently."""
    db = _make_db(tmp_path / "o.db", ONE_COL, [(1, "2026-09-28T09:12:13Z")])
    repair_database_datetimes(db, backup=True)

    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO t VALUES (2, 'later-corruption')")
    conn.commit()
    conn.close()

    with pytest.raises(MalformedDatetimeError):
        repair_database_datetimes(db, backup=True)

    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT value FROM t WHERE id=2").fetchone() == ("later-corruption",)
    finally:
        conn.close()


# --- 7. the real startup path must not swallow the failure --------------------

def test_init_db_propagates_malformed_datetime_failure(tmp_path, monkeypatch):
    """`init_db()` must fail closed too — a try/except there would defeat this fix.

    `init_db` does `from backend.config import DATA_DIR` inside the function
    body, so the patch target is the `backend.config` module attribute, not
    `backend.database`.
    """
    import asyncio

    from backend import config as config_module
    from backend import database as db_module

    bad = _make_db(tmp_path / "viralmint.db", ONE_COL, [(1, "weird-human-date")])
    monkeypatch.setattr(config_module, "DATA_DIR", tmp_path)

    # Guard the assumption this test rests on: the repair must run before the
    # ORM creates/alters tables, otherwise the failure we assert could come
    # from an unrelated startup error.
    source = __import__("inspect").getsource(db_module.init_db)
    repair_at = source.index("repair_database_datetimes")
    orm_at = source.index("_run_phase_g_data_migration_if_needed")
    assert repair_at < orm_at, "repair must precede the ORM migration"

    with pytest.raises(MalformedDatetimeError):
        asyncio.run(db_module.init_db())

    assert _read(bad) == [(1, "weird-human-date")]


def test_init_db_succeeds_on_a_repairable_database(tmp_path, monkeypatch):
    """A clean or repairable database must still boot normally."""
    import asyncio

    from backend import config as config_module
    from backend import database as db_module

    good = _make_db(tmp_path / "viralmint.db", ONE_COL, [(1, "2026-09-28T09:12:13Z")])
    monkeypatch.setattr(config_module, "DATA_DIR", tmp_path)

    asyncio.run(db_module.init_db())

    assert _read(good) == [(1, "2026-09-28 09:12:13.000000")]
