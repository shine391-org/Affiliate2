# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Prompt templates and metadata generation for the video generation pipeline."""

PLATFORM_GUIDELINES = {
    "tiktok": """PLATFORM: TikTok (9:16 vertical)
- Hook MUST be in the first 1 second (no intro, no greeting)
- Total duration: 30-60 seconds ideal
- Pacing: fast cuts every 2-3 seconds
- Tone: casual, direct, slightly provocative
- End with a question or CTA to boost comments
- Use pattern interrupts every 8-10 seconds""",

    "youtube_shorts": """PLATFORM: YouTube Shorts (9:16 vertical)
- Hook in first 2 seconds
- Total duration: 30-58 seconds (under 60s required)
- Slightly more informational than TikTok
- End with subscribe CTA
- Thumbnail-worthy moment in first 3 seconds""",

    "youtube_long": """PLATFORM: YouTube Long-form (16:9 horizontal)
- Hook in first 5 seconds, then brief intro (10-15s)
- Total duration: 8-15 minutes optimal for monetization
- Structure: hook > intro > 3-5 sections > conclusion > CTA
- Include chapter-worthy section breaks
- Mid-roll ad break opportunities every 3-4 minutes""",

    "instagram_reels": """PLATFORM: Instagram Reels (9:16 vertical)
- Hook in first 1 second
- Total duration: 15-30 seconds for maximum reach
- Visually driven — less talking, more showing
- End with save-worthy tip or share-worthy moment""",
}

SCRIPT_PROMPT = """You are a viral content writer. Write an original video script.

Source insights from competitor analysis:
{insights_json}

{transcript_section}

{platform_guidelines}

{search_demand_section}

Requirements:
- Duration: {duration_seconds} seconds when spoken at 150 words/minute
- Format: {aspect_ratio} ({platform_format})
- Hook: First 5 seconds must be irresistible — use the competitor's proven hook style but original content
- Structure: Follow competitor's proven structure but with completely original content and angle
- Tone: Match competitor's tone style: {tone}
- End with a strong CTA (subscribe/follow/comment)
- DO NOT copy competitor content — take the angle and structure only
- Write for spoken delivery — conversational, no markdown
- If the user asks you to translate or adapt the transcript, use the ORIGINAL TRANSCRIPT above as the source material
- If search demand keywords are provided above, naturally incorporate 2-3 of the most relevant ones into your script to maximize discoverability

Return ONLY the script text. No title, no stage directions, no [INTRO] markers.
Just the words to be spoken."""

YOUTUBE_META_PROMPT = """Generate YouTube metadata for this video.
Script: {script_preview}
Niche: {niche}

{search_demand_section}

IMPORTANT: If search demand keywords are provided above, use them strategically:
- Include the top 2-3 demand keywords naturally in the title (they're what people actually search for)
- Front-load the most searched keyword in the title
- Include ALL relevant demand keywords in tags
- Weave demand keywords into the first 2 sentences of the description

Return JSON only (no markdown):
{{
  "title": "Click-worthy title under 100 chars, with power words and demand keywords",
  "description": "Full description 200-400 words with keywords, timestamps if relevant, CTA, links placeholder",
  "tags": ["tag1", "tag2", "...up to 15 tags"],
  "category_id": "22"
}}"""

TIKTOK_META_PROMPT = """Generate TikTok post metadata.
Script: {script_preview}
Niche: {niche}

{search_demand_section}

IMPORTANT: If search demand keywords are provided, use them as hashtags and work them into the caption.

Return JSON only (no markdown):
{{
  "title": "Hook-first caption under 150 chars with 3-5 hashtags using demand keywords",
  "description": "Engaging description under 2200 chars with hashtags"
}}"""
