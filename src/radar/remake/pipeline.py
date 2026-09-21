"""Phase 3.2 — Remake pipeline orchestrator.

Ties together adaptation, TTS, media processing, and caption burn
to produce 3 Vietnamese remake prototypes.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .adaptation import generate_adaptation, validate_adaptation
from .media import (
    SegmentPlan, Segment, probe_video, get_duration, get_dimensions,
    extract_segment, concat_segments, mux_audio_video, validate_segment_plan,
)
from .tts import generate_tts, get_duration as get_audio_duration, DEFAULT_VOICE_MALE, DEFAULT_VOICE_FEMALE
from .caption import burn_captions_timed

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
CANDIDATES_DIR = ROOT / "data" / "candidates"
REMAKES_DIR = ROOT / "data" / "remakes"


@dataclass
class RemakeResult:
    """Result of a remake pipeline run."""
    candidate_id: str
    platform: str
    content_id: str
    adaptation_path: str | None = None
    segment_plan_path: str | None = None
    audio_path: str | None = None
    video_no_audio_path: str | None = None
    final_path: str | None = None
    duration_original: float = 0.0
    duration_final: float = 0.0
    target_duration: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.final_path is not None and Path(self.final_path).exists()

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "platform": self.platform,
            "content_id": self.content_id,
            "adaptation_path": self.adaptation_path,
            "segment_plan_path": self.segment_plan_path,
            "audio_path": self.audio_path,
            "video_no_audio_path": self.video_no_audio_path,
            "final_path": self.final_path,
            "duration_original": self.duration_original,
            "duration_final": self.duration_final,
            "target_duration": self.target_duration,
            "success": self.success,
            "errors": self.errors,
            "warnings": self.warnings,
        }



def _build_segment_plan(
    adaptation: dict,
    source_duration: float,
    source_path: Path,
) -> SegmentPlan:
    """Build segment plan from adaptation data."""
    reuse = adaptation.get("reuse_plan", {})
    sections = adaptation.get("vietnamese_adaptation", {}).get("sections", [])

    segments = []

    # Use Vietnamese adaptation sections to determine output timing
    for sec in sections:
        time_str = sec.get("time", "")
        text = sec.get("text", "")
        purpose = sec.get("purpose", "content")
        try:
            if "-" in time_str:
                parts = time_str.split("-")
                start = _parse_time(parts[0].strip())
                end = _parse_time(parts[1].strip())
            else:
                start = 0
                end = min(5, source_duration)
            segments.append(Segment(
                start=start,
                end=min(end, source_duration),
                purpose=purpose,
                keep=True,
                narration_section=text,
            ))
        except (ValueError, IndexError) as e:
            log.warning("Could not parse section time '%s': %s", time_str, e)

    # Fallback: use entire source
    if not segments:
        segments.append(Segment(
            start=0,
            end=source_duration,
            purpose="full_source",
            keep=True,
        ))

    return SegmentPlan(
        source_path=str(source_path),
        segments=segments,
    )


def _parse_time(time_str: str) -> float:
    """Parse time string like '0:15', '15s', '1:30' to seconds."""
    time_str = time_str.strip()
    if time_str.endswith("s"):
        return float(time_str[:-1])
    if ":" in time_str:
        parts = time_str.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return float(time_str)


async def run_remake_pipeline(
    candidate_id: str,
    target_duration: str = "20-35s",
    voice_id: str = DEFAULT_VOICE_MALE,
    use_cache: bool = True,
) -> RemakeResult:
    """Run the full remake pipeline for a candidate.

    Args:
        candidate_id: Directory name under data/candidates/ (e.g. "douyin_7653466619146170234")
        target_duration: Target duration string
        voice_id: Edge TTS voice ID
        use_cache: Use cached adaptation if available

    Returns:
        RemakeResult with paths and status
    """
    candidate_dir = CANDIDATES_DIR / candidate_id
    output_dir = REMAKES_DIR / candidate_id

    result = RemakeResult(
        candidate_id=candidate_id,
        platform="",
        content_id="",
        target_duration=target_duration,
    )

    try:
        # 1. Load source media info
        source_path = candidate_dir / "source.mp4"
        if not source_path.exists():
            result.errors.append(f"Source not found: {source_path}")
            return result

        result.duration_original = get_duration(source_path)
        src_w, src_h = get_dimensions(source_path)
        log.info("Source: %s (%dx%d, %.1fs)", candidate_id, src_w, src_h, result.duration_original)

        # 2. Generate adaptation
        try:
            adaptation = await generate_adaptation(
                candidate_dir=candidate_dir,
                output_dir=output_dir,
                target_duration=target_duration,
                use_cache=use_cache,
            )
            result.adaptation_path = str(output_dir / "adaptation.json")
            result.platform = adaptation.get("platform", "")
            result.content_id = adaptation.get("content_id", "")

            # Validate
            errors = validate_adaptation(adaptation)
            if errors:
                result.warnings.extend(errors)
        except Exception as e:
            result.errors.append(f"Adaptation failed: {e}")
            return result

        # 3. Build segment plan
        try:
            plan = _build_segment_plan(adaptation, result.duration_original, source_path)
            plan_errors = validate_segment_plan(plan, result.duration_original)
            if plan_errors:
                result.warnings.extend(plan_errors)

            plan_path = output_dir / "segment_plan.json"
            plan_path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            result.segment_plan_path = str(plan_path)
        except Exception as e:
            result.errors.append(f"Segment plan failed: {e}")
            return result

        # 4. Extract and assemble segments
        try:
            segment_files = []
            for i, seg in enumerate(plan.segments):
                if not seg.keep:
                    continue
                seg_path = output_dir / f"segment_{i:02d}.mp4"
                extract_segment(source_path, seg_path, seg.start, seg.end)
                segment_files.append(seg_path)

            if len(segment_files) == 1:
                video_no_audio = segment_files[0]
            else:
                video_no_audio = output_dir / "assembled.mp4"
                concat_segments(segment_files, video_no_audio)

            result.video_no_audio_path = str(video_no_audio)
        except Exception as e:
            result.errors.append(f"Segment extraction failed: {e}")
            return result

        # 5. Generate TTS audio
        try:
            script = adaptation.get("vietnamese_adaptation", {}).get("script", "")
            if not script:
                result.errors.append("No script in adaptation")
                return result

            audio_path = output_dir / "vietnamese_audio.mp3"
            await generate_tts(script, audio_path, voice_id)
            result.audio_path = str(audio_path)
        except Exception as e:
            result.errors.append(f"TTS failed: {e}")
            return result

        # 6. Mux audio + video
        try:
            muxed_path = output_dir / "muxed.mp4"
            mux_audio_video(video_no_audio, audio_path, muxed_path)
        except Exception as e:
            result.errors.append(f"Mux failed: {e}")
            return result

        # 7. Burn captions
        try:
            sections = adaptation.get("vietnamese_adaptation", {}).get("sections", [])
            if sections:
                # Convert section time strings to start/end floats
                timed_sections = []
                for sec in sections:
                    time_str = sec.get("time", "")
                    text = sec.get("text", "")
                    try:
                        if "-" in time_str:
                            parts = time_str.split("-")
                            start = _parse_time(parts[0].strip())
                            end = _parse_time(parts[1].strip())
                        else:
                            start = 0
                            end = 5
                        timed_sections.append({"start": start, "end": end, "text": text})
                    except (ValueError, IndexError):
                        pass

                final_path = output_dir / "final.mp4"
                burn_captions_timed(muxed_path, final_path, timed_sections)
            else:
                # No sections — just copy muxed as final
                final_path = output_dir / "final.mp4"
                import shutil
                shutil.copy2(muxed_path, final_path)

            result.final_path = str(final_path)
            result.duration_final = get_duration(final_path)
        except Exception as e:
            result.errors.append(f"Caption burn failed: {e}")
            return result

        log.info("Remake complete: %s -> %s (%.1fs)", candidate_id, result.final_path, result.duration_final)

    except Exception as e:
        result.errors.append(f"Pipeline error: {e}")

    return result


async def run_all_pilots(
    pilots: list[dict],
    use_cache: bool = True,
) -> list[RemakeResult]:
    """Run remake pipeline for all pilots.

    Args:
        pilots: List of dicts with keys: candidate_id, target_duration, voice_id
        use_cache: Use cached adaptations

    Returns:
        List of RemakeResult
    """
    results = []
    for pilot in pilots:
        log.info("=== Starting pilot: %s ===", pilot["candidate_id"])
        result = await run_remake_pipeline(
            candidate_id=pilot["candidate_id"],
            target_duration=pilot.get("target_duration", "20-35s"),
            voice_id=pilot.get("voice_id", DEFAULT_VOICE_MALE),
            use_cache=use_cache,
        )
        results.append(result)
        log.info("=== Pilot %s: %s ===", pilot["candidate_id"], "SUCCESS" if result.success else "FAILED")
    return results


def generate_review_report(results: list[RemakeResult], output_path: Path) -> Path:
    """Generate PHASE_3_2_REMAKE_REVIEW.md from results."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Phase 3.2 — Vietnamese Remake Review",
        "",
        f"- Total pilots: {len(results)}",
        f"- Successful: {sum(1 for r in results if r.success)}",
        f"- Failed: {sum(1 for r in results if not r.success)}",
        "",
    ]

    for r in results:
        lines.append(f"## Pilot: {r.candidate_id}")
        lines.append("")
        lines.append(f"- Platform: {r.platform}")
        lines.append(f"- Content ID: {r.content_id}")
        lines.append(f"- Original duration: {r.duration_original:.1f}s")
        lines.append(f"- Final duration: {r.duration_final:.1f}s")
        lines.append(f"- Target duration: {r.target_duration}")
        lines.append(f"- Status: {'SUCCESS' if r.success else 'FAILED'}")
        lines.append(f"- Final path: {r.final_path}")
        lines.append("")

        if r.errors:
            lines.append("### Errors")
            for e in r.errors:
                lines.append(f"- {e}")
            lines.append("")

        if r.warnings:
            lines.append("### Warnings")
            for w in r.warnings:
                lines.append(f"- {w}")
            lines.append("")

        # Load adaptation for details
        if r.adaptation_path and Path(r.adaptation_path).exists():
            try:
                adaptation = json.loads(Path(r.adaptation_path).read_text(encoding="utf-8"))
                va = adaptation.get("vietnamese_adaptation", {})
                lines.append("### Vietnamese Adaptation")
                lines.append("")
                lines.append(f"**Audience:** {va.get('audience', 'N/A')}")
                lines.append("")
                lines.append(f"**Angle:** {va.get('angle', 'N/A')}")
                lines.append("")
                lines.append(f"**Hook:** {va.get('hook', 'N/A')}")
                lines.append("")
                lines.append(f"**Script:** {va.get('script', 'N/A')}")
                lines.append("")
                lines.append(f"**CTA:** {va.get('cta', 'N/A')}")
                lines.append("")

                loc = adaptation.get("localization_changes", {})
                if loc:
                    lines.append("### Localization Changes")
                    lines.append("")
                    for key, val in loc.items():
                        lines.append(f"- **{key}:** {val}")
                    lines.append("")

                risks = adaptation.get("risk_notes", [])
                if risks:
                    lines.append("### Risk Notes")
                    for risk in risks:
                        lines.append(f"- {risk}")
                    lines.append("")
            except Exception:
                lines.append("### Adaptation Details")
                lines.append("(could not load adaptation.json)")
                lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
