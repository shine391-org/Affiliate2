# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Unit tests for the pure helpers in backend/core/tool_runners.py.

These guard the tricky, ffmpeg-free logic in the Tools surface (speed-change
filter graph, AI-output JSON-fence stripping, subtitle timestamp formatting)
without needing ffmpeg, Whisper, or a database — so they run fast in CI.
"""
import pytest

from backend.core.tool_runners import (
    _build_speed_filters,
    _strip_json_fence,
    _format_ts,
)


class TestBuildSpeedFilters:
    def test_video_filter_inverts_speed(self):
        v, _ = _build_speed_filters(2.0, keep_pitch=True)
        assert v == "setpts=0.500000*PTS"

    def test_single_atempo_within_band(self):
        # 0.5–2.0 fits in one atempo pass.
        _, a = _build_speed_filters(1.5, keep_pitch=True)
        assert a == "atempo=1.500000"

    def test_fast_speed_chains_atempo(self):
        # 4x needs two passes (2.0 × 2.0) — each link capped at the [0.5,2.0] band.
        _, a = _build_speed_filters(4.0, keep_pitch=True)
        assert a == "atempo=2.0,atempo=2.000000"

    def test_slow_speed_chains_atempo(self):
        # 0.25x needs a 0.5 link then the remainder.
        _, a = _build_speed_filters(0.25, keep_pitch=True)
        assert a == "atempo=0.5,atempo=0.500000"

    def test_keep_pitch_false_uses_asetrate(self):
        v, a = _build_speed_filters(2.0, keep_pitch=False)
        assert v == "setpts=0.500000*PTS"
        assert a == "asetrate=44100*2.000000,aresample=44100"


class TestStripJsonFence:
    def test_plain_json_unchanged(self):
        assert _strip_json_fence('{"a": 1}') == '{"a": 1}'

    def test_strips_json_fence(self):
        assert _strip_json_fence('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_strips_bare_fence(self):
        assert _strip_json_fence('```\n{"b": 2}\n```') == '{"b": 2}'

    def test_handles_empty_and_none(self):
        assert _strip_json_fence("") == ""
        assert _strip_json_fence(None) == ""


class TestFormatTs:
    def test_srt_uses_comma(self):
        assert _format_ts(65.5) == "00:01:05,500"

    def test_vtt_uses_dot(self):
        assert _format_ts(65.5, vtt=True) == "00:01:05.500"

    def test_hours(self):
        assert _format_ts(3661.25) == "01:01:01,250"

    def test_zero(self):
        assert _format_ts(0) == "00:00:00,000"

    def test_negative_clamped_to_zero(self):
        assert _format_ts(-5) == "00:00:00,000"
