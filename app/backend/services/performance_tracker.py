# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Native post-upload performance snapshots for exact GeneratedVideo assets."""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, and_

from backend.core.api_keys import get_youtube_api_key
from backend.core.exceptions import safe_json_loads
from backend.database import AsyncSessionLocal
from backend.models.generated_video import GeneratedVideo
from backend.models.user_settings import UserSettings
from backend.models.video_metrics import VideoMetrics

logger = logging.getLogger(__name__)
PLATFORMS = {"youtube", "tiktok", "instagram"}
METRIC_FIELDS = ("views", "likes", "comments", "shares")


def normalize_metrics(payload: dict) -> dict:
    """Never coerce a missing, negative or malformed provider value to zero."""
    if not isinstance(payload, dict):
        raise ValueError("Metrics must be an object")
    result = {}
    for field in METRIC_FIELDS:
        value = payload.get(field)
        if value is None:
            result[field] = None
        elif isinstance(value, bool) or not str(value).isdigit():
            raise ValueError(f"{field} must be a non-negative integer")
        else:
            result[field] = int(value)
    if all(value is None for value in result.values()):
        raise ValueError("At least one metric is required")
    return result


def _platform_id(video, platform):
    return {"youtube": video.youtube_video_id,
            "tiktok": video.tiktok_publish_id,
            "instagram": video.instagram_media_id}.get(platform)


def _uploaded_platforms(video):
    value = safe_json_loads(video.uploaded_platforms_json, [], logger)
    return list(dict.fromkeys(p for p in value if isinstance(p, str))) if isinstance(value, list) else []


def _should_poll_now(video: GeneratedVideo, last_synced_at: datetime | None = None) -> bool:
    """Use the platform snapshot timestamp; a metadata edit is not a sync."""
    if not video.created_at:
        return False
    age = datetime.utcnow() - video.created_at
    if age >= timedelta(days=30):
        return False
    interval = (timedelta(hours=2) if age < timedelta(days=1)
                else timedelta(hours=6) if age < timedelta(days=7)
                else timedelta(days=1))
    return last_synced_at is None or datetime.utcnow() - last_synced_at >= interval


async def poll_all_uploaded_videos():
    """Explicit maintenance call; no tight background polling or retry storm."""
    async with AsyncSessionLocal() as db:
        videos = (await db.execute(select(GeneratedVideo).where(
            GeneratedVideo.status == "uploaded",
            GeneratedVideo.created_at >= datetime.utcnow() - timedelta(days=30),
        ))).scalars().all()
    for video in videos:
        if "youtube" not in _uploaded_platforms(video) or not video.youtube_video_id:
            continue
        try:
            async with AsyncSessionLocal() as db:
                last = (await db.execute(select(VideoMetrics).where(
                    VideoMetrics.generated_video_id == video.id,
                    VideoMetrics.platform == "youtube",
                ).order_by(VideoMetrics.fetched_at.desc()).limit(1))).scalar_one_or_none()
            if not _should_poll_now(video, last.fetched_at if last else None):
                continue
            metrics = await fetch_youtube_metrics(video.youtube_video_id, video.user_id or "local")
            if metrics is not None:
                await _save_metrics(video.id, "youtube", metrics)
        except Exception as exc:
            logger.warning("YouTube metric sync failed for %s: %s", video.id, exc)
    # TikTok publish_id is an upload transaction ID, not necessarily a public
    # post ID. Do not query it as if the video mapping were proven.


