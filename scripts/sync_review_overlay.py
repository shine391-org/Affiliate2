#!/usr/bin/env python3
"""Stage the Content Engine review overlay into the ignored local ViralMint clone.

This follows ViralMint's documented overlay seam:
- backend package is copied to tools/ViralMint/viralmint_overlay
- frontend plugin registry + ReviewQueue page are copied into frontend/src/plugins

The tracked source of truth remains in this repository.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIRALMINT = ROOT / "tools" / "ViralMint"


def _registry_kind(path: Path) -> str:
    """Classify a ViralMint frontend plugin registry without mutating it."""
    if not path.exists():
        return "missing"
    text = path.read_text(encoding="utf-8")
    if (
        "export const pluginRoutes = []" in text
        and "export const pluginNavItems = []" in text
    ):
        return "stub"
    if "CONTENT_ENGINE_REVIEW_OVERLAY" in text:
        return "ours"
    return "custom"


def sync(force: bool = False) -> None:
    if not (VIRALMINT / "backend" / "core" / "plugins.py").exists():
        raise SystemExit("ViralMint clone not found at tools/ViralMint")

    backend_src = ROOT / "viralmint_overlay"
    backend_dst = VIRALMINT / "viralmint_overlay"
    shutil.copytree(
        backend_src,
        backend_dst,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    plugin_src = ROOT / "overlay" / "frontend" / "plugins.jsx"
    page_src = ROOT / "overlay" / "frontend" / "review" / "ReviewQueue.jsx"
    controls_src = ROOT / "overlay" / "frontend" / "review" / "ActionControls.jsx"
    publish_src = ROOT / "overlay" / "frontend" / "review" / "PublishGate.jsx"
    performance_src = ROOT / "overlay" / "frontend" / "performance" / "Performance.jsx"
    plugin_dst = VIRALMINT / "frontend" / "src" / "plugins" / "index.jsx"
    page_dst = VIRALMINT / "frontend" / "src" / "plugins" / "review" / "ReviewQueue.jsx"
    controls_dst = VIRALMINT / "frontend" / "src" / "plugins" / "review" / "ActionControls.jsx"
    publish_dst = VIRALMINT / "frontend" / "src" / "plugins" / "review" / "PublishGate.jsx"
    performance_dst = VIRALMINT / "frontend" / "src" / "plugins" / "performance" / "Performance.jsx"
    page_dst.parent.mkdir(parents=True, exist_ok=True)
    performance_dst.parent.mkdir(parents=True, exist_ok=True)

    # Vite resolves index.js before index.jsx, so the upstream OSS stub must be
    # removed after we stage the JSX plugin registry. But never delete a custom
    # index.js silently: downstream ViralMint installs may already use the plugin seam.
    old_js = VIRALMINT / "frontend" / "src" / "plugins" / "index.js"

    old_js_kind = _registry_kind(old_js)
    jsx_kind = _registry_kind(plugin_dst)

    conflicts = []
    if old_js_kind == "custom":
        conflicts.append("frontend/src/plugins/index.js")
    if jsx_kind == "custom":
        conflicts.append("frontend/src/plugins/index.jsx")

    if conflicts and not force:
        joined = ", ".join(conflicts)
        raise SystemExit(
            f"Custom ViralMint plugin registry detected at {joined}. "
            "Refusing to overwrite/delete it; merge overlay/frontend/plugins.jsx "
            "manually or rerun with --force only after reviewing the conflict."
        )

    if old_js.exists():
        old_js.unlink()

    shutil.copy2(plugin_src, plugin_dst)
    shutil.copy2(page_src, page_dst)
    shutil.copy2(controls_src, controls_dst)
    shutil.copy2(publish_src, publish_dst)
    shutil.copy2(performance_src, performance_dst)
    print("Review/action/publish/performance overlay synced into tools/ViralMint")


def build_frontend() -> None:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise SystemExit("npm not found")
    subprocess.run([npm, "run", "build"], cwd=VIRALMINT / "frontend", check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="overwrite a custom plugin registry")
    parser.add_argument("--build", action="store_true", help="run ViralMint frontend build after sync")
    args = parser.parse_args()
    sync(force=args.force)
    if args.build:
        build_frontend()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
