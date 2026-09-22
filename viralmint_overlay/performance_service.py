"""Performance feedback service for published content."""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

from .performance_store import PerformanceStore, PerformanceValidationError
from .publish_service import PublishActionError, _youtube_credentials
from .publish_store import PublishStore
from .review_store import ReviewStore


class PerformanceActionError(ValueError):
    pass


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def derived_metrics(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    if not snapshots:
        return {
            "engagement_count": None,
            "engagement_rate": None,
            "view_velocity_per_hour": None,
            "snapshot_count": 0,
        }

    latest = snapshots[-1]
    views = _as_int(latest.get("views"))
    engagement_parts = [
        _as_int(latest.get("likes")),
        _as_int(latest.get("comments")),
        _as_int(latest.get("shares")),
    ]
    known_engagement = [v for v in engagement_parts if v is not None]
    engagement_count = sum(known_engagement) if known_engagement else None
    engagement_rate = (
        engagement_count / views
        if engagement_count is not None and views is not None and views > 0
        else None
    )

    velocity = None
    if len(snapshots) >= 2:
        first = snapshots[0]
        first_views = _as_int(first.get("views"))
        t0 = _parse_time(first.get("collected_at"))
        t1 = _parse_time(latest.get("collected_at"))
        if (
            first_views is not None
            and views is not None
            and t0 is not None
            and t1 is not None
        ):
            hours = (t1 - t0).total_seconds() / 3600.0
            if hours > 0:
                velocity = (views - first_views) / hours

    return {
        "engagement_count": engagement_count,
        "engagement_rate": engagement_rate,
        "view_velocity_per_hour": velocity,
        "snapshot_count": len(snapshots),
    }


def add_manual_metrics(
    store: PerformanceStore,
    publication_id: int,
    *,
    views: int | None,
    likes: int | None,
    comments: int | None,
    shares: int | None,
    note: str | None = None,
) -> dict[str, Any]:
    return store.add_snapshot(
        publication_id,
        source="manual",
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        note=note,
    )


def sync_youtube_metrics(
    performance_store: PerformanceStore,
    publish_store: PublishStore,
    publication_id: int,
) -> dict[str, Any]:
    publication = performance_store.get_publication(publication_id)
    if not publication:
        raise PerformanceActionError("Publication not found")
    if publication.get("platform") != "youtube":
        raise PerformanceActionError("Automatic metric sync is available only for YouTube")
    if publication.get("status") != "PUBLISHED":
        raise PerformanceActionError("Publication is not PUBLISHED")
    video_id = (publication.get("external_id") or "").strip()
    if not video_id:
        raise PerformanceActionError("YouTube publication has no video id")

    try:
        from googleapiclient.discovery import build

        creds = _youtube_credentials(publish_store)
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        response = youtube.videos().list(part="statistics", id=video_id).execute()
    except PublishActionError as exc:
        raise PerformanceActionError(str(exc)) from exc
    except Exception as exc:
        raise PerformanceActionError(f"YouTube metric sync failed: {exc}") from exc

    items = response.get("items") or []
    if not items:
        raise PerformanceActionError("YouTube returned no statistics for this video")
    stats = items[0].get("statistics") or {}

    return performance_store.add_snapshot(
        publication_id,
        source="youtube_api",
        views=_as_int(stats.get("viewCount")),
        likes=_as_int(stats.get("likeCount")),
        comments=_as_int(stats.get("commentCount")),
        shares=None,
        raw={"statistics": stats},
    )


def publication_performance(store: PerformanceStore, publication_id: int) -> dict[str, Any]:
    publication = store.get_publication(publication_id)
    if not publication:
        raise PerformanceActionError("Publication not found")
    snapshots = store.list_snapshots(publication_id)
    return {
        "publication": publication,
        "snapshots": snapshots,
        "latest": snapshots[-1] if snapshots else None,
        "derived": derived_metrics(snapshots),
    }


def _candidate_features(review_store: ReviewStore, source_platform: str, content_id: str) -> dict[str, Any]:
    try:
        detail = review_store.get_candidate_detail(source_platform, content_id)
    except Exception:
        return {}
    intel = detail.get("intelligence") or {}
    content_format = intel.get("content_format") or {}
    hook = intel.get("hook") or {}
    return {
        "topic": intel.get("topic"),
        "format": content_format.get("type") if isinstance(content_format, dict) else content_format,
        "hook_type": hook.get("type") if isinstance(hook, dict) else None,
        "localization_potential": intel.get("localization_potential"),
        "remake_difficulty": intel.get("remake_difficulty"),
    }


def _avg(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def build_feedback_summary(
    store: PerformanceStore,
    review_store: ReviewStore,
) -> dict[str, Any]:
    rows = store.published_rows()
    posts: list[dict[str, Any]] = []

    for pub in rows:
        snapshots = store.list_snapshots(pub["id"])
        derived = derived_metrics(snapshots)
        latest = snapshots[-1] if snapshots else None
        features = _candidate_features(
            review_store,
            pub["source_platform"],
            pub["content_id"],
        )
        posts.append(
            {
                **pub,
                "latest_metrics": latest,
                "derived": derived,
                "features": features,
            }
        )

    pattern_buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for post in posts:
        latest = post.get("latest_metrics")
        if not latest:
            continue
        platform = post.get("platform") or "unknown"
        features = post.get("features") or {}
        for dimension in ("topic", "format", "hook_type"):
            value = features.get(dimension)
            if not value:
                continue
            pattern_buckets.setdefault((platform, dimension, str(value)), []).append(post)

    patterns = []
    for (platform, dimension, value), members in pattern_buckets.items():
        views = [
            float(p["latest_metrics"]["views"])
            for p in members
            if p["latest_metrics"].get("views") is not None
        ]
        ers = [
            float(p["derived"]["engagement_rate"])
            for p in members
            if p["derived"].get("engagement_rate") is not None
        ]
        velocities = [
            float(p["derived"]["view_velocity_per_hour"])
            for p in members
            if p["derived"].get("view_velocity_per_hour") is not None
        ]
        patterns.append(
            {
                "platform": platform,
                "dimension": dimension,
                "value": value,
                "sample_count": len(members),
                "avg_views": _avg(views),
                "avg_engagement_rate": _avg(ers),
                "avg_view_velocity_per_hour": _avg(velocities),
            }
        )

    patterns.sort(
        key=lambda p: (
            p["sample_count"],
            p["avg_views"] if p["avg_views"] is not None else -1,
        ),
        reverse=True,
    )

    by_platform: dict[str, dict[str, int]] = {}
    for post in posts:
        p = post.get("platform") or "unknown"
        bucket = by_platform.setdefault(p, {"published": 0, "with_metrics": 0})
        bucket["published"] += 1
        if post.get("latest_metrics"):
            bucket["with_metrics"] += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "published_posts": len(posts),
        "posts_with_metrics": sum(1 for p in posts if p.get("latest_metrics")),
        "by_platform": by_platform,
        "posts": posts,
        "patterns": patterns,
        "learning_policy": {
            "ranking_weights_changed": False,
            "reason": (
                "Phase 4 records observed market performance but does not auto-tune "
                "Radar ranking weights from a small sample."
            ),
        },
    }
