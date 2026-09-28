# SPDX-License-Identifier: AGPL-3.0-only
"""Release packaging contracts: health probe and Windows pre-flight checks."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from starlette.testclient import TestClient

from backend.main import create_app


ROOT = Path(__file__).resolve().parents[2]
BUILD_RELEASE = ROOT / "scripts" / "build_release.py"
BOOTSTRAP = ROOT / "scripts" / "bootstrap.ps1"
PREFLIGHT = ROOT / "scripts" / "preflight.py"
RUN_SCRIPT = ROOT / "scripts" / "run.ps1"


def _load_preflight():
    spec = importlib.util.spec_from_file_location("affiliate2_preflight", PREFLIGHT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_build_release():
    spec = importlib.util.spec_from_file_location("affiliate2_build_release", BUILD_RELEASE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_health_endpoint_is_machine_readable():
    client = TestClient(create_app())
    expected = {
        "status": "ok",
        "app": "Affiliate2",
        "version": "1.0.0",
    }
    for path in ("/api/health", "/health"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == expected


def test_stable_run_honors_data_and_content_roots_from_env():
    script = RUN_SCRIPT.read_text(encoding="utf-8")
    assert "VIRALMINT_DATA_DIR\\s*=\\s*(.+?)" in script
    assert "CONTENT_ENGINE_ROOT\\s*=\\s*(.+?)" in script
    assert '$env:VIRALMINT_DATA_DIR = $DataRoot' in script
    assert '$env:CONTENT_ENGINE_ROOT = $ContentRoot' in script
    assert '$env:VIRALMINT_DATA_DIR = $RepoRoot' not in script


def test_release_builder_packages_bootstrap_cuda_helper(tmp_path):
    build_release = _load_build_release()
    release = tmp_path / build_release.RELEASE_NAME

    build_release.build_directory(ROOT, release)

    assert "cuda_torch.py" in build_release.SCRIPT_FILES
    for filename in (
        "bootstrap.ps1",
        "cuda_torch.py",
        "preflight.py",
        "run.ps1",
        "stop.ps1",
    ):
        assert (release / "scripts" / filename).is_file()
    assert "cuda_torch.py" in BOOTSTRAP.read_text(encoding="utf-8")


def test_preflight_requires_bootstrap_cuda_helper():
    preflight = _load_preflight()

    assert "scripts/cuda_torch.py" in preflight.REQUIRED_FILES


def test_preflight_reports_missing_mandatory_tools(monkeypatch, tmp_path):
    preflight = _load_preflight()
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: None)

    report = preflight.run_checks(root=tmp_path, environ={})

    assert report.ok is False
    assert any("Node.js" in item for item in report.errors)
    assert any("npm" in item for item in report.errors)
    assert any("FFmpeg" in item for item in report.errors)
    assert any("ffprobe" in item for item in report.errors)


def test_preflight_accepts_the_active_python_311_without_python_on_path(monkeypatch, tmp_path):
    preflight = _load_preflight()
    for relative in preflight.REQUIRED_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder", encoding="utf-8")

    def fake_which(name):
        if name in {"python", "py"}:
            return None
        return f"C:/tools/{name}.exe"

    monkeypatch.setattr(preflight.shutil, "which", fake_which)
    monkeypatch.setattr(
        preflight,
        "command_version",
        lambda command: (
            True,
            "v22.0.0" if "node" in command[0] else "10.0.0" if "npm" in command[0] else "test-version",
        ),
    )

    report = preflight.run_checks(root=tmp_path, environ={})

    assert not any("Python" in item for item in report.errors)


def test_preflight_creates_required_writable_directories(monkeypatch, tmp_path):
    preflight = _load_preflight()
    for relative in preflight.REQUIRED_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: f"C:/tools/{name}.exe")
    monkeypatch.setattr(
        preflight,
        "command_version",
        lambda command: (
            True,
            "v22.0.0" if "node" in command[0] else "10.0.0" if "npm" in command[0] else "test-version",
        ),
    )

    report = preflight.run_checks(
        root=tmp_path,
        environ={"VIRALMINT_DATA_DIR": str(tmp_path / "runtime-data")},
    )

    assert report.ok is True
    assert not report.errors
    for relative in (
        "storage/videos",
        "storage/audio",
        "storage/generated",
        "storage/thumbnails",
        "storage/tmp",
        "storage/tools/in",
        "storage/tools/out",
    ):
        assert (tmp_path / "runtime-data" / relative).is_dir()


def test_preflight_cli_returns_nonzero_for_missing_release_files(tmp_path):
    result = subprocess.run(
        [sys.executable, str(PREFLIGHT), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": ""},
        check=False,
    )
    assert result.returncode == 1
    assert "PREFLIGHT FAIL" in result.stdout
