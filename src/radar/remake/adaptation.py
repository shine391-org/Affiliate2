"""Phase 3.2 — Vietnamese Creative Adaptation.

Uses Gemini to produce adaptation.json for each pilot candidate.
NOT literal translation — creative remake for Vietnamese short-form.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ViralMint path for AI provider
_VIRALMINT = Path(__file__).resolve().parents[3] / "tools" / "ViralMint"
if str(_VIRALMINT) not in sys.path:
    sys.path.insert(0, str(_VIRALMINT))

_DEFAULT_MODEL = "gemini-3.5-flash-lite"


def _load_intelligence(candidate_dir: Path) -> dict:
    """Load Phase 3.1 intelligence.json for a candidate."""
    intel_path = candidate_dir / "intelligence.json"
    if not intel_path.exists():
        raise FileNotFoundError(f"intelligence.json not found: {intel_path}")
    return json.loads(intel_path.read_text(encoding="utf-8"))


def _load_transcript(candidate_dir: Path) -> dict:
    """Load transcript.json for a candidate."""
    tx_path = candidate_dir / "transcript.json"
    if not tx_path.exists():
        raise FileNotFoundError(f"transcript.json not found: {tx_path}")
    return json.loads(tx_path.read_text(encoding="utf-8"))


def _build_adaptation_prompt(
    intel: dict,
    transcript: dict,
    target_duration: str,
    review_note: str | None = None,
) -> str:
    """Build Gemini prompt for creative Vietnamese adaptation."""
    vietnamese_angle = intel.get("vietnamese_angle", {})
    suggested_hook = vietnamese_angle.get("suggested_hook", "")
    angle_notes = vietnamese_angle.get("notes", "")

    # Extract transcript text
    segments = transcript.get("segments", [])
    transcript_text = " ".join(s.get("text", "") for s in segments[:30])

    # Structure summary
    structure = intel.get("structure", [])
    structure_lines = []
    for s in structure:
        sec = s.get("section", "?")
        time_range = s.get("approximate_time", "?")
        purpose = s.get("purpose", "")
        structure_lines.append(f"- {sec} ({time_range}): {purpose}")
    structure_text = "\n".join(structure_lines) if structure_lines else "(no structure data)"

    prompt = f"""You are a Vietnamese short-form video creative director. Your job is to adapt a Chinese viral video concept into a Vietnamese remake prototype for internal testing.

## ORIGINAL VIDEO INTELLIGENCE
Topic: {intel.get('topic', 'Unknown')}
Subtopic: {intel.get('subtopic', 'Unknown')}
Content Format: {intel.get('content_format', {}).get('type', 'Unknown')}
Presentation: {intel.get('content_format', {}).get('presentation_style', 'Unknown')}

Original Hook: {intel.get('hook', {}).get('text', 'N/A')}
Hook Type: {intel.get('hook', {}).get('type', 'N/A')}

Value Proposition: {intel.get('value_proposition', 'N/A')}
Emotional Trigger: {intel.get('emotional_trigger', 'N/A')}

Why It May Work:
{chr(10).join(f'- {r}' for r in intel.get('why_it_may_work', []))}

## VIDEO STRUCTURE
{structure_text}

## TRANSCRIPT (first 30 segments)
{transcript_text}

## EXISTING VIETNAMESE ANGLE (from Phase 3.1)
Concept: {vietnamese_angle.get('concept', 'N/A')}
Suggested Hook: {suggested_hook}
Notes: {angle_notes}

## HUMAN REWORK FEEDBACK
{review_note.strip() if review_note else "(none — first render)"}

## TARGET
- Target duration: {target_duration}
- Language: Vietnamese (Tiếng Việt)
- Format: 9:16 vertical short-form
- Audience: Vietnamese Gen Z / young adults on TikTok/Shorts
- Goal: Creative remake — NOT literal translation

## RULES
1. Keep the original winning idea and hook mechanism
2. Change Chinese cultural references to Vietnamese ones
3. Replace ingredients/products with local Vietnamese equivalents
4. Remove sponsor mentions, creator-specific jokes, irrelevant CTAs
5. Do NOT invent health/weight-loss claims
6. Script must sound native — spoken Vietnamese short-form
7. Optimize for 20-60 second vertical video
8. If HUMAN REWORK FEEDBACK is present, explicitly correct that problem while preserving the strong parts of the previous concept

Respond with JSON only — no markdown, no commentary. Schema:

