"""Legacy Radar Douyin adapter must stay a thin canonical-provider bridge."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from radar.sources import douyin as dy
from backend.sources.base import ProviderStatus, SourceProviderResult


def test_radar_douyin_adapter_maps_canonical_rows_without_mediacrawler(monkeypatch):
    async def fake_search(self, query, limit=15):
        assert query == "AI"
        assert limit == 2
        return SourceProviderResult(
            platform="douyin",
            query=query,
            status=ProviderStatus.SUCCESS,
            provider="douyin-cdp-web-search",
            items=[
                {
                    "platform": "douyin",
                    "video_id": "123",
                    "video_url": "https://www.douyin.com/video/123",
                    "creator_id": "creator",
                    "author": "Alice",
                    "title": "Example",
                    "description": "Description",
                    "upload_date": datetime(2026, 9, 24, 10, 0, 0),
                    "radar_metrics": {
                        "views": None,
                        "likes": 10,
                        "comments": 2,
                        "shares": 1,
                        "favorites": 3,
                    },
                }
            ],
        )

    monkeypatch.setattr(dy.DouyinProvider, "search", fake_search)
    result = asyncio.run(dy.scan_async("AI", max_count=2))

    assert result.status == "SUCCESS"
    assert result.records_raw == 1
    assert result.records_normalized == 1
    assert result.contents[0].content_id == "123"
    assert result.contents[0].creator_name == "Alice"
    assert result.snapshots[0].views is None
    assert result.snapshots[0].likes == 10
    assert not hasattr(dy, "MC_DIR")
    assert not hasattr(dy, "_read_jsonl")


def test_radar_douyin_adapter_preserves_auth_required(monkeypatch):
    async def fake_search(self, query, limit=15):
        return SourceProviderResult(
            platform="douyin",
            query=query,
            status=ProviderStatus.AUTH_REQUIRED,
            error="CDP login required",
            provider="douyin-cdp-web-search",
        )

    monkeypatch.setattr(dy.DouyinProvider, "search", fake_search)
    result = asyncio.run(dy.scan_async("AI"))

    assert result.status == "AUTH_REQUIRED"
    assert result.error == "CDP login required"
    assert result.records_normalized == 0
