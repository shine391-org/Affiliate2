"""Phase-G runtime must not fall back to writable legacy persistence."""
from __future__ import annotations

from pathlib import Path

from backend.core.plugins import load_overlay


ROOT = Path(__file__).resolve().parents[2]


def test_overlay_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VIRALMINT_OVERLAY", raising=False)
    assert load_overlay() is None


def test_managed_launchers_do_not_enable_legacy_overlay():
    for relative in ("scripts/dev.ps1", "scripts/run.ps1"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "$env:VIRALMINT_OVERLAY" not in source
        assert "viralmint_overlay" not in source


def test_supported_radar_cli_has_no_overlay_or_legacy_database_owner():
    cli = (ROOT / "radar.py").read_text(encoding="utf-8")
    store = (ROOT / "src/radar/db.py").read_text(encoding="utf-8")
    ingest = (ROOT / "src/radar/ingest.py").read_text(encoding="utf-8")

    assert "viralmint_overlay" not in cli
    assert "data/radar.db" not in store
    assert "CREATE TABLE IF NOT EXISTS content" not in store
    assert "INSERT INTO content" not in ingest
    assert "backend.affiliate.radar" in store
    assert "from .db import get_conn" in ingest


def test_migrated_legacy_runtime_packages_are_removed():
    removed_files = (
        "app/frontend/src/plugins/index.jsx",
        "scripts/sync_review_overlay.py",
    )
    for relative in removed_files:
        assert not (ROOT / relative).is_file(), relative

    removed_source_dirs = (
        "viralmint_overlay",
        "app/viralmint_overlay",
        "overlay/frontend",
        "app/frontend/src/plugins/review",
    )
    for relative in removed_source_dirs:
        path = ROOT / relative
        assert not any(path.rglob("*.py")), relative
        assert not any(path.rglob("*.jsx")), relative

    assert (ROOT / "app/frontend/src/plugins/performance/Performance.jsx").is_file()
