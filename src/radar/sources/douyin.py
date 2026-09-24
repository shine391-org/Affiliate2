"""Legacy Radar CLI adapter for the canonical app-native Douyin provider.

Radar no longer launches MediaCrawler or reads JSONL. The compatibility CLI
reuses the same authenticated CDP provider as the Affiliate2 app and only maps
its normalized Scout-row shape into Radar legacy dataclasses.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from ..intel._canonical_bridge import ensure_canonical_app_imports
from ..models import MetricSnapshot, NormalizedContent, SourceRunResult

ensure_canonical_app_imports()

from backend.sources.base import ProviderStatus
from backend.sources.douyin import DouyinProvider


def _publish_timestamp(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_metric(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


async def scan_async(keyword: str, max_count: int = 15) -> SourceRunResult:
    """Run canonical Douyin discovery and adapt the result for legacy Radar."""
    captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider_result = await DouyinProvider().search(keyword, limit=max_count)

    if provider_result.status != ProviderStatus.SUCCESS:
        return SourceRunResult(
            platform="douyin",
            keyword=keyword,
            status=provider_result.status.value,
            records_raw=len(provider_result.items),
            records_normalized=0,
            error=provider_result.error,
        )

    contents: list[NormalizedContent] = []
    snapshots: list[MetricSnapshot] = []
    seen: set[str] = set()

    for row in provider_result.items:
        content_id = str(row.get("video_id") or "").strip()
        if not content_id or content_id in seen:
            continue
        seen.add(content_id)

        radar_metrics = row.get("radar_metrics") or {}
        contents.append(
            NormalizedContent(
                platform="douyin",
                content_id=content_id,
                canonical_url=row.get("video_url"),
                creator_id=row.get("creator_id"),
                creator_name=row.get("author"),
                title=row.get("title"),
                description=row.get("description"),
                publish_time=_publish_timestamp(row.get("upload_date")),
                source_keyword=keyword,
            )
        )
        snapshots.append(
            MetricSnapshot(
                platform="douyin",
                content_id=content_id,
                captured_at=captured_at,
                views=_optional_metric(radar_metrics.get("views")),
                likes=_optional_metric(radar_metrics.get("likes")),
                comments=_optional_metric(radar_metrics.get("comments")),
                shares=_optional_metric(radar_metrics.get("shares")),
                favorites=_optional_metric(radar_metrics.get("favorites")),
            )
        )

    return SourceRunResult(
        platform="douyin",
        keyword=keyword,
        status="SUCCESS" if contents else "EMPTY",
        records_raw=len(provider_result.items),
        records_normalized=len(contents),
        contents=contents,
        snapshots=snapshots,
    )


def scan(keyword: str, max_count: int = 15, timeout: int = 300) -> SourceRunResult:
    """Sync compatibility API used by radar.py/scanplan.

    timeout is retained for callers but no subprocess exists anymore. The
    canonical provider owns its bounded CDP/network timeouts.
    """
    del timeout
    return asyncio.run(scan_async(keyword, max_count=max_count))
