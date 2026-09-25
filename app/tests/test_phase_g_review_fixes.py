"""Regression coverage for independent Phase-G review findings."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.affiliate.radar.service import observe_scout_rows
from backend.affiliate.radar.store import get_conn
from backend.agents.scout import ScoutAgent
from backend.database import AsyncSessionLocal, init_db
from backend.models.scout_result import ScoutResult


ROOT = Path(__file__).resolve().parents[2]


def test_radar_store_fresh_db_matches_canonical_scout_model(tmp_path):
    db_path = tmp_path / "fresh.db"
    conn = get_conn(db_path)
    try:
        actual = {
            row[1]
            for row in conn.execute("PRAGMA table_info(scout_results)").fetchall()
        }
    finally:
        conn.close()

    expected = {column.name for column in ScoutResult.__table__.columns}
    assert actual == expected


@pytest.mark.parametrize(
    "args",
    [
        ["stats"],
        ["creators", "--top", "0"],
    ],
)
def test_radar_cli_commands_work_on_canonical_schema(tmp_path, args):
    env = os.environ.copy()
    env["VIRALMINT_DATA_DIR"] = str(tmp_path)
    env["CONTENT_ENGINE_ROOT"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, str(ROOT / "radar.py"), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_supported_python_launcher_pins_repo_root_data_contract():
    source = (ROOT / "app" / "run.py").read_text(encoding="utf-8")
    assert 'os.environ.setdefault("VIRALMINT_DATA_DIR", str(REPO_ROOT))' in source
    assert 'storage_root = Path(os.environ["VIRALMINT_DATA_DIR"]) / "storage"' in source


def test_startup_wires_phase_g_migration_before_runtime_schema_init():
    source = (ROOT / "app" / "backend" / "database.py").read_text(encoding="utf-8")
    init_pos = source.index("async def init_db():")
    migrate_pos = source.index("await _run_phase_g_data_migration_if_needed()", init_pos)
    import_pos = source.index("# Import all models so Base knows about them", init_pos)
    assert init_pos < migrate_pos < import_pos


@pytest.mark.asyncio
async def test_scout_save_enriches_identity_precreated_by_radar():
    await init_db()
    row = {
        "platform": "bilibili",
        "video_id": "phase-g-metadata",
        "video_url": "https://www.bilibili.com/video/phase-g-metadata",
        "embed_url": "https://player.example/embed",
        "creator_id": "creator-42",
        "author": "Creator 42",
        "author_url": "https://space.bilibili.com/42",
        "title": "Canonical metadata",
        "description": "desc",
        "thumbnail_url": "https://img.example/thumb.jpg",
        "views": 1234,
        "likes": 123,
        "comments": 12,
        "shares": 3,
        "favorites": 4,
        "duration_seconds": 67,
        "virality_score": 88.5,
        "views_per_hour": 321.0,
        "outlier_score": 4.2,
        "subscriber_count": 9000,
        "channel_avg_views": 200,
    }

    # Phase-G ordering: Radar creates/observes the canonical identity before
    # _save_results resolves the full Scout row.
    observe_scout_rows([row], "first discovery")

    display, new_count = await ScoutAgent()._save_results(
        [row], "job-phase-g", "first discovery", "local"
    )
    assert new_count == 0
    assert display[0]["video_id"] == "phase-g-metadata"

    async with AsyncSessionLocal() as session:
        saved = (
            await session.execute(
                select(ScoutResult).where(
                    ScoutResult.user_id == "local",
                    ScoutResult.platform == "bilibili",
                    ScoutResult.video_id == "phase-g-metadata",
                )
            )
        ).scalar_one()

    assert saved.job_id == "job-phase-g"
    assert saved.embed_url == row["embed_url"]
    assert saved.creator_id == row["creator_id"]
    assert saved.author_url == row["author_url"]
    assert saved.thumbnail_url == row["thumbnail_url"]
    assert saved.duration_seconds == 67
    assert saved.virality_score == 88.5
    assert saved.views_per_hour == 321.0
    assert saved.outlier_score == 4.2
    assert saved.subscriber_count == 9000
    assert saved.channel_avg_views == 200