{{
  "content_id": "{intel.get('content_id', '')}",
  "platform": "{intel.get('platform', '')}",
  "original": {{
    "hook": "original hook text",
    "structure": "brief structure description",
    "value_proposition": "original value prop"
  }},
  "vietnamese_adaptation": {{
    "audience": "target audience description",
    "angle": "the creative angle for Vietnamese market",
    "hook": "Vietnamese hook text (spoken, natural)",
    "script": "full Vietnamese script (spoken style, natural)",
    "sections": [
      {{"time": "0-5s", "text": "Vietnamese text", "purpose": "hook/intro"}},
      {{"time": "5-15s", "text": "Vietnamese text", "purpose": "content"}},
      {{"time": "15-25s", "text": "Vietnamese text", "purpose": "CTA/outro"}}
    ],
    "cta": "call to action in Vietnamese"
  }},
  "localization_changes": {{
    "ingredients": ["list of ingredient swaps"],
    "cultural_references": ["list of cultural reference changes"],
    "terminology": ["list of terminology changes"],
    "pacing": "pacing adjustment notes",
    "sponsor_product_removal": "what was removed and why"
  }},
  "reuse_plan": {{
    "source_segments": ["list of source time ranges to reuse"],
    "segments_to_drop": ["list of source time ranges to drop"],
    "segments_to_reorder": ["list of segments to reorder"],
    "additional_visual_needed": ["list of any additional visuals needed"]
  }},
  "risk_notes": ["list of risks and concerns"],
  "target_duration": "{target_duration}"
}}"""
    return prompt


async def _call_gemini(prompt: str) -> dict:
    """Call Gemini via ViralMint's AI provider."""
    from backend.core.ai_provider import AIClient, AIProvider
    from backend.config import settings

    # Configure compatible provider
    api_key = (
        getattr(settings, "AI_API_KEY", "")
        or os.getenv("AI_API_KEY", "")
        or getattr(settings, "GEMINI_API_KEY", "")
        or os.getenv("GEMINI_API_KEY", "")
    ).strip()
    base_url = (
        getattr(settings, "AI_BASE_URL", "")
        or os.getenv("AI_BASE_URL", "")
    ).strip()
    model = (
        getattr(settings, "AI_MODEL", "")
        or os.getenv("AI_MODEL", "")
        or _DEFAULT_MODEL
    ).strip()

    if not api_key:
        from backend.core.exceptions import AIKeyMissingError
        raise AIKeyMissingError("GEMINI_API_KEY required for adaptation")

    if not base_url:
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"

    client = AIClient(
        provider=AIProvider.COMPATIBLE,
        api_key=api_key,
        model=model,
        base_url=base_url,
    )

    messages = [{"role": "user", "content": prompt}]
    response = await client.chat(messages=messages, max_tokens=4096)

    # Parse JSON from response
    text = response.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first line (```json or ```) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        log.warning("Gemini response not valid JSON: %s... text=%s", str(e)[:100], text[:200])
        # Try to extract JSON from text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Gemini response could not be parsed as JSON: {e}")


async def generate_adaptation(
    candidate_dir: Path,
    output_dir: Path,
    target_duration: str = "20-35s",
    use_cache: bool = True,
    review_note: str | None = None,
) -> dict:
    """Generate adaptation.json for a candidate.

    Args:
        candidate_dir: Path to candidate directory (with intelligence.json, transcript.json)
        output_dir: Path to output directory (data/remakes/<id>/)
        target_duration: Target duration string (e.g. "20-35s", "35-60s")
        use_cache: If True, return cached adaptation.json if it exists
        review_note: Optional human REWORK feedback. Presence forces a fresh adaptation.

    Returns:
        adaptation dict
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "adaptation.json"

    if use_cache and not review_note and out_path.exists():
        log.info("Using cached adaptation: %s", out_path)
        return json.loads(out_path.read_text(encoding="utf-8"))

    intel = _load_intelligence(candidate_dir)
    transcript = _load_transcript(candidate_dir)
    prompt = _build_adaptation_prompt(intel, transcript, target_duration, review_note=review_note)

    log.info("Calling Gemini for adaptation: %s", candidate_dir.name)
    adaptation = await _call_gemini(prompt)

    # Validate required fields
    required = ["content_id", "platform", "vietnamese_adaptation", "reuse_plan"]
    for field in required:
        if field not in adaptation:
            raise ValueError(f"adaptation.json missing required field: {field}")

    va = adaptation.get("vietnamese_adaptation", {})
    if not va.get("script"):
        raise ValueError("adaptation.json missing vietnamese_adaptation.script")
    if not va.get("hook"):
        raise ValueError("adaptation.json missing vietnamese_adaptation.hook")

    out_path.write_text(
        json.dumps(adaptation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Adaptation saved: %s", out_path)
    return adaptation


def validate_adaptation(adaptation: dict) -> list[str]:
    """Validate adaptation.json structure. Returns list of errors."""
    errors = []
    required_top = ["content_id", "platform", "original", "vietnamese_adaptation", "localization_changes", "reuse_plan", "target_duration"]
    for field in required_top:
        if field not in adaptation:
            errors.append(f"missing required field: {field}")

    va = adaptation.get("vietnamese_adaptation", {})
    va_required = ["audience", "angle", "hook", "script", "sections", "cta"]
    for field in va_required:
        if not va.get(field):
            errors.append(f"vietnamese_adaptation missing: {field}")

    reuse = adaptation.get("reuse_plan", {})
    reuse_required = ["source_segments", "segments_to_drop", "segments_to_reorder", "additional_visual_needed"]
    for field in reuse_required:
        if field not in reuse:
            errors.append(f"reuse_plan missing: {field}")

    return errors
