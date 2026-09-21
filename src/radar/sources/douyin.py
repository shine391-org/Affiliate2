"""
sources/douyin.py — Douyin adapter for Radar Ingestion (Phase 2.1)

Strategy:
  1. Invoke MediaCrawler as a subprocess (proven working in Phase 1).
  2. Read the JSONL output file it produces.
  3. Normalize each record.

Requires:
  - Edge browser open with Douyin CDP session on port 9222
    (or MediaCrawler will open its own session and prompt for QR login)
  - MediaCrawler configured with SAVE_DATA_OPTION=jsonl
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from ..models import MetricSnapshot, NormalizedContent, SourceRunResult
from ..normalize import normalize_douyin

# Project root = 3 levels up from this file
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
MC_DIR = PROJECT_ROOT / "tools" / "MediaCrawler"
VENV_PY = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"


def _get_today_jsonl() -> Path:
    today = date.today().strftime("%Y-%m-%d")
    return MC_DIR / "data" / "douyin" / "jsonl" / f"search_contents_{today}.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def scan(keyword: str, max_count: int = 15, timeout: int = 300) -> SourceRunResult:
    """
    Run a Douyin keyword search via MediaCrawler and return normalized records.

    Args:
        keyword:   Search keyword (Chinese or any string)
        max_count: Maximum videos to fetch per keyword
        timeout:   Subprocess timeout in seconds

    Returns:
        SourceRunResult with status, counts, and normalized data
    """
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    jsonl_path = _get_today_jsonl()

    # Record lines before run so we can identify new records added by this scan
    lines_before = _count_jsonl_lines(jsonl_path)

    cmd = [
        str(VENV_PY),
        "main.py",
        "--platform", "dy",
        "--lt", "qrcode",
        "--type", "search",
        "--keywords", keyword,
        "--crawler_max_notes_count", str(max_count),
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(MC_DIR),
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**_base_env(), "PYTHONUTF8": "1"},
        )
    except subprocess.TimeoutExpired:
        return SourceRunResult(
            platform="douyin",
            keyword=keyword,
            status="ERROR",
            error=f"MediaCrawler subprocess timed out after {timeout}s",
        )
    except Exception as exc:
        return SourceRunResult(
            platform="douyin",
            keyword=keyword,
            status="ERROR",
            error=str(exc),
        )

    # Detect auth failure in output
    combined = (result.stdout or "") + (result.stderr or "")
    if "login_by_qrcode" in combined and "Begin login" in combined:
        # QR code was triggered — means no valid session was found
        return SourceRunResult(
            platform="douyin",
            keyword=keyword,
            status="AUTH_REQUIRED",
            error="Douyin QR login triggered — no active CDP session. Open Edge with Douyin profile first.",
        )

    # Read ALL records from today's file then filter to this keyword
    all_records = _read_jsonl(jsonl_path)
    keyword_records = [r for r in all_records if r.get("source_keyword") == keyword]

    if not keyword_records:
        return SourceRunResult(
            platform="douyin",
            keyword=keyword,
            status="EMPTY",
            records_raw=0,
            records_normalized=0,
        )

    contents: list[NormalizedContent] = []
    snapshots: list[MetricSnapshot] = []
    seen: set[str] = set()

    for raw in keyword_records:
        c, s = normalize_douyin(raw, captured_at)
        if not c.content_id or c.content_id in seen:
            continue
        seen.add(c.content_id)
        contents.append(c)
        snapshots.append(s)

    return SourceRunResult(
        platform="douyin",
        keyword=keyword,
        status="SUCCESS",
        records_raw=len(keyword_records),
        records_normalized=len(contents),
        contents=contents,
        snapshots=snapshots,
    )


def _count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def _base_env() -> dict:
    import os
    return dict(os.environ)
