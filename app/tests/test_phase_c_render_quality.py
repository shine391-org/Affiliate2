from pathlib import Path
import pytest

from backend.services.ffmpeg_service import RENDER_PROFILES, video_encode_args


def test_render_profiles_cover_phase_c_quality_levels():
    assert set(RENDER_PROFILES) == {"draft", "balanced", "hq"}


def test_hq_uses_lower_crf_than_balanced_and_draft():
    assert RENDER_PROFILES["hq"]["crf"] < RENDER_PROFILES["balanced"]["crf"]
    assert RENDER_PROFILES["balanced"]["crf"] < RENDER_PROFILES["draft"]["crf"]


def test_encode_args_are_compatible_h264():
    args = video_encode_args("hq", prefer_nvenc=False)
    assert args[:2] == ["-c:v", "libx264"]
    assert "-crf" in args and args[args.index("-crf") + 1] == "18"
    assert args[-2:] == ["-pix_fmt", "yuv420p"]


def test_encode_args_nvenc_when_supported():
    from backend.services.ffmpeg_service import has_nvenc_support
    if has_nvenc_support():
        args = video_encode_args("hq", prefer_nvenc=True)
        assert args[:2] == ["-c:v", "h264_nvenc"]
        assert "-cq" in args and args[args.index("-cq") + 1] == "19"


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
    assert ("-crf 18" in command) or ("-cq 19" in command)


@pytest.mark.asyncio
async def test_final_retime_external_audio_reframe_caption_share_one_encode(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from backend.services import caption_service

    video = tmp_path / "source.mp4"
    mixed = tmp_path / "mixed.wav"
    ass = tmp_path / "captions.ass"
    out = tmp_path / "out.mp4"
    video.write_bytes(b"video")
    mixed.write_bytes(b"audio")
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
        external_audio=mixed,
        video_slowdown=1.08,
    )
    assert rendered == out
    assert len(calls) == 1
    command = " ".join(calls[0])
    assert str(mixed) in command
    assert "setpts=1.08000000*PTS" in command
    assert "ass=" in command
    assert "1080:1920" in command
    assert "-map 1:a:0" in command
    assert ("-crf 18" in command) or ("-cq 19" in command)


@pytest.mark.asyncio
async def test_burn_captions_with_mask_box_solid_and_blur(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from backend.services import caption_service

    video = tmp_path / "source.mp4"
    ass = tmp_path / "captions.ass"
    out1 = tmp_path / "out1.mp4"
    out2 = tmp_path / "out2.mp4"
    video.write_bytes(b"video")
    ass.write_text("[Script Info]\n", encoding="utf-8")

    monkeypatch.setattr(caption_service, "_ffmpeg_has_ass", True)
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *a, **k: 10.0)

    async def fake_dims(_):
        return (1080, 1920)
    monkeypatch.setattr("backend.services.ffmpeg_service._probe_dimensions", fake_dims)

    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        target = Path(cmd[-1])
        target.write_bytes(b"rendered")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(caption_service.subprocess, "run", fake_run)

    # Test solid mask
    await caption_service.burn_captions(
        video, ass, out1,
        mask_box={"x": 0.1, "y": 0.7, "width": 0.8, "height": 0.15, "mode": "solid"},
    )
    cmd1 = " ".join(calls[0])
    assert "drawbox=" in cmd1
    assert "color=black@1.0:t=fill" in cmd1

    # Test blur mask
    calls.clear()
    await caption_service.burn_captions(
        video, ass, out2,
        mask_box={"x": 0.1, "y": 0.7, "width": 0.8, "height": 0.15, "mode": "blur"},
    )
    cmd2 = " ".join(calls[0])
    assert "boxblur=" in cmd2
    assert "crop=" in cmd2
    assert "overlay=" in cmd2


@pytest.mark.asyncio
async def test_generate_ass_places_vietsub_inside_mask_region_with_vertical_offset(tmp_path):
    from backend.services import caption_service

    segments = [{"start": 0.0, "end": 1.0, "text": "Phụ đề tiếng Việt"}]
    mask = {
        "x": 0.05,
        "y": 0.72,
        "width": 0.9,
        "height": 0.15,
        "mode": "blur",
        "normalized": True,
    }

    centered = tmp_path / "centered.ass"
    shifted = tmp_path / "shifted.ass"
    default = tmp_path / "default.ass"
    await caption_service.generate_captions_ass(
        segments,
        aspect_ratio="9:16",
        output_path=centered,
        emoji_style="none",
        mask_box=mask,
        caption_placement={"mode": "mask_region", "vertical_offset": 0.0},
    )
    await caption_service.generate_captions_ass(
        segments,
        aspect_ratio="9:16",
        output_path=shifted,
        emoji_style="none",
        mask_box=mask,
        caption_placement={"mode": "mask_region", "vertical_offset": -0.05},
    )
    await caption_service.generate_captions_ass(
        segments,
        aspect_ratio="9:16",
        output_path=default,
        emoji_style="none",
    )

    centered_text = centered.read_text(encoding="utf-8")
    shifted_text = shifted.read_text(encoding="utf-8")
    default_text = default.read_text(encoding="utf-8")
    assert "\\an5\\pos(540,1526)" in centered_text
    assert "\\an5\\pos(540,1430)" in shifted_text
    assert "\\pos(" not in default_text


@pytest.mark.asyncio
async def test_generate_ass_rejects_mask_region_too_small_for_caption(tmp_path):
    from backend.services import caption_service

    with pytest.raises(ValueError, match="too small"):
        await caption_service.generate_captions_ass(
            [{"start": 0.0, "end": 1.0, "text": "Phụ đề tiếng Việt"}],
            aspect_ratio="9:16",
            output_path=tmp_path / "small.ass",
            emoji_style="none",
            mask_box={
                "x": 0.4,
                "y": 0.8,
                "width": 0.05,
                "height": 0.02,
                "normalized": True,
            },
            caption_placement={"mode": "mask_region", "vertical_offset": 0.0},
        )
