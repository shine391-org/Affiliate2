"""Tests for Phase 3.2 remake pipeline."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.radar.remake.adaptation import validate_adaptation
from src.radar.remake.media import (
    SegmentPlan, Segment, validate_segment_plan,
    get_dimensions, get_duration,
)
from src.radar.remake.pipeline import _parse_time, _build_segment_plan


class TestAdaptationValidation(unittest.TestCase):
    """Test adaptation.json validation."""

    def test_valid_adaptation(self):
        adaptation = {
            "content_id": "123",
            "platform": "douyin",
            "original": {"hook": "h", "structure": "s", "value_proposition": "v"},
            "vietnamese_adaptation": {
                "audience": "gen z",
                "angle": "test",
                "hook": "test hook",
                "script": "test script",
                "sections": [{"time": "0-5s", "text": "hi", "purpose": "hook"}],
                "cta": "follow",
            },
            "localization_changes": {
                "ingredients": [],
                "cultural_references": [],
                "terminology": [],
                "pacing": "",
                "sponsor_product_removal": "",
            },
            "reuse_plan": {
                "source_segments": ["0-10s"],
                "segments_to_drop": [],
                "segments_to_reorder": [],
                "additional_visual_needed": [],
            },
            "risk_notes": [],
            "target_duration": "20-35s",
        }
        errors = validate_adaptation(adaptation)
        self.assertEqual(errors, [])

    def test_missing_required_fields(self):
        adaptation = {"content_id": "123", "platform": "douyin"}
        errors = validate_adaptation(adaptation)
        self.assertTrue(any("missing" in e for e in errors))

    def test_missing_vietnamese_adaptation_fields(self):
        adaptation = {
            "content_id": "123",
            "platform": "douyin",
            "original": {},
            "vietnamese_adaptation": {"audience": "gen z"},
            "localization_changes": {},
            "reuse_plan": {
                "source_segments": [],
                "segments_to_drop": [],
                "segments_to_reorder": [],
                "additional_visual_needed": [],
            },
            "target_duration": "20-35s",
        }
        errors = validate_adaptation(adaptation)
        self.assertTrue(any("vietnamese_adaptation missing" in e for e in errors))


class TestSegmentPlan(unittest.TestCase):
    """Test segment plan validation."""

    def test_valid_plan(self):
        plan = SegmentPlan(
            source_path="test.mp4",
            segments=[
                Segment(start=0, end=10, purpose="hook"),
                Segment(start=10, end=20, purpose="content"),
            ],
        )
        errors = validate_segment_plan(plan, 30.0)
        self.assertEqual(errors, [])

    def test_negative_start(self):
        plan = SegmentPlan(
            source_path="test.mp4",
            segments=[Segment(start=-1, end=10, purpose="hook")],
        )
        errors = validate_segment_plan(plan, 30.0)
        self.assertTrue(any("start < 0" in e for e in errors))

    def test_end_before_start(self):
        plan = SegmentPlan(
            source_path="test.mp4",
            segments=[Segment(start=10, end=5, purpose="hook")],
        )
        errors = validate_segment_plan(plan, 30.0)
        self.assertTrue(any("end <= start" in e for e in errors))

    def test_end_exceeds_duration(self):
        plan = SegmentPlan(
            source_path="test.mp4",
            segments=[Segment(start=0, end=100, purpose="hook")],
        )
        errors = validate_segment_plan(plan, 30.0)
        self.assertTrue(any("end" in e and "duration" in e for e in errors))


class TestParseTime(unittest.TestCase):
    """Test time string parsing."""

    def test_seconds_only(self):
        self.assertEqual(_parse_time("15s"), 15.0)

    def test_mm_ss(self):
        self.assertEqual(_parse_time("1:30"), 90.0)

    def test_hh_mm_ss(self):
        self.assertEqual(_parse_time("1:00:00"), 3600.0)

    def test_plain_number(self):
        self.assertEqual(_parse_time("45"), 45.0)


class TestBuildSegmentPlan(unittest.TestCase):
    """Test segment plan building from adaptation."""

    def test_from_sections(self):
        adaptation = {
            "vietnamese_adaptation": {
                "sections": [
                    {"time": "0-5s", "text": "hook", "purpose": "hook"},
                    {"time": "5-15s", "text": "content", "purpose": "content"},
                    {"time": "15-25s", "text": "outro", "purpose": "CTA"},
                ],
            },
            "reuse_plan": {
                "source_segments": [],
                "segments_to_drop": [],
                "segments_to_reorder": [],
                "additional_visual_needed": [],
            },
        }
        plan = _build_segment_plan(adaptation, 30.0, Path("test.mp4"))
        self.assertEqual(len(plan.segments), 3)
        self.assertEqual(plan.segments[0].start, 0)
        self.assertEqual(plan.segments[0].end, 5)

    def test_fallback_full_source(self):
        adaptation = {
            "vietnamese_adaptation": {"sections": []},
            "reuse_plan": {
                "source_segments": [],
                "segments_to_drop": [],
                "segments_to_reorder": [],
                "additional_visual_needed": [],
            },
        }
        plan = _build_segment_plan(adaptation, 30.0, Path("test.mp4"))
        self.assertEqual(len(plan.segments), 1)
        self.assertEqual(plan.segments[0].start, 0)
        self.assertEqual(plan.segments[0].end, 30.0)


class TestMediaProbe(unittest.TestCase):
    """Test media probing on real source files."""

    def test_probe_douyin_source(self):
        source = ROOT / "data/candidates/douyin_7653466619146170234/source.mp4"
        if not source.exists():
            self.skipTest("Source not available")
        dims = get_dimensions(source)
        self.assertEqual(dims, (1080, 1920))

    def test_probe_bilibili_source(self):
        source = ROOT / "data/candidates/bilibili_117272756624085/source.mp4"
        if not source.exists():
            self.skipTest("Source not available")
        dur = get_duration(source)
        self.assertGreater(dur, 0)


if __name__ == "__main__":
    unittest.main()
