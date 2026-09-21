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

    plugin_src = ROOT / "overlay" / "frontend" / "plugins.js"
    page_src = ROOT / "overlay" / "frontend" / "review" / "ReviewQueue.jsx"
    plugin_dst = VIRALMINT / "frontend" / "src" / "plugins" / "index.js"
    page_dst = VIRALMINT / "frontend" / "src" / "plugins" / "review" / "ReviewQueue.jsx"
    page_dst.parent.mkdir(parents=True, exist_ok=True)

    existing = plugin_dst.read_text(encoding="utf-8") if plugin_dst.exists() else ""
    is_stub = (
        "export const pluginRoutes = []" in existing
        and "export const pluginNavItems = []" in existing
    )
    is_ours = "CONTENT_ENGINE_REVIEW_OVERLAY" in existing
    if existing and not (is_stub or is_ours or force):
        raise SystemExit(
            "frontend/src/plugins/index.js has custom plugin content. "
            "Refusing to overwrite it; merge overlay/frontend/plugins.js manually "
            "or rerun with --force after reviewing."
        )

    shutil.copy2(plugin_src, plugin_dst)
    shutil.copy2(page_src, page_dst)
    print("Review overlay synced into tools/ViralMint")


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
