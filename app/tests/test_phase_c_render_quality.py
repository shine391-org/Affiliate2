# SPDX-License-Identifier: AGPL-3.0-only
import pytest

from backend.services.ffmpeg_service import RENDER_PROFILES, video_encode_args


def test_render_profiles_cover_phase_c_quality_levels():
    assert set(RENDER_PROFILES) == {"draft", "balanced", "hq"}


def test_hq_uses_lower_crf_than_balanced_and_draft():
    assert RENDER_PROFILES["hq"]["crf"] < RENDER_PROFILES["balanced"]["crf"]
    assert RENDER_PROFILES["balanced"]["crf"] < RENDER_PROFILES["draft"]["crf"]


def test_encode_args_are_compatible_h264():
    args = video_encode_args("hq")
    assert args[:2] == ["-c:v", "libx264"]
    assert "-crf" in args and args[args.index("-crf") + 1] == "18"
    assert args[-2:] == ["-pix_fmt", "yuv420p"]


def test_unknown_render_profile_fails_loudly():
    with pytest.raises(ValueError, match="Unknown render profile"):
        video_encode_args("ultra-magic")
