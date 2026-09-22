# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
YouTube Search Suggest — fetches autocomplete suggestions from YouTube's suggest API.

This is the same API that powers YouTube's search bar autocomplete.
Free, no API key needed, returns what real users are searching for right now.

Usage:
  suggestions = await get_suggestions("how to save money")
  # Returns: ["how to save money fast", "how to save money as a teenager", ...]

  demand = await get_search_demand("personal finance")
  # Returns full demand analysis with related keywords for prompt injection
"""
import asyncio
import json
import logging
import re
import time
from typing import Optional

import httpx
from backend.core.http_utils import get_user_agent

logger = logging.getLogger(__name__)

# ── In-memory cache (15-min TTL — suggestions don't change fast) ────────────

_cache: dict[str, tuple[list, float]] = {}
CACHE_TTL = 900  # 15 minutes


def _cache_get(key: str) -> Optional[list]:
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
        del _cache[key]
    return None


def _cache_set(key: str, data: list):
    # Keep cache bounded
    if len(_cache) > 500:
        oldest = sorted(_cache.items(), key=lambda x: x[1][1])[:100]
        for k, _ in oldest:
            del _cache[k]
    _cache[key] = (data, time.time())


# ── Core API ────────────────────────────────────────────────────────────────

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"


async def get_suggestions(
    query: str,
    language: str = "en",
    max_results: int = 10,
) -> list[str]:
    """
    Fetch YouTube search autocomplete suggestions for a query.

    Returns a list of suggestion strings, ordered by YouTube's relevance ranking.
    These represent what real users are actually searching for.
    """
    if not query or len(query.strip()) < 2:
        return []

    query = query.strip()
    cache_key = f"suggest:{language}:{query.lower()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached[:max_results]

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                SUGGEST_URL,
                params={
                    "client": "youtube",
                    "q": query,
                    "hl": language,
                    "ds": "yt",  # YouTube data source
                },
                headers={
                    "User-Agent": get_user_agent(),
                },
            )
            resp.raise_for_status()

        # Response is JSONP: window.google.ac.h([...])
        text = resp.text
        # Extract JSON array from JSONP callback
        match = re.search(r"\((\[.*\])\)", text, re.DOTALL)
        if not match:
            logger.warning(f"Could not parse YouTube suggest response for '{query}'")
            return []

        data = json.loads(match.group(1))
        # data[1] contains the suggestions as [["suggestion", 0, [512,433]], ...]
        if len(data) < 2 or not isinstance(data[1], list):
            return []

        suggestions = []
        for item in data[1]:
            if isinstance(item, list) and len(item) > 0:
                suggestion = item[0]
                if isinstance(suggestion, str) and suggestion.strip():
                    suggestions.append(suggestion.strip())

        _cache_set(cache_key, suggestions)
        return suggestions[:max_results]

    except Exception as e:
        logger.warning(f"YouTube suggest failed for '{query}': {e}")
        return []


async def get_search_demand(
    niche: str,
    language: str = "en",
) -> dict:
    """
    Analyze search demand for a niche by expanding it into related search queries.

    Strategy: fetch suggestions for the niche itself + common question prefixes.
    This reveals what users are actually searching for and gives us high-demand
    keywords to inject into script generation and video metadata.

    Returns:
        {
            "niche": "personal finance",
            "primary_suggestions": ["personal finance for beginners", ...],
            "question_keywords": ["how to personal finance", "what is personal finance", ...],
            "long_tail_keywords": ["personal finance tips for 20s", ...],
            "top_keywords": ["personal finance", "budgeting", "save money", ...],
            "demand_summary": "High demand. Users search for: beginner guides, ..."
        }
    """
    niche = niche.strip()
    if not niche:
        return {"niche": niche, "primary_suggestions": [], "question_keywords": [],
                "long_tail_keywords": [], "top_keywords": [], "demand_summary": ""}

    # Fetch suggestions in parallel for speed
    prefixes = [
        niche,                    # direct
        f"how to {niche}",        # how-to intent
        f"what is {niche}",       # informational intent
        f"best {niche}",          # comparison intent
        f"why {niche}",           # curiosity intent
        f"{niche} for beginners", # beginner intent
        f"{niche} tips",          # actionable intent
    ]

    tasks = [get_suggestions(prefix, language, 8) for prefix in prefixes]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    primary = []
    questions = []
    long_tail = []
    seen = set()

    for i, result in enumerate(results):
        if isinstance(result, Exception) or not result:
            continue
        for suggestion in result:
            lower = suggestion.lower()
            if lower in seen:
                continue
            seen.add(lower)

            if i == 0:
                primary.append(suggestion)
            elif i <= 4:
                questions.append(suggestion)
            else:
                long_tail.append(suggestion)

    # Extract unique keywords (top-level terms that appear across suggestions)
    all_suggestions = primary + questions + long_tail
    top_keywords = _extract_top_keywords(niche, all_suggestions)

    # Build human-readable demand summary for prompt injection
    demand_summary = _build_demand_summary(niche, primary, questions, long_tail)

    return {
        "niche": niche,
        "primary_suggestions": primary[:8],
        "question_keywords": questions[:8],
        "long_tail_keywords": long_tail[:8],
        "top_keywords": top_keywords[:15],
        "demand_summary": demand_summary,
    }


def _extract_top_keywords(niche: str, suggestions: list[str]) -> list[str]:
    """Extract the most common meaningful words/phrases from suggestions."""
    # Count word frequency across all suggestions
    niche_words = set(niche.lower().split())
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                  "to", "of", "in", "for", "on", "with", "at", "by", "from",
                  "and", "or", "but", "not", "no", "do", "does", "did", "how",
                  "what", "why", "when", "where", "who", "which", "that", "this",
                  "it", "its", "i", "my", "your", "you", "we", "they", "can"}

    word_counts: dict[str, int] = {}
    for suggestion in suggestions:
        words = suggestion.lower().split()
        for word in words:
            word = word.strip(".,!?\"'()[]{}:;")
            if len(word) < 3 or word in stop_words or word in niche_words:
                continue
            word_counts[word] = word_counts.get(word, 0) + 1

    # Sort by frequency, return top keywords
    sorted_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
    return [word for word, count in sorted_words if count >= 2]


def _build_demand_summary(
    niche: str,
    primary: list[str],
    questions: list[str],
    long_tail: list[str],
) -> str:
    """Build a concise summary of search demand for prompt injection."""
    parts = []

    if primary:
        parts.append(f"Top searches: {', '.join(primary[:5])}")
    if questions:
        parts.append(f"Common questions: {', '.join(questions[:4])}")
    if long_tail:
        parts.append(f"Long-tail opportunities: {', '.join(long_tail[:3])}")

    total = len(primary) + len(questions) + len(long_tail)
    if total >= 15:
        level = "Very high"
    elif total >= 8:
        level = "High"
    elif total >= 3:
        level = "Moderate"
    else:
        level = "Low"

    header = f"Search demand for '{niche}': {level} ({total} related queries found)."
    return f"{header}\n{chr(10).join(parts)}" if parts else header
