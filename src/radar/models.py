"""
models.py — Normalized dataclasses for Radar Ingestion (Phase 2.1)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NormalizedContent:
    """Platform-agnostic content record (maps to `content` table)."""
    platform: str
    content_id: str
    canonical_url: Optional[str] = None
    creator_id: Optional[str] = None
    creator_name: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    publish_time: Optional[int] = None   # Unix timestamp
    source_keyword: Optional[str] = None


@dataclass
class MetricSnapshot:
    """Immutable engagement observation (maps to `metric_snapshots` table)."""
    platform: str
    content_id: str
    captured_at: str          # ISO-8601 UTC
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    favorites: Optional[int] = None


@dataclass
class SourceRunResult:
    """Summary returned by each source adapter after a crawl."""
    platform: str
    keyword: str
    status: str                           # SUCCESS | EMPTY | AUTH_REQUIRED | ERROR
    records_raw: int = 0
    records_normalized: int = 0
    records_new: int = 0
    records_updated: int = 0
    error: Optional[str] = None
    contents: list[NormalizedContent] = field(default_factory=list)
    snapshots: list[MetricSnapshot] = field(default_factory=list)