async def fetch_youtube_metrics(youtube_video_id: str, user_id: str = "local") -> dict | None:
    """YouTube Data API v3; BYOK in Settings takes priority over .env."""
    import httpx
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(UserSettings).where(
            UserSettings.user_id == user_id))).scalar_one_or_none()
    api_key = get_youtube_api_key(row)
    if not api_key:
        return None

    def _fetch():
        resp = httpx.get("https://www.googleapis.com/youtube/v3/videos",
                         params={"part": "statistics", "id": youtube_video_id, "key": api_key}, timeout=10)
        if resp.status_code != 200:
            logger.warning("YouTube API error: %s", resp.status_code)
            return None
        data = resp.json()
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            return None
        # Reject missing or mismatched content IDs rather than attributing
        # unrelated provider statistics to this GeneratedVideo.
        item = items[0]
        if item.get("id") != youtube_video_id:
            return None
        stats = item.get("statistics")
        if not isinstance(stats, dict):
            return None
        try:
            return normalize_metrics({
                "views": stats.get("viewCount"), "likes": stats.get("likeCount"),
                "comments": stats.get("commentCount"), "shares": None,
            })
        except ValueError as exc:
            logger.warning("Malformed YouTube statistics: %s", exc)
            return None
    return await asyncio.to_thread(_fetch)


async def fetch_tiktok_metrics(publish_id: str) -> dict | None:
    """Disabled: upload publish_id is not proven to be a public video ID."""
    return None


async def _save_metrics(generated_video_id: str, platform: str, metrics: dict) -> bool:
    """Append a changed snapshot only for an exact published asset/platform."""
    if platform not in PLATFORMS:
        raise ValueError("Unsupported platform")
    values = normalize_metrics(metrics)
    async with AsyncSessionLocal() as db:
        video = (await db.execute(select(GeneratedVideo).where(
            GeneratedVideo.id == generated_video_id))).scalar_one_or_none()
        if not video or video.status != "uploaded" or platform not in _uploaded_platforms(video):
            raise ValueError("Video is not published to this platform")
        latest = (await db.execute(select(VideoMetrics).where(
            VideoMetrics.generated_video_id == generated_video_id,
            VideoMetrics.platform == platform,
        ).order_by(VideoMetrics.fetched_at.desc()).limit(1))).scalar_one_or_none()
        if latest and all(getattr(latest, key) == value for key, value in values.items()):
            return False
        db.add(VideoMetrics(generated_video_id=generated_video_id, platform=platform, **values))
        await db.commit()
    return True


def _snapshot(row, video):
    now = datetime.utcnow()
    values = {key: getattr(row, key) for key in METRIC_FIELDS}
    views = values["views"]
    # A rate from only one reported interaction field looks complete but is not.
    has_engagement = values["likes"] is not None and values["comments"] is not None
    engagement = sum(values[key] or 0 for key in ("likes", "comments", "shares"))
    return {**values, "watch_time_hours": row.watch_time_hours,
            "avg_view_duration": row.avg_view_duration, "ctr": row.ctr,
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
            "stale": row.fetched_at is None or now - row.fetched_at > timedelta(days=2),
            "platform_content_id": _platform_id(video, row.platform),
            "engagement_rate": (engagement / views if has_engagement and views and views > 0 else None),
            "state": "INSUFFICIENT_DATA"}


