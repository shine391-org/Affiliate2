"""
scanplan.py — Scan plan loader + scan-all runner (Phase 2.3)

Loads data/radar_sources.yaml, runs all enabled sources sequentially,
reports per-source results. No concurrency.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from .ingest import ingest
from .models import SourceRunResult
from .sources import bilibili as bili_source
from .sources import douyin as dy_source

PROJECT_ROOT = Path(__file__).parent.parent.parent
SOURCES_PATH = PROJECT_ROOT / "data" / "radar_sources.yaml"


@dataclass
class ScanSource:
    platform: str
    keyword: str
    enabled: bool = True
    max_count: int = 15


def load_scan_plan(path: Optional[Path] = None) -> list[ScanSource]:
    """Load scan plan from YAML. Returns list of ScanSource (enabled only)."""
    p = path or SOURCES_PATH
    if not p.exists():
        raise FileNotFoundError(f"Scan plan not found: {p}")
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    sources = []
    for item in data.get("sources", []):
        sources.append(
            ScanSource(
                platform=item["platform"],
                keyword=item["keyword"],
                enabled=item.get("enabled", True),
                max_count=item.get("max_count", 15),
            )
        )
    return sources


def run_scan_all(
    path: Optional[Path] = None,
    only_enabled: bool = True,
) -> list[tuple[ScanSource, SourceRunResult]]:
    """
    Run all sources sequentially. Returns list of (source, result) tuples.
    Stops on first Douyin AUTH_REQUIRED to avoid hammering login wall.
    """
    plan = load_scan_plan(path)
    if only_enabled:
        plan = [s for s in plan if s.enabled]
    if not plan:
        return []

    results: list[tuple[ScanSource, SourceRunResult]] = []
    auth_blocked = False

    for src in plan:
        if auth_blocked and src.platform == "douyin":
            print(f"  [skip] {src.platform}/{src.keyword} — Douyin auth blocked earlier")
            continue

        print(f"  [scan] {src.platform}/{src.keyword} (max={src.max_count})")
        if src.platform == "douyin":
            raw = dy_source.scan(src.keyword, max_count=src.max_count)
        elif src.platform == "bilibili":
            raw = bili_source.scan(src.keyword, max_count=src.max_count)
        else:
            print(f"  [skip] unknown platform: {src.platform}")
            continue

        result = ingest(raw)
        results.append((src, result))
        print(
            f"    -> {result.status}: raw={result.records_raw} "
            f"norm={result.records_normalized} +new={result.records_new} ~upd={result.records_updated}"
        )
        if result.status == "AUTH_REQUIRED":
            auth_blocked = True
            print("    -> Douyin auth required: stopping further Douyin scans.")

    return results
