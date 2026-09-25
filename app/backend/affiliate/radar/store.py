"""Canonical application-DB connection for Radar observations.

The ORM models own the schema. The synchronous Radar compatibility surface
bootstraps those exact tables from Base.metadata so running radar.py before the
FastAPI app cannot create a narrower, incompatible scout_results table.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable

from backend.config import DATA_DIR
from backend.database import Base
from backend.models.phase_g import RadarObservation, RadarSourceRun
from backend.models.scout_result import ScoutResult


DB_PATH = DATA_DIR / "viralmint.db"
_RADAR_TABLES = (
    ScoutResult.__table__,
    RadarObservation.__table__,
    RadarSourceRun.__table__,
)


def _compat_ddl() -> str:
    """DDL for in-memory legacy tests, generated from the canonical models."""
    dialect = sqlite_dialect.dialect()
    return ";\n".join(
        str(CreateTable(table).compile(dialect=dialect))
        for table in _RADAR_TABLES
    ) + ";"


# Kept only for the historical in-memory Radar tests. Runtime schema ownership
# remains Base.metadata via _ensure_schema().
DDL = _compat_ddl()


def _ensure_schema(path: Path) -> None:
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        Base.metadata.create_all(engine, tables=list(_RADAR_TABLES))
    finally:
        engine.dispose()


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_schema(path)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn
