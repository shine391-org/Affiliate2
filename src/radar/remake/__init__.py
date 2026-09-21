"""Phase 3.2 — Vietnamese remake pipeline."""
from .adaptation import generate_adaptation, validate_adaptation
from .pipeline import run_remake_pipeline, run_all_pilots, generate_review_report, RemakeResult
from .media import probe_video, get_duration, get_dimensions, validate_segment_plan, SegmentPlan, Segment
from .tts import generate_tts, DEFAULT_VOICE_MALE, DEFAULT_VOICE_FEMALE

__all__ = [
    "generate_adaptation",
    "validate_adaptation",
    "run_remake_pipeline",
    "run_all_pilots",
    "generate_review_report",
    "RemakeResult",
    "probe_video",
    "get_duration",
    "get_dimensions",
    "validate_segment_plan",
    "SegmentPlan",
    "Segment",
    "generate_tts",
    "DEFAULT_VOICE_MALE",
    "DEFAULT_VOICE_FEMALE",
]
