"""
intel/report.py — Human review output for Candidate Intelligence (Phase 3.1)

Generates:
  data/candidate_intelligence.json — full structured data
  data/candidate_intelligence.md  — human-readable review + shortlist table
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def _fmt_ts(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return str(iso)[:10]


def _risk_badge(risks: dict) -> str:
    """Summarize risk levels like C:LOW P:MED PR:LOW X:HIGH."""
    if not isinstance(risks, dict) or not risks:
        return "-"
    parts = []
    mapping = {
        "copyright_dependency": "C",
        "personality_dependency": "P",
        "product_dependency": "PR",
        "context_dependency": "X",
    }
    for k, short in mapping.items():
        v = risks.get(k)
        if isinstance(v, str) and v:
            parts.append(f"{short}:{v[:3].upper()}")
    return " ".join(parts) if parts else "-"


def build_report(summary: dict, candidates: list[dict]) -> tuple[str, str]:
    """
    Build JSON and Markdown reports.

    Args:
        summary: pipeline summary (with results)
        candidates: original candidate dicts (radar ranking format)

    Returns:
        (json_str, markdown_str)
    """
    # ---- JSON ----
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_summary": {
            k: v for k, v in summary.items() if k != "results"
        },
        "candidates": [],
    }
    for entry in summary["results"]:
        cand = next(
            (c for c in candidates
             if c["platform"] == entry["platform"] and c["content_id"] == entry["content_id"]),
            {},
        )
        intel = entry.get("intelligence") or {}
        out["candidates"].append({
            "rank": cand.get("rank"),
            "platform": entry["platform"],
            "content_id": entry["content_id"],
            "title": entry["title"],
            "creator": cand.get("creator_name"),
            "url": cand.get("canonical_url"),
            "radar_score": cand.get("total_score"),
            "radar_confidence": cand.get("confidence"),
            "radar_breakdown": cand.get("breakdown"),
            "media_status": entry["media_status"],
            "transcript_status": entry["transcript_status"],
            "intelligence": intel if intel else None,
        })
    json_str = json.dumps(out, ensure_ascii=False, indent=2)

    # ---- Markdown ----
    md = []
    md.append("# Candidate Intelligence Report")
    md.append("")
    md.append(f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    md.append("")
    md.append("## Pipeline Summary")
    md.append("")
    md.append(f"- Requested: **{summary['requested']}**")
    md.append(f"- Media downloaded: **{summary['media_downloaded']}** (already had: {summary['media_already']}, unavailable: {summary['media_unavailable']})")
    md.append(f"- Transcripts: **{summary['transcripts']}**")
    md.append(f"- Intelligence generated: **{summary['intelligence_generated']}** (cached: {summary['intelligence_cached']}, errors: {summary['intelligence_errors']})")
    md.append(f"- Model calls: {summary['model_calls']} (retries: {summary['model_retries']})")
    md.append("")

    # Shortlist table
    md.append("## Shortlist (factual criteria — human decides)")
    md.append("")
    md.append("| # | Platform | Title | Creator | Score | Conf | Difficulty | Localization | Risks (C/P/PR/X) | Media |")
    md.append("|---|----------|-------|---------|-------|------|------------|--------------|------------------|-------|")
    for i, c in enumerate(out["candidates"], 1):
        intel = c.get("intelligence") or {}
        title = (c.get("title") or "-")[:45].replace("|", "/").replace("\n", " ")
        creator = (c.get("creator") or "-")[:14].replace("|", "/")
        md.append(
            f"| {i} | {c['platform']} | {title} | {creator} "
            f"| {c.get('radar_score', '-')} | {c.get('radar_confidence', '-')} "
            f"| {intel.get('remake_difficulty', '-')} | {intel.get('localization_potential', '-')} "
            f"| {_risk_badge(intel.get('risks', {}))} | {c['media_status'][:8]} |"
        )
    md.append("")

    # Per-candidate detail
    md.append("## Candidate Details")
    md.append("")
    for i, c in enumerate(out["candidates"], 1):
        intel = c.get("intelligence") or {}
        bd = c.get("radar_breakdown") or {}
        md.append(f"### #{i} [{c['platform']}] {c.get('title') or '-'}")
        md.append("")
        md.append(f"- **Creator:** {c.get('creator') or '-'}")
        md.append(f"- **URL:** {c.get('url') or '-'}")
        md.append(f"- **Radar score:** {c.get('radar_score')} (confidence {c.get('radar_confidence')})")
        md.append(f"- **Breakdown:** freshness={bd.get('freshness', '-')} engagement={bd.get('engagement', '-')} velocity={bd.get('velocity', '-')} outlier/creator={bd.get('creator', '-')}")
        md.append(f"- **Media:** {c['media_status']} | **Transcript:** {c['transcript_status']}")
        md.append("")
        if intel:
            hook = intel.get("hook", {})
            fmt = intel.get("content_format", {})
            md.append(f"- **Topic:** {intel.get('topic', '-')} / {intel.get('subtopic', '-')}")
            if hook:
                md.append(f"- **Hook:** \"{hook.get('text', '-')}\" ({hook.get('type', '-')}, ~{hook.get('first_seconds', '-')}s)")
            if fmt:
                md.append(f"- **Format:** {fmt.get('type', '-')} / {fmt.get('presentation_style', '-')}")
            md.append(f"- **Value proposition:** {intel.get('value_proposition', '-')}")
            md.append(f"- **Emotional trigger:** {intel.get('emotional_trigger', '-')}")
            if intel.get("retention_devices"):
                md.append(f"- **Retention devices:** {', '.join(intel['retention_devices'][:5])}")
            md.append(f"- **CTA:** {intel.get('cta', '-')}")
            if intel.get("why_it_may_work"):
                md.append("- **Why it may work:**")
                for w in intel["why_it_may_work"][:5]:
                    md.append(f"  - {w}")
            va = intel.get("vietnamese_angle", {})
            if va:
                md.append(f"- **Vietnamese angle:** {va.get('concept', '-')}")
                md.append(f"  - Suggested hook: {va.get('suggested_hook', '-')}")
                if va.get("notes"):
                    md.append(f"  - Notes: {va['notes']}")
            md.append(f"- **Remake difficulty:** {intel.get('remake_difficulty', '-')} | **Localization potential:** {intel.get('localization_potential', '-')}")
            risks = intel.get("risks", {})
            if risks:
                md.append(f"- **Risks:** copyright={risks.get('copyright_dependency','-')} personality={risks.get('personality_dependency','-')} product={risks.get('product_dependency','-')} context={risks.get('context_dependency','-')}")
            if intel.get("structure"):
                md.append("- **Structure:**")
                for s in intel["structure"][:8]:
                    md.append(f"  - `{s.get('approximate_time', '?')}` {s.get('section', '?')} — {s.get('purpose', '')}")
        else:
            md.append("- _(intelligence unavailable)_")
        md.append("")

    md.append("---")
    md.append("_AI output is advisory. The human team decides which candidates to remake._")
    md.append("")

    return json_str, "\n".join(md)


def write_reports(json_str: str, md_str: str) -> tuple[Path, Path]:
    """Write reports to data/candidate_intelligence.{json,md}."""
    jpath = DATA_DIR / "candidate_intelligence.json"
    mpath = DATA_DIR / "candidate_intelligence.md"
    jpath.write_text(json_str, encoding="utf-8")
    mpath.write_text(md_str, encoding="utf-8")
    return jpath, mpath
