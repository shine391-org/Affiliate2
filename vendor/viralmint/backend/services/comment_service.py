# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Comment fetching and AI sentiment analysis.

Fetches top comments from YouTube (and TikTok via TikHub) and runs AI analysis
to extract audience reactions, praised moments, common questions, and content gaps.
"""
import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

COMMENT_ANALYSIS_PROMPT = """Analyze these viewer comments from a video to extract audience intelligence.

Video transcript (first 1500 chars for context):
{transcript}

Top {count} comments (sorted by likes):
{comments_text}

Return JSON only (no markdown):
{{
  "top_praised_moments": [
    "specific moment or topic viewers loved (quote relevant comments)"
  ],
  "common_questions": [
    "questions viewers asked (potential content gaps to fill)"
  ],
  "audience_sentiment": "1-2 sentence overall sentiment summary",
  "sentiment_score": 0.75,
  "content_gaps": [
    "topics viewers want more depth on or follow-ups requested"
  ],
  "engagement_drivers": [
    "what specifically drove comments — personal story, practical tips, controversy, etc."
  ],
  "negative_feedback": [
    "criticisms or complaints (if any) — useful for avoiding mistakes"
  ],
  "comment_themes": [
    {{"theme": "budgeting tips", "count": 8, "sentiment": "positive"}},
    {{"theme": "asking for part 2", "count": 5, "sentiment": "positive"}}
  ]
}}

RULES:
- sentiment_score: 0.0 (very negative) to 1.0 (very positive), 0.5 = neutral
- Be specific — quote or paraphrase actual comments, don't generalize
- comment_themes: group similar comments, count how many fit each theme
- Limit to the most important 3-5 items per category"""


async def fetch_youtube_comments(
    video_id: str,
    api_key: str,
    max_comments: int = 30,
) -> list[dict]:
    """
    Fetch top comments from YouTube Data API v3.
    commentThreads.list costs only 1 quota unit (very cheap).
    Returns list of {author, text, likes, published_at}.
    """
    def _fetch():
        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
            youtube = build("youtube", "v3", developerKey=api_key)
            response = youtube.commentThreads().list(
                part="snippet",
                videoId=video_id,
                order="relevance",
                maxResults=min(max_comments, 100),
                textFormat="plainText",
            ).execute()

            comments = []
            for item in response.get("items", []):
                snippet = item["snippet"]["topLevelComment"]["snippet"]
                text = (snippet.get("textDisplay") or "").strip()
                if not text:
                    continue
                comments.append({
                    "author": snippet.get("authorDisplayName", ""),
                    "text": text,
                    "likes": snippet.get("likeCount", 0),
                    "published_at": snippet.get("publishedAt", ""),
                })
            return comments
        except HttpError as e:
            status = e.resp.status if hasattr(e, "resp") else 0
            # 403 = comments disabled, 404 = video not found/private
            if status in (403, 404):
                logger.info(f"Comments unavailable for {video_id} (HTTP {status}) — skipping")
            else:
                logger.warning(f"YouTube comments API error for {video_id}: {e}")
            return []
        except Exception as e:
            logger.warning(f"Failed to fetch YouTube comments for {video_id}: {e}")
            return []

    return await asyncio.to_thread(_fetch)


async def fetch_tiktok_comments(
    video_id: str,
    tikhub_api_key: str = "",
    max_comments: int = 30,
) -> list[dict]:
    """Fetch top comments from TikTok via TikHub API."""
    if not tikhub_api_key:
        return []

    import httpx

    def _fetch():
        try:
            resp = httpx.get(
                "https://api.tikhub.io/api/v1/tiktok/app/v3/fetch_video_comments",
                params={"aweme_id": video_id, "count": min(max_comments, 50), "cursor": 0},
                headers={"Authorization": f"Bearer {tikhub_api_key}"},
                timeout=15,
            )
            if resp.status_code == 404:
                logger.info(f"TikTok video {video_id} not found — skipping comments")
                return []
            if resp.status_code == 429:
                logger.warning(f"TikHub rate limited when fetching comments for {video_id}")
                return []
            if resp.status_code != 200:
                logger.warning(f"TikHub API returned {resp.status_code} for comments on {video_id}")
                return []
            data = resp.json().get("data", {}).get("comments", [])
            if not isinstance(data, list):
                return []
            results = []
            for c in data:
                if not isinstance(c, dict):
                    continue
                text = (c.get("text") or "").strip()
                if not text:
                    continue
                results.append({
                    "author": c.get("user", {}).get("nickname", ""),
                    "text": text,
                    "likes": c.get("digg_count", 0),
                    "published_at": "",
                })
            return results
        except httpx.TimeoutException:
            logger.warning(f"TikHub comment fetch timed out for {video_id}")
            return []
        except Exception as e:
            logger.warning(f"Failed to fetch TikTok comments for {video_id}: {e}")
            return []

    return await asyncio.to_thread(_fetch)


async def analyze_comments(
    comments: list[dict],
    transcript: str,
    ai_client,
) -> Optional[dict]:
    """
    AI analyzes comments to extract audience intelligence.
    Returns structured insights dict or None on failure.
    """
    if not comments:
        return None

    # Format comments for the prompt
    comments_text = "\n".join([
        f"[{c.get('likes', 0)} likes] {c.get('author', 'User')}: {c.get('text', '')[:200]}"
        for c in comments[:30]
    ])

    prompt = COMMENT_ANALYSIS_PROMPT.format(
        transcript=transcript[:1500] if transcript else "(no transcript available)",
        count=len(comments),
        comments_text=comments_text,
    )

    try:
        response = await ai_client.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
        )
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(clean)
        except json.JSONDecodeError as je:
            from backend.core.ai_retry import ai_fix_json
            return await ai_fix_json(clean, str(je), None)
    except Exception as e:
        logger.warning(f"Comment analysis failed: {e}")
        return None
