#!/usr/bin/env python3
"""Build the clean Affiliate2 V1 Windows source release and ZIP archive."""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
import zipfile
from pathlib import Path


VERSION = "1.0.0"
RELEASE_NAME = f"Affiliate2-v{VERSION}"
ROOT_FILES = (
    ".env.example",
    "README.md",
    "RELEASE.md",
    "setup.ps1",
    "start.ps1",
    "stop.ps1",
)
SCRIPT_FILES = (
    "bootstrap.ps1",
    "preflight.py",
    "run.ps1",
    "stop.ps1",
)
APP_FILES = (
    ".env.example",
    "LICENSE",
    "LEGAL.md",
    "launcher.py",
    "pyproject.toml",
    "requirements.txt",
    "requirements-lock.txt",
    "run.py",
)
EXCLUDED_DIR_NAMES = {
    ".git",
    ".github",
    ".pytest_cache",
    "__pycache__",
    "dist",
    "node_modules",
    "tests",
}
EXCLUDED_SUFFIXES = {
    ".db",
    ".log",
    ".mp3",
    ".mp4",
    ".mkv",
    ".mov",
    ".pyc",
    ".pyo",
    ".wav",
}
SECRET_FILE_PARTS = (
    "cookie",
    "credential",
    "private_key",
    "session.json",
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|gsk|hf)_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:xoxb|xapp)-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
)
MACHINE_PATH_PATTERNS = (
    re.compile(r"C:\\Users\\shine", re.IGNORECASE),
    re.compile(r"E:\\Affiliate ver 1", re.IGNORECASE),
    re.compile(r"E:/Affiliate ver 1", re.IGNORECASE),
    re.compile(r"/c/Users/shine", re.IGNORECASE),
)


def _ignored(path: Path) -> bool:
    lowered = {part.lower() for part in path.parts}
    if lowered & EXCLUDED_DIR_NAMES:
        return True
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    name = path.name.lower()
    if name == ".env" or name.startswith("qa_") or name.endswith(".test.js"):
        return True
    return False


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Required release file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"Required release directory is missing: {source}")
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if _ignored(relative) or not path.is_file():
            continue
        _copy_file(path, destination / relative)


def build_directory(root: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    for relative in ROOT_FILES:
        _copy_file(root / relative, destination / relative)
    for relative in SCRIPT_FILES:
        _copy_file(root / "scripts" / relative, destination / "scripts" / relative)
    for relative in APP_FILES:
        _copy_file(root / "app" / relative, destination / "app" / relative)

    _copy_tree(root / "app" / "backend", destination / "app" / "backend")
    _copy_tree(root / "app" / "frontend", destination / "app" / "frontend")


def scan_release(release_dir: Path) -> tuple[list[str], list[str]]:
    security_errors: list[str] = []
    machine_paths: list[str] = []
    for path in release_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(release_dir).as_posix()
        lowered = relative.lower()
        if path.name == ".env" or any(part in lowered for part in SECRET_FILE_PARTS):
            security_errors.append(f"forbidden secret/session filename: {relative}")
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            security_errors.append(f"forbidden runtime/binary artifact: {relative}")
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            security_errors.append(f"potential secret material: {relative}")
        if any(pattern.search(text) for pattern in MACHINE_PATH_PATTERNS):
            machine_paths.append(relative)
    return sorted(set(security_errors)), sorted(set(machine_paths))


def create_zip(release_dir: Path, zip_path: Path) -> str:
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(release_dir.rglob("*")):
            if path.is_file():
                archive.write(path, Path(release_dir.name) / path.relative_to(release_dir))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    zip_path.with_suffix(zip_path.suffix + ".sha256").write_text(
        f"{digest}  {zip_path.name}\n", encoding="ascii"
    )
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    output_root = (args.output or (root / "release")).resolve()
    release_dir = output_root / RELEASE_NAME
    zip_path = output_root / f"{RELEASE_NAME}.zip"
    output_root.mkdir(parents=True, exist_ok=True)

    build_directory(root, release_dir)
    secret_errors, machine_paths = scan_release(release_dir)
    if secret_errors or machine_paths:
        for item in secret_errors:
            print(f"[SECURITY ERROR] {item}")
        for item in machine_paths:
            print(f"[PORTABILITY ERROR] developer path found in {item}")
        print("RELEASE BUILD BLOCKED")
        return 1

    digest = create_zip(release_dir, zip_path)
    file_count = sum(1 for path in release_dir.rglob("*") if path.is_file())
    print(f"Release directory: {release_dir}")
    print(f"Files: {file_count}")
    print(f"ZIP: {zip_path}")
    print(f"SHA-256: {digest}")
    print("RELEASE BUILD PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
