# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Google Trends wrapper — finds trending search terms in a niche."""
import asyncio
import logging

logger = logging.getLogger(__name__)


async def get_trending_queries(niche: str, timeframe: str = "today 1-m") -> list[str]:
    """
    Get related trending search queries for a niche.
    Returns list of query strings to expand scout searches.
    """
    def _fetch():
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US", tz=360)
        pytrends.build_payload([niche], cat=0, timeframe=timeframe)

        try:
            related = pytrends.related_queries()
            queries = []
            for key in related:
                top = related[key].get("top")
                rising = related[key].get("rising")
                if top is not None and not top.empty:
                    queries.extend(top["query"].tolist()[:5])
                if rising is not None and not rising.empty:
                    queries.extend(rising["query"].tolist()[:5])
            return list(dict.fromkeys(queries))[:10]  # deduplicate, max 10
        except Exception as e:
            logger.warning(f"Google Trends query failed: {e}")
            return []

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        logger.error(f"Google Trends error: {e}")
        return []