async def get_video_performance(generated_video_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        video = (await db.execute(select(GeneratedVideo).where(
            GeneratedVideo.id == generated_video_id))).scalar_one_or_none()
        if video is None:
            raise ValueError("Video not found")
        records = (await db.execute(select(VideoMetrics).where(
            VideoMetrics.generated_video_id == generated_video_id,
        ).order_by(VideoMetrics.fetched_at.asc(), VideoMetrics.id.asc()))).scalars().all()
    history = {}
    for row in records:
        if row.platform in _uploaded_platforms(video):
            history.setdefault(row.platform, []).append(_snapshot(row, video))
    return {"generated_video_id": generated_video_id,
            "latest": {key: values[-1] for key, values in history.items()},
            "history": history}


async def get_performance_overview(user_id: str = "local") -> dict:
    """One native Performance owner; facts only, no inferred winning score."""
    async with AsyncSessionLocal() as db:
        videos = (await db.execute(select(GeneratedVideo).where(
            GeneratedVideo.user_id == user_id, GeneratedVideo.status == "uploaded",
        ).order_by(GeneratedVideo.created_at.desc()))).scalars().all()
        ids = [v.id for v in videos]
        rows = (await db.execute(select(VideoMetrics).where(
            VideoMetrics.generated_video_id.in_(ids)
        ).order_by(VideoMetrics.fetched_at.asc(), VideoMetrics.id.asc()))).scalars().all() if ids else []
    latest = {}
    for row in rows:
        latest[(row.generated_video_id, row.platform)] = row
    posts = []
    by_platform = {}
    for video in videos:
        for platform in _uploaded_platforms(video):
            if platform not in PLATFORMS:
                continue
            row = latest.get((video.id, platform))
            data = _snapshot(row, video) if row else None
            posts.append({"generated_video_id": video.id, "title": video.title,
                          "thumbnail_url": f"/api/videos/{video.id}/thumbnail" if video.thumbnail_path else None,
                          "platform": platform, "platform_content_id": _platform_id(video, platform),
                          "published_at": None,  # Not persisted by old uploader; do not invent it.
                          "latest_metrics": data, "state": data["state"] if data else "INSUFFICIENT_DATA",
                          "source_downloaded_video_id": video.source_downloaded_video_id,
                          "hook_type": video.clip_hook_type, "format": video.source_type,
                          "niche": video.niche, "gen_tier": video.gen_tier,
                          "aspect_ratio": video.aspect_ratio})
            bucket = by_platform.setdefault(platform, {"published": 0, "with_metrics": 0, "views": None})
            bucket["published"] += 1
            if data:
                bucket["with_metrics"] += 1
                if data["views"] is not None:
                    bucket["views"] = (bucket["views"] or 0) + data["views"]
    measured = [p for p in posts if p["latest_metrics"] and p["latest_metrics"]["views"] is not None]
    measured.sort(key=lambda p: p["latest_metrics"]["views"], reverse=True)
    return {"published_posts": len(posts), "posts_with_metrics": sum(bool(p["latest_metrics"]) for p in posts),
            "by_platform": by_platform, "posts": posts, "top_posts": measured[:5]}


async def get_performance_summary(user_id: str = "local") -> dict:
    """Compatibility shape for existing Assistant/cards; sourced from same owner."""
    overview = await get_performance_overview(user_id)
    measured = [p for p in overview["posts"] if p["latest_metrics"]]
    return {"total_views": sum(p["latest_metrics"]["views"] or 0 for p in measured),
            "total_likes": sum(p["latest_metrics"]["likes"] or 0 for p in measured),
            "total_videos": len({p["generated_video_id"] for p in overview["posts"]}),
            "best_video": ({"id": overview["top_posts"][0]["generated_video_id"],
                            "title": overview["top_posts"][0]["title"],
                            "views": overview["top_posts"][0]["latest_metrics"]["views"]}
                           if overview["top_posts"] else None)}

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday")


def _weekday_index(name: str) -> int:
    """Sort key for day names — Monday first, anything unrecognised last.

    Day keys come from `created_at.strftime("%A").lower()`, so a non-English
    locale can yield a name that isn't in the tuple; that must sort, not raise.
    """
    try:
        return _WEEKDAYS.index(name)
    except ValueError:
        return len(_WEEKDAYS)


async def recommend_posting_time(
    user_id: str = "local",
    platform: str = "youtube",
) -> dict:
    """
    Analyze when uploaded videos performed best and recommend optimal posting time.
    Groups videos by upload hour and day-of-week, compares early view velocity
    (views in first 24h) to find the best time slot.

    Requires at least 5 uploaded videos with performance data for meaningful results.
    """
    async with AsyncSessionLocal() as db:
        # Get all uploaded videos for this platform
        uploaded = await db.execute(
            select(GeneratedVideo).where(
                and_(
                    GeneratedVideo.user_id == user_id,
                    GeneratedVideo.status == "uploaded",
                )
            )
        )
        videos = uploaded.scalars().all()

        if not videos:
            return {
                "recommendation": None,
                "confidence": 0,
                "sample_size": 0,
                "message": "No uploaded videos found. Upload some videos first!",
            }

        # Filter to videos on the target platform
        platform_videos = []
        for v in videos:
            platforms = json.loads(v.uploaded_platforms_json or "[]")
            if platform in platforms:
                platform_videos.append(v)

        if len(platform_videos) < 3:
            return {
                "recommendation": None,
                "confidence": 0,
                "sample_size": len(platform_videos),
                "message": f"Need at least 3 videos uploaded to {platform} for recommendations (found {len(platform_videos)}).",
            }

        # For each video, get early metrics (first data point = ~2h after upload)
        hour_performance = {}   # hour (0-23) → list of early view counts
        day_performance = {}    # day name → list of early view counts

        for v in platform_videos:
            if not v.created_at:
                continue

            # Get the earliest metric snapshot for this video
            metrics_result = await db.execute(
                select(VideoMetrics)
                .where(
                    and_(
                        VideoMetrics.generated_video_id == v.id,
                        VideoMetrics.platform == platform,
                    )
                )
                .order_by(VideoMetrics.fetched_at.asc())
                .limit(1)
            )
            first_metric = metrics_result.scalar_one_or_none()
            if not first_metric or first_metric.views == 0:
                continue

            upload_hour = v.created_at.hour
            upload_day = v.created_at.strftime("%A").lower()  # monday, tuesday, etc.

            # Use views from first metric as proxy for early velocity
            hour_performance.setdefault(upload_hour, []).append(first_metric.views)
            day_performance.setdefault(upload_day, []).append(first_metric.views)

    if not hour_performance:
        return {
            "recommendation": None,
            "confidence": 0,
            "sample_size": len(platform_videos),
            "message": "Not enough performance data yet. Wait for metrics to be collected.",
        }

    # Find best hour
    hour_avgs = {h: sum(vs) / len(vs) for h, vs in hour_performance.items()}
    best_hour = max(hour_avgs, key=hour_avgs.get)
    overall_avg = sum(v for vs in hour_performance.values() for v in vs) / sum(len(vs) for vs in hour_performance.values())
    hour_lift = (hour_avgs[best_hour] / max(overall_avg, 1) - 1) * 100

    # Find best day
    best_day = None
    day_lift = 0
    # Initialised unconditionally: the response below reads day_avgs, and a
    # name that only exists inside the `if` is a NameError waiting for the next
    # edit.
    day_avgs: dict[str, float] = {}
    if day_performance:
        day_avgs = {d: sum(vs) / len(vs) for d, vs in day_performance.items()}
        best_day = max(day_avgs, key=day_avgs.get)
        day_avg_overall = sum(v for vs in day_performance.values() for v in vs) / sum(len(vs) for vs in day_performance.values())
        day_lift = (day_avgs[best_day] / max(day_avg_overall, 1) - 1) * 100

    # Confidence based on sample size
    total_data_points = sum(len(vs) for vs in hour_performance.values())
    if total_data_points >= 20:
        confidence = 0.85
    elif total_data_points >= 10:
        confidence = 0.65
    elif total_data_points >= 5:
        confidence = 0.45
    else:
        confidence = 0.25

    note_parts = [f"Based on {total_data_points} uploads"]
    if hour_lift > 10:
        note_parts.append(f"posting at {best_hour}:00 UTC gets {hour_lift:.0f}% more early views")
    if best_day and day_lift > 10:
        note_parts.append(f"{best_day.title()}s perform {day_lift:.0f}% better")

    return {
        "recommendation": {
            "hour_utc": best_hour,
            "day": best_day,
            "platform": platform,
        },
        "confidence": round(confidence, 2),
        "sample_size": total_data_points,
        "hour_lift_pct": round(hour_lift, 1),
        "day_lift_pct": round(day_lift, 1),
        "hour_breakdown": {str(h): round(avg, 1) for h, avg in sorted(hour_avgs.items())},
        # day_avgs, NOT day_performance: the latter maps day → LIST of view
        # counts, and round() on a list raises TypeError — which took out this
        # whole endpoint on its happy path (any user with upload history).
        "day_breakdown": {
            d: round(avg, 1)
            for d, avg in sorted(day_avgs.items(), key=lambda kv: _weekday_index(kv[0]))
        },
        "message": ". ".join(note_parts) + ".",
    }
