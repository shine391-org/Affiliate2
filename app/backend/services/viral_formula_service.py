# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Cross-Video Viral Formula Generator.

Analyzes patterns across 5+ competitor videos in the same niche to produce
a "Viral Formula" — a structured document identifying common patterns,
optimal structure, tone, and content gaps.
"""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

FORMULA_PROMPT = """I have analyzed {n} competitor videos in the "{niche}" niche.
Here are their individual analyses (insights + segment scores):

{analyses_json}

Based on patterns across ALL these videos, generate a "Viral Formula" document.

Return JSON only (no markdown):
{{
  "hook_patterns": {{
    "dominant_style": "curiosity-gap | shock-stat | question | story-opening | direct-claim",
    "avg_hook_duration_seconds": 5,
    "common_techniques": ["technique used by 60%+ of videos"],
    "example_phrases": ["actual phrases from the transcripts that work"],
    "confidence": 0.8
  }},
  "optimal_structure": {{
    "dominant_pattern": "hook-problem-solution | listicle | story-arc | tutorial | reaction",
    "avg_section_count": 5,
    "avg_duration_seconds": 90,
    "key_insight_position_pct": 30,
    "structure_notes": "What all successful videos do with their structure",
    "confidence": 0.7
  }},
  "tone_and_style": {{
    "formality": "casual | semi-formal | formal",
    "speaking_pace": "fast | medium | slow",
    "humor_level": "none | light | moderate | heavy",
    "emotion_style": "authority | empathy | excitement | controversy",
    "first_person_usage": true,
    "confidence": 0.6
  }},
  "ideal_metrics": {{
    "optimal_duration_range": [60, 120],
    "target_engagement_rate_pct": 5.0,
    "avg_virality_score": 75.0,
    "confidence": 0.5
  }},
  "content_gaps": {{
    "untouched_angles": ["angle no one has tried yet"],
    "audience_wants_more": ["topics hinted at but not covered deeply"],
    "underserved_platforms": ["platform where this niche has less competition"],
    "confidence": 0.6
  }},
  "winning_formula_summary": "3-4 sentence synthesis: the exact recipe for going viral in this niche"
}}

RULES:
- confidence: 0.0-1.0, based on how consistent the pattern is across videos
- Be specific — reference actual content from the analyses, not generic advice
- If a pattern appears in 70%+ of videos, confidence >= 0.7
- If only in 40-70%, confidence 0.4-0.7
- winning_formula_summary should be actionable enough that someone could follow it to create a video"""


async def generate_viral_formula(
    niche: str,
    analyses: list[dict],
    ai_client,
) -> Optional[dict]:
    """
    Generate cross-video viral formula from multiple video analyses.
    Requires at least 3 analyses to find meaningful patterns.
    """
    if len(analyses) < 3:
        logger.info(f"Not enough analyses for viral formula (need 3+, got {len(analyses)})")
        return None

    # Trim each analysis to key fields to fit in context
    trimmed = []
    for a in analyses[:15]:  # max 15 videos
        trimmed.append({
            "hook": a.get("hook", ""),
            "structure": a.get("structure", ""),
            "tone": a.get("tone", ""),
            "why_viral": a.get("why_viral", ""),
            "structural_pattern": a.get("structural_pattern", ""),
            "scores": a.get("scores", {}),
            "suggested_angle": a.get("suggested_angle", ""),
            "key_phrases": a.get("key_phrases", []),
        })

    prompt = FORMULA_PROMPT.format(
        n=len(trimmed),
        niche=niche,
        analyses_json=json.dumps(trimmed, indent=1)[:6000],
    )

    try:
        response = await ai_client.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
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
        logger.error(f"Viral formula generation failed: {e}")
        return None
