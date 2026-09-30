"""A direct-media download must report a real duration, not 0.

`download_video` read duration from yt-dlp's `info` envelope. A resolved
Douyin CDN URL is handed to the generic extractor, which returns no duration
at all, so the top-level `duration` became 0 while the file on disk was fine.
ffprobe already measured the real value for `media_metadata`; the field the
product stores and every duration-based guard reads was simply never fed from
it.

Measured 2026-09-30: requested 7670961858774591397 reported duration 0 while
ffprobe reported 70.423991s.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import types
from pathlib import Path

import pytest

from backend.services import ytdlp_service as YT


def _has_ffmpeg() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


requires_ffmpeg = pytest.mark.skipif(
    not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH"
)


def _write_av_mp4(path: Path, seconds: int) -> Path:
    """Real A/V bytes whose true duration ffprobe can read back."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c=black:s=320x240:r=10:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.fixture
def direct_media_env(monkeypatch, tmp_path):
    out_dir = tmp_path / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    async def _browser():
        return ("edge", "Default")

    async def _cdp():
        return None

    monkeypatch.setattr(YT, "get_download_browser_session", _browser)
    monkeypatch.setattr(YT, "get_douyin_cdp_url", _cdp)
    monkeypatch.setattr(
        YT, "_inspect_browser_session",
        lambda *a, **k: {"ok": False, "error": "qa-stub", "detail": ""},
    )
    monkeypatch.setattr(YT, "_apply_cookies", lambda *a, **k: False, raising=False)
    return out_dir


def _install_yt_dlp(monkeypatch, out_dir, url, info, seconds):
    """A stub that lands real bytes and reports the given yt-dlp envelope."""
    state = {}

    class Fake:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def prepare_filename(self, info):
            return str(info["_out"])

        def extract_info(self, requested, download=True):
            state["requested"] = requested
            landed = out_dir / "dl.mp4"
            _write_av_mp4(landed, seconds)
            payload = dict(info)
            payload["_out"] = landed
            return payload

    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = lambda opts, *a, **k: Fake()
    mod.utils = types.SimpleNamespace(DownloadError=type("DownloadError", (Exception,), {}))
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    return state


@requires_ffmpeg
def test_duration_comes_from_probe_when_extractor_reports_none(
    direct_media_env, monkeypatch
):
    """Live shape: yt-dlp has no duration, the file does."""
    url = "https://v3-web.douyinvod.com/no-duration.mp4"
    import backend.services.douyin_media_resolver as resolver
    from backend.services.douyin_media_resolver import ResolvedDouyinMedia

    async def _resolve(source, cdp_url=None, title_hint="", **_kwargs):
        return ResolvedDouyinMedia(
            url=url, headers={"User-Agent": "QA", "Referer": source},
            source_url=source, requested_aweme_id="123", candidate_aweme_id="123",
            provenance="exact_aweme:play_addr", resolution_path="exact_aweme",
        )

    monkeypatch.setattr(resolver, "resolve_douyin_media", _resolve)
    _install_yt_dlp(monkeypatch, direct_media_env, url, {"id": "x", "title": "t"}, 3)

    out = asyncio.run(YT.download_video(
        "https://www.douyin.com/video/123",
        output_dir=direct_media_env, filename="dl", extract_audio=False,
    ))

    assert out["media_metadata"]["duration_seconds"] >= 2.5
    assert out["duration"] == pytest.approx(
        out["media_metadata"]["duration_seconds"], rel=0.05
    ), f"top-level duration {out['duration']} must reflect the probed media"
    # The DB column is an Integer, so the stored value must be usable as-is.
    assert int(out["duration"]) >= 2


@requires_ffmpeg
def test_reported_extractor_duration_is_not_overwritten(
    direct_media_env, monkeypatch
):
    """YouTube/TikTok already report a duration; do not clobber it."""
    url = "https://v3-web.douyinvod.com/has-duration.mp4"
    import backend.services.douyin_media_resolver as resolver
    from backend.services.douyin_media_resolver import ResolvedDouyinMedia

    async def _resolve(source, cdp_url=None, title_hint="", **_kwargs):
        return ResolvedDouyinMedia(
            url=url, headers={"User-Agent": "QA", "Referer": source},
            source_url=source, requested_aweme_id="123", candidate_aweme_id="123",
            provenance="exact_aweme:play_addr", resolution_path="exact_aweme",
        )

    monkeypatch.setattr(resolver, "resolve_douyin_media", _resolve)
    _install_yt_dlp(monkeypatch, direct_media_env, url, {"id": "x", "duration": 3}, 3)

    out = asyncio.run(YT.download_video(
        "https://www.douyin.com/video/123",
        output_dir=direct_media_env, filename="dl", extract_audio=False,
    ))

    assert out["duration"] == 3


def test_duration_guards_no_longer_skip_when_duration_is_known():
    """The reason this matters: a 0 duration disables the range guard."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.api.downloaded import _validate_manual_time_ranges, auto_clip_count

    assert auto_clip_count(70.424) == 3
    with pytest.raises(Exception) as exc:
        _validate_manual_time_ranges([{"start": 80, "end": 90}], video_duration=70.424)
    assert "exceeds video duration" in str(exc.value.detail)
