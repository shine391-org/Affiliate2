# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""REST /api/news — news scouting + article analysis."""
import json
import logging
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select

from backend.agents.job_helper import create_job
from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.models.scout_result import ScoutResult
from backend.models.downloaded_video import DownloadedVideo

logger = logging.getLogger(__name__)
router = APIRouter()


class NewsScoutRequest(BaseModel):
    query: str
    expanded_queries: list[str] = []
    sources: list[str] = ["google", "bing", "hackernews", "reddit"]


class AnalyzeUrlRequest(BaseModel):
    url: str


class NewsSaveRequest(BaseModel):
    article_ids: list[str]


@router.post("/news/scout")
async def start_news_scout(req: NewsScoutRequest):
    """Start a news scout job."""
    from backend.core.task_runner import run_news_scout, dispatch
    job = await create_job("news_scout", "local", {
        "query": req.query,
        "expanded_queries": req.expanded_queries,
        "sources": req.sources,
    })
    dispatch(run_news_scout(
        job_id=job.id,
        query=req.query,
        expanded_queries=req.expanded_queries or None,
        sources=req.sources or None,
        user_id="local",
    ))
    return {"job_id": job.id}


@router.post("/news/analyze-url")
async def analyze_url(req: AnalyzeUrlRequest):
    """Analyze a single article URL."""
    from backend.core.task_runner import run_news_scout, dispatch
    job = await create_job("news_scout", "local", {
        "direct_url": req.url,
    })
    dispatch(run_news_scout(
        job_id=job.id,
        query="direct URL analysis",
        direct_url=req.url,
        user_id="local",
    ))
    return {"job_id": job.id}


@router.post("/news/save")
async def save_news_to_library(req: NewsSaveRequest):
    """
    Save selected news articles to Library (downloaded_videos).
    Downloads article images locally for video generation.
    """
    if not req.article_ids:
        raise HTTPException(400, "No article IDs provided")

    user_id = "local"
    saved = []

    async with AsyncSessionLocal() as db:
        for article_id in req.article_ids:
            result = await db.execute(
                select(ScoutResult).where(ScoutResult.id == article_id)
            )
            sr = result.scalar_one_or_none()
            if not sr:
                logger.warning("News save: scout result %s not found", article_id)
                continue

            # Check if already saved
            existing = await db.execute(
                select(DownloadedVideo).where(
                    DownloadedVideo.scout_result_id == sr.id,
                    DownloadedVideo.user_id == user_id,
                )
            )
            if existing.scalar_one_or_none():
                logger.info("News save: article %s already saved, skipping", article_id[:8])
                continue

            # Parse the analysis from description
            analysis = {}
            try:
                analysis = json.loads(sr.description or "{}")
            except json.JSONDecodeError:
                pass

            # Use full text if available, fall back to preview
            full_text = analysis.pop("full_text", "") or analysis.pop("full_text_preview", "")

            # Download article image locally if available
            thumbnail_local = None
            image_url = sr.thumbnail_url
            if image_url:
                thumbnail_local = await _download_image(image_url, sr.id)

            dv = DownloadedVideo(
                user_id=user_id,
                scout_result_id=sr.id,
                title=sr.title or "Untitled Article",
                platform="news",
                transcript=full_text or sr.title,
                insights_json=json.dumps({
                    "source_url": sr.video_url,
                    "source_domain": sr.author,
                    "published_at": sr.upload_date.isoformat() if sr.upload_date else None,
                    "image_url": image_url,
                    **analysis,
                }, ensure_ascii=False),
                video_path=None,
                audio_path=None,
                thumbnail_path=thumbnail_local or image_url,
            )
            db.add(dv)
            await db.flush()
            saved.append({"id": dv.id, "title": dv.title})

        await db.commit()

    logger.info("News save: saved %d/%d articles to Library", len(saved), len(req.article_ids))
    return {"saved": len(saved), "articles": saved}


async def _download_image(url: str, article_id: str) -> str | None:
    """Download an article image to storage/thumbnails/. Returns local path or None."""
    import httpx

    try:
        # Clean HTML entities from URL
        clean_url = url.replace("&amp;", "&")

        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(clean_url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "image" not in content_type and not any(
            clean_url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")
        ):
            logger.debug("Not an image (content-type: %s): %s", content_type, clean_url[:80])
            return None

        # Determine extension
        ext = ".jpg"
        if "png" in content_type:
            ext = ".png"
        elif "webp" in content_type:
            ext = ".webp"
        elif "gif" in content_type:
            ext = ".gif"

        thumb_dir = settings.THUMBNAILS_DIR
        thumb_dir.mkdir(parents=True, exist_ok=True)
        filename = f"news_{article_id[:8]}_{uuid4().hex[:6]}{ext}"
        local_path = thumb_dir / filename
        local_path.write_bytes(resp.content)

        logger.info("Downloaded article image: %s (%d KB)", filename, len(resp.content) // 1024)
        return str(local_path)

    except Exception as e:
        logger.warning("Failed to download article image from %s: %s", url[:80], e)
        return None
