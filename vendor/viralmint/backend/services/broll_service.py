# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
AI B-Roll Timing Service.

Analyzes a video script to determine optimal moments for B-roll clip insertion.
Works with both stock footage (Pexels) and AI-generated clips.
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

BROLL_TIMING_PROMPT = """Analyze this video script and determine the best moments to insert B-roll footage.
B-roll = supplementary visual clips that illustrate what the narrator is saying.

Script:
{script}

Total script duration: ~{duration} seconds

Identify 3-6 moments where a B-roll clip would enhance viewer engagement.
For each moment, suggest a search query for finding relevant stock footage.

Return JSON only (no markdown):
{{
  "broll_cues": [
    {{
      "trigger_text": "exact phrase from the script that triggers this B-roll",
      "position_pct": 15,
      "duration_seconds": 3,
      "search_query": "stock footage search query for this moment",
      "visual_description": "what the viewer should see during this B-roll",
      "purpose": "illustrate | emphasize | transition | emotional",
      "priority": "high | medium | low"
    }}
  ],
  "total_broll_seconds": 15,
  "broll_coverage_pct": 17
}}

RULES:
- position_pct: approximate position in the script (0-100%)
- duration_seconds: how long the B-roll should play (2-5 seconds typically)
- search_query: specific enough for Pexels/stock search (e.g. "person typing laptop coffee shop")
- Never place B-roll during the hook (first 5 seconds) — the speaker's face matters there
- Place B-roll during explanatory/descriptive sections, not emotional/personal moments
- Total B-roll should cover 15-30% of the video, never more than 40%
- priority "high" = the script is describing something visual that NEEDS illustration"""


async def analyze_broll_timing(
    script: str,
    duration_seconds: int = 60,
    ai_client=None,
) -> Optional[dict]:
    """
    Analyze a script and return optimal B-roll insertion points.
    """
    if not script or not ai_client:
        return None

    prompt = BROLL_TIMING_PROMPT.format(
        script=script[:3000],
        duration=duration_seconds,
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
        logger.warning(f"B-roll timing analysis failed: {e}")
        return None


def map_broll_to_timestamps(
    broll_cues: list[dict],
    word_timestamps: list[dict],
    total_duration: float,
) -> list[dict]:
    """
    Map B-roll trigger texts to exact timestamps using Whisper word-level timestamps.
    Returns cues with start_time and end_time in seconds.
    """
    if not broll_cues or not word_timestamps:
        return []

    # Build full text with positions for matching
    full_text = " ".join(w.get("word", w.get("text", "")) for w in word_timestamps).lower()

    results = []
    for cue in broll_cues:
        trigger = cue.get("trigger_text", "").lower().strip()
        if not trigger:
            # Fallback: use position_pct
            start = total_duration * cue.get("position_pct", 0) / 100.0
        else:
            # Find trigger text in word timestamps
            idx = full_text.find(trigger[:30])  # first 30 chars of trigger
            if idx >= 0:
                # Map character position to word index
                char_count = 0
                start = 0
                for w in word_timestamps:
                    word_text = w.get("word", w.get("text", ""))
                    char_count += len(word_text) + 1
                    if char_count >= idx:
                        start = w.get("start", 0)
                        break
            else:
                start = total_duration * cue.get("position_pct", 0) / 100.0

        dur = cue.get("duration_seconds", 3)
        results.append({
            **cue,
            "start_time": round(start, 2),
            "end_time": round(start + dur, 2),
        })

    return results
