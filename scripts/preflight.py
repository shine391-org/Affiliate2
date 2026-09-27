#!/usr/bin/env python3
"""Affiliate2 V1 release pre-flight checks (standard library only)."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Mapping, NamedTuple, Sequence


class CheckReport(NamedTuple):
    errors: list[str]
    warnings: list[str]
    info: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


REQUIRED_FILES = (
    "app/requirements.txt",
    "app/frontend/package.json",
    "app/frontend/package-lock.json",
    ".env.example",
)
REQUIRED_DIRS = (
    "storage/videos",
    "storage/audio",
    "storage/generated",
    "storage/thumbnails",
    "storage/tmp",
    "storage/tools/in",
    "storage/tools/out",
)


def command_version(command: Sequence[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            list(command), capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (result.stdout or result.stderr or "").strip().splitlines()
    return result.returncode == 0, (output[0] if output else "no version output")


def _major(version_text: str) -> int | None:
    match = re.search(r"(\d+)", version_text)
    return int(match.group(1)) if match else None


def _load_env_file(path: Path, base: Mapping[str, str]) -> dict[str, str]:
    values = dict(base)
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return values


def _probe_url(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status < 500, f"HTTP {response.status}"
    except (OSError, urllib.error.URLError) as exc:
        return False, str(exc)


def _is_loopback_url(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def run_checks(*, root: Path, environ: Mapping[str, str] | None = None) -> CheckReport:
    root = root.resolve()
    env = _load_env_file(root / ".env", environ or os.environ)
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    if sys.version_info[:2] != (3, 11):
        errors.append(
            f"Python 3.11.x is required for the V1 lock; running {sys.version.split()[0]}."
        )
    else:
        info.append(f"Python {sys.version.split()[0]}")

    node_cmd = shutil.which("node")
    if not node_cmd:
        errors.append("Node.js 22+ was not found on PATH.")
    else:
        ok, text = command_version([node_cmd, "--version"])
        if not ok or (_major(text) or 0) < 22:
            errors.append(f"Node.js 22+ is required; detected {text}.")
        else:
            info.append(f"Node.js {text}")

    npm_cmd = shutil.which("npm")
    if not npm_cmd:
        errors.append("npm 10+ was not found on PATH.")
    else:
        ok, text = command_version([npm_cmd, "--version"])
        if not ok or (_major(text) or 0) < 10:
            errors.append(f"npm 10+ is required; detected {text}.")
        else:
            info.append(f"npm {text}")

    for executable, label in (("ffmpeg", "FFmpeg"), ("ffprobe", "ffprobe")):
        path = shutil.which(executable)
        if not path:
            errors.append(f"{label} was not found on PATH.")
            continue
        ok, text = command_version([path, "-version"])
        if not ok:
            errors.append(f"{label} exists but could not run: {text}")
        else:
            info.append(text)

    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"Required release file is missing: {relative}")

    data_root = Path(env.get("VIRALMINT_DATA_DIR") or root).expanduser().resolve()
    try:
        data_root.mkdir(parents=True, exist_ok=True)
        for relative in REQUIRED_DIRS:
            directory = data_root / relative
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".affiliate2-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        info.append(f"Writable runtime data directory: {data_root}")
    except OSError as exc:
        errors.append(f"Runtime data directory is not writable ({data_root}): {exc}")

    ai_base_url = env.get("AI_BASE_URL", "").rstrip("/")
    ai_provider = env.get("AI_PROVIDER", "").lower().strip()
    if ai_provider and ai_base_url and _is_loopback_url(ai_base_url):
        ok, detail = _probe_url(f"{ai_base_url}/models")
        message = f"Configured AI endpoint {ai_base_url}: {detail}"
        (info if ok else warnings).append(message)

    cdp_url = (
        env.get("AFFILIATE2_DOUYIN_CDP_URL")
        or env.get("DOUYIN_CDP_URL")
        or ""
    ).rstrip("/")
    if cdp_url:
        ok, detail = _probe_url(f"{cdp_url}/json/version")
        message = f"Configured Douyin CDP endpoint {cdp_url}: {detail}"
        (info if ok else warnings).append(message)

    if not (root / ".env").is_file():
        warnings.append("No .env exists yet; setup will copy .env.example and the app will generate local secrets.")

    return CheckReport(errors=errors, warnings=warnings, info=info)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    report = run_checks(root=args.root)

    for item in report.info:
        print(f"[OK] {item}")
    for item in report.warnings:
        print(f"[WARN] {item}")
    for item in report.errors:
        print(f"[ERROR] {item}")
    print("PREFLIGHT PASS" if report.ok else "PREFLIGHT FAIL")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
