# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Keyword Opportunity Score — rates niche keywords on volume vs. competition.

Uses YouTube Data API search results count as a proxy for competition,
and pytrends (Google Trends) for relative search volume.

Score formula:
  opportunity = (volume_norm * 0.6 + freshness * 0.1) / (competition_norm * 0.3 + 0.01)
  Clamped to 0-100.

Higher score = high demand, low competition = best opportunity.
"""
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def score_keyword(keyword: str, youtube_api_key: str = "") -> dict:
    """
    Score a keyword/niche for content opportunity.

    Returns:
        {
            "keyword": "personal finance",
            "opportunity_score": 72.5,
            "search_volume": "high",        # relative (high/medium/low)
            "competition": "medium",        # relative
            "trend_direction": "rising",    # rising/stable/declining
            "volume_index": 78,             # 0-100 from Google Trends
            "competition_index": 45,        # 0-100 based on YouTube results
            "related_keywords": ["budgeting tips", "save money fast"],
        }
    """
    # Run volume and competition checks in parallel
    volume_task = asyncio.create_task(_get_search_volume(keyword))
    competition_task = asyncio.create_task(_get_competition(keyword, youtube_api_key))

    volume_data = await volume_task
    competition_data = await competition_task

    vol_index = volume_data.get("volume_index", 50)
    comp_index = competition_data.get("competition_index", 50)
    trend = volume_data.get("trend_direction", "stable")

    # Freshness bonus: rising trends get a boost
    freshness = {"rising": 1.0, "stable": 0.5, "declining": 0.0}.get(trend, 0.5)

    # Opportunity score: high volume + low competition = high opportunity
    vol_norm = vol_index / 100.0
    comp_norm = comp_index / 100.0
    raw_score = (vol_norm * 0.6 + freshness * 0.1) / (comp_norm * 0.3 + 0.01)
    opportunity_score = round(min(raw_score * 30, 100.0), 1)  # scale to 0-100

    # Classify
    vol_label = "high" if vol_index >= 60 else ("medium" if vol_index >= 30 else "low")
    comp_label = "high" if comp_index >= 60 else ("medium" if comp_index >= 30 else "low")

    return {
        "keyword": keyword,
        "opportunity_score": opportunity_score,
        "search_volume": vol_label,
        "competition": comp_label,
        "trend_direction": trend,
        "volume_index": vol_index,
        "competition_index": comp_index,
        "related_keywords": volume_data.get("related", []),
    }


async def _get_search_volume(keyword: str) -> dict:
    """Get relative search volume from Google Trends via pytrends."""
    def _fetch():
        try:
            from pytrends.request import TrendReq
            pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
            pytrends.build_payload([keyword], cat=0, timeframe="today 3-m")

            # Interest over time
            interest = pytrends.interest_over_time()
            if interest.empty or keyword not in interest.columns:
                return {"volume_index": 50, "trend_direction": "stable", "related": []}

            values = interest[keyword].tolist()
            avg = sum(values) / len(values) if values else 50

            # Trend direction: compare last 2 weeks vs first 2 weeks
            if len(values) >= 4:
                recent = sum(values[-2:]) / 2
                early = sum(values[:2]) / 2
                if recent > early * 1.2:
                    direction = "rising"
                elif recent < early * 0.8:
                    direction = "declining"
                else:
                    direction = "stable"
            else:
                direction = "stable"

            # Related queries
            related = []
            try:
                related_df = pytrends.related_queries()
                if keyword in related_df and related_df[keyword].get("top") is not None:
                    top = related_df[keyword]["top"]
                    related = top["query"].head(5).tolist()
            except Exception as e:
                logger.debug(f"pytrends related queries failed for '{keyword}': {e}")

            return {
                "volume_index": int(min(avg, 100)),
                "trend_direction": direction,
                "related": related,
            }
        except Exception as e:
            logger.warning(f"pytrends failed for '{keyword}': {e}")
            return {"volume_index": 50, "trend_direction": "stable", "related": []}

    return await asyncio.to_thread(_fetch)


async def _get_competition(keyword: str, youtube_api_key: str = "") -> dict:
    """Estimate competition from YouTube search result count."""
    if not youtube_api_key:
        from backend.config import settings as env
        youtube_api_key = env.YOUTUBE_API_KEY

    if not youtube_api_key:
        return {"competition_index": 50}

    def _fetch():
        try:
            import httpx
            resp = httpx.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "part": "snippet",
                    "q": keyword,
                    "type": "video",
                    "maxResults": 1,
                    "key": youtube_api_key,
                    "order": "relevance",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                return {"competition_index": 50}

            data = resp.json()
            total = data.get("pageInfo", {}).get("totalResults", 0)

            # Map total results to 0-100 competition index
            # <10K results = low, 10K-500K = medium, >500K = high
            if total < 10_000:
                idx = int(total / 10_000 * 30)  # 0-30
            elif total < 500_000:
                idx = 30 + int((total - 10_000) / 490_000 * 40)  # 30-70
            else:
                idx = min(70 + int((total - 500_000) / 5_000_000 * 30), 100)  # 70-100

            return {"competition_index": idx}
        except Exception as e:
            logger.warning(f"YouTube competition check failed for '{keyword}': {e}")
            return {"competition_index": 50}

    return await asyncio.to_thread(_fetch)


async def discover_rising_keywords(seed_niche: str, max_results: int = 15) -> list[dict]:
    """
    Discover rising keywords related to a seed niche using Google Trends.
    Returns keywords sorted by growth velocity (rising queries).
    """
    def _discover():
        try:
            from pytrends.request import TrendReq
            pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
            pytrends.build_payload([seed_niche], cat=0, timeframe="today 3-m")

            results = []

            # Get rising related queries (keywords gaining momentum)
            related = pytrends.related_queries()
            if seed_niche in related:
                rising_df = related[seed_niche].get("rising")
                if rising_df is not None and not rising_df.empty:
                    for _, row in rising_df.head(max_results).iterrows():
                        query = row.get("query", "")
                        value = row.get("value", 0)
                        results.append({
                            "keyword": query,
                            "growth_pct": int(value) if value != "Breakout" else 5000,
                            "is_breakout": value == "Breakout" or (isinstance(value, (int, float)) and value >= 5000),
                            "source": "rising_query",
                        })

                # Also get top queries for context
                top_df = related[seed_niche].get("top")
                if top_df is not None and not top_df.empty:
                    existing_kws = {r["keyword"] for r in results}
                    for _, row in top_df.head(5).iterrows():
                        query = row.get("query", "")
                        if query not in existing_kws:
                            results.append({
                                "keyword": query,
                                "growth_pct": 0,
                                "is_breakout": False,
                                "source": "top_query",
                            })

            # Get related topics
            try:
                topics = pytrends.related_topics()
                if seed_niche in topics:
                    rising_topics = topics[seed_niche].get("rising")
                    if rising_topics is not None and not rising_topics.empty:
                        existing_kws = {r["keyword"] for r in results}
                        for _, row in rising_topics.head(5).iterrows():
                            title = row.get("topic_title", "")
                            if title and title not in existing_kws:
                                results.append({
                                    "keyword": title,
                                    "growth_pct": int(row.get("value", 0)) if row.get("value") != "Breakout" else 5000,
                                    "is_breakout": row.get("value") == "Breakout",
                                    "source": "rising_topic",
                                })
            except Exception as e:
                logger.debug(f"pytrends related topics failed for '{seed_niche}': {e}")

            results.sort(key=lambda x: x["growth_pct"], reverse=True)
            return results[:max_results]

        except Exception as e:
            logger.warning(f"Rising keywords discovery failed for '{seed_niche}': {e}")
            return []

    return await asyncio.to_thread(_discover)
