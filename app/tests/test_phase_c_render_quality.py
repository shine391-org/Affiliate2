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


@pytest.mark.asyncio
async def test_final_caption_reframe_is_one_video_encode(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from backend.services import caption_service

    video = tmp_path / "source.mp4"
    ass = tmp_path / "captions.ass"
    out = tmp_path / "out.mp4"
    video.write_bytes(b"video")
    ass.write_text("[Script Info]\n", encoding="utf-8")

    monkeypatch.setattr(caption_service, "_ffmpeg_has_ass", True)
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *a, **k: 10.0)

    async def fake_dims(_):
        return (1920, 1080)
    monkeypatch.setattr("backend.services.ffmpeg_service._probe_dimensions", fake_dims)

    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        out.write_bytes(b"rendered")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(caption_service.subprocess, "run", fake_run)

    rendered = await caption_service.burn_captions(
        video,
        ass,
        out,
        render_profile="hq",
        target_aspect="9:16",
        reframe_method="auto",
    )
    assert rendered == out
    assert len(calls) == 1
    command = " ".join(calls[0])
    assert "-filter_complex" in command
    assert "ass=" in command
    assert "1080:1920" in command
    assert "-crf 18" in command
