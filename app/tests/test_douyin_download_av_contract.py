# SPDX-License-Identifier: AGPL-3.0-only
"""A Douyin "video" download must land a PLAYABLE video, not a video track.

Real production failure these tests encode: two of three live Douyin downloads
on 2026-09-29 stored a 1024x576 h264 file with ZERO audio streams and were
recorded in `downloaded_videos` as successful, with `audio_path = NULL`.
Nothing complained. The failure surfaced hours later, inside Demucs, as
"Stream map '0:a:0' matches no streams", after the model was already on the GPU.

Douyin serves a video-only rendition and a muxed A/V rendition of the SAME
aweme. Picking a different RENDITION to recover audio is legitimate; picking a
different VIDEO is not — so every fallback candidate must still be proven to be
the requested aweme.

`yt_dlp` is stubbed; nothing here touches the network.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import types
from pathlib import Path

import pytest

from backend.core.exceptions import SourceMediaContractError
from backend.services import ytdlp_service as YT
from backend.services.douyin_media_resolver import (
    ResolvedDouyinMedia,
    _MediaCandidate,
    _verified_ordered,
)


# ── real-media fixtures ──────────────────────────────────────────────────────

def _has_ffmpeg() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


requires_ffmpeg = pytest.mark.skipif(
    not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH"
)


def _write_video_only_mp4(path: Path) -> Path:
    """Bytes that ffprobe reports as h264 video with NO audio stream."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=320x240:r=10:d=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )
    return path


def _write_av_mp4(path: Path) -> Path:
    """Bytes that ffprobe reports as h264 video WITH an aac audio stream."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=320x240:r=10:d=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


def _fake_inventory(path, has_audio: bool, readable: bool = True, has_video: bool = True):
    from backend.services.video_utils import MediaStreamInventory
    return MediaStreamInventory(
        has_video=has_video, has_audio=has_audio,
        video_codec="h264", audio_codec="aac" if has_audio else None,
        width=320, height=240, duration=1.0,
        container="mov,mp4,m4a,3gp,3g2,mj2", readable=readable,
    )


# ── STEP 7.7: identity must survive the audio fallback ───────────────────────

def test_verified_ordered_only_returns_candidates_of_the_requested_aweme():
    requested = "123"
    candidates = [
        _MediaCandidate(url="https://v3-web.douyinvod.com/a.mp4?__vid=123",
                        url_aweme_ids=("123",), provenance="search:play_addr"),
        _MediaCandidate(url="https://v3-web.douyinvod.com/b.mp4?__vid=123",
                        url_aweme_ids=("123",), provenance="search:play_addr"),
        # A DIFFERENT aweme that happens to be downloadable.
        _MediaCandidate(url="https://v3-web.douyinvod.com/wrong.mp4?__vid=999",
                        url_aweme_ids=("999",), provenance="search:play_addr"),
    ]

    ordered = _verified_ordered(candidates, requested)

    assert len(ordered) == 2, "the aweme 999 candidate must never be returned"
    for candidate in ordered:
        assert "123" in candidate.url_aweme_ids
        assert "999" not in candidate.url_aweme_ids


def test_verified_ordered_is_empty_when_only_another_aweme_is_available():
    candidates = [
        _MediaCandidate(url="https://v3-web.douyinvod.com/wrong.mp4?__vid=999",
                        url_aweme_ids=("999",)),
    ]
    assert _verified_ordered(candidates, "123") == []


def test_verified_ordered_refuses_to_run_without_a_requested_id():
    """The fail-closed invariant must survive the refactor to a list return."""
    candidates = [_MediaCandidate(url="https://v3-web.douyinvod.com/a.mp4",
                                  dom_aweme_ids=("123",))]
    with pytest.raises(ValueError):
        _verified_ordered(candidates, None)


def test_verified_candidate_still_returns_the_single_best():
    candidates = [
        _MediaCandidate(url="https://v3-web.douyinvod.com/a.mp4?__vid=123",
                        url_aweme_ids=("123",), content_length=100),
        _MediaCandidate(url="https://v3-web.douyinvod.com/b.mp4?__vid=123",
                        url_aweme_ids=("123",), content_length=999),
        _MediaCandidate(url="https://v3-web.douyinvod.com/c.mp4?__vid=999",
                        url_aweme_ids=("999",), content_length=99999),
    ]
    best = YT and _verified_ordered(candidates, "123")
    from backend.services.douyin_media_resolver import _verified_candidate
    assert _verified_candidate(candidates, "123").url.endswith("b.mp4?__vid=123")


# ── the download-side harness ────────────────────────────────────────────────

class _FakeYtdlp:
    """A yt_dlp stand-in that materialises a real file per URL.

    Written to disk rather than faked in the probe so the production
    ffprobe-based gate reads genuine bytes: a mocked probe would only prove
    the mock agrees with itself.
    """

    def __init__(self, payload_by_url, factory):
        self.payload_by_url = payload_by_url
        self.factory = factory
        self.requested_urls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def prepare_filename(self, info):
        return str(info["_out"])

    def extract_info(self, url, download=True):
        self.requested_urls.append(url)
        info = dict(self.payload_by_url[url])
        info["_out"] = self.factory(url, info)
        return info


@pytest.fixture
def douyin_env(monkeypatch, tmp_path):
    """Wire download_video to a stubbed resolver + yt-dlp + probe."""
    out_dir = tmp_path / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)
    state = {"out_dir": out_dir, "written": [], "requested": []}

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
    monkeypatch.setattr(
        YT, "_apply_cookies", lambda *a, **k: False, raising=False,
    )
    return state


def _install_resolver(monkeypatch, primary_url, alternate_urls, aweme="123"):
    """Make resolve_douyin_media return a primary + N same-aweme alternates."""
    import backend.services.douyin_media_resolver as resolver

    def _resolved():
        return ResolvedDouyinMedia(
            url=primary_url,
            headers={"User-Agent": "QA", "Referer": "https://www.douyin.com/"},
            source_url="https://www.douyin.com/video/123",
            requested_aweme_id=aweme,
            candidate_aweme_id=aweme,
            provenance="search:play_addr",
            resolution_path="search",
            alternates=tuple(
                ResolvedDouyinMedia(
                    url=u,
                    headers={"User-Agent": "QA", "Referer": "https://www.douyin.com/"},
                    source_url="https://www.douyin.com/video/123",
                    requested_aweme_id=aweme,
                    candidate_aweme_id=aweme,
                    provenance="search:play_addr",
                    resolution_path="search",
                )
                for u in alternate_urls
            ),
        )

    async def _resolve(url, cdp_url=None, title_hint=""):
        return _resolved()

    monkeypatch.setattr(resolver, "resolve_douyin_media", _resolve)


def _install_yt_dlp(monkeypatch, state, by_url):
    """Map each URL to a file-producing factory."""
    fake = _FakeYtdlp(
        payload_by_url={u: {"id": u, "duration": 1, "title": "t"} for u in by_url},
        factory=by_url,
    )

    def _factory(url, info):
        target = Path(state["out_dir"]) / f"{abs(hash(url)) % 10**8}.mp4"
        by_url[url](target)
        state["written"].append(target)
        return target

    fake.factory = _factory

    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = lambda opts, *a, **k: fake
    mod.utils = types.SimpleNamespace(
        DownloadError=type("DownloadError", (Exception,), {}),
    )
    sys.modules["yt_dlp"] = mod
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    return fake


@requires_ffmpeg
def test_video_only_rendition_with_no_alternate_fails_closed(
    douyin_env, monkeypatch, tmp_path
):
    """STEP 7.4 + 7.6: a video-only Douyin download is NOT a successful video."""
    primary = "https://v3-web.douyinvod.com/primary.mp4"
    _install_resolver(monkeypatch, primary, [])
    fake = _install_yt_dlp(
        monkeypatch, douyin_env,
        {primary: lambda p: _write_video_only_mp4(p)},
    )

    with pytest.raises(SourceMediaContractError) as excinfo:
        asyncio.run(YT.download_video(
            "https://www.douyin.com/video/123",
            output_dir=douyin_env["out_dir"],
            filename="dl",
            extract_audio=False,
        ))

    assert "audio" in str(excinfo.value)
    assert "0:a:0" not in str(excinfo.value)
    # The unusable file must not be left behind to be picked up as a success.
    assert not any(p.exists() for p in douyin_env["written"]), (
        "a video-only download must be cleaned up, not stored as a video source"
    )


@requires_ffmpeg
def test_fallback_recovers_audio_from_another_verified_rendition(
    douyin_env, monkeypatch
):
    """STEP 7.5: a same-aweme muxed rendition is accepted."""
    primary = "https://v3-web.douyinvod.com/primary.mp4"
    alternate = "https://v3-web.douyinvod.com/alternate.mp4"
    _install_resolver(monkeypatch, primary, [alternate])
    _install_yt_dlp(monkeypatch, douyin_env, {
        primary: lambda p: _write_video_only_mp4(p),
        alternate: lambda p: _write_av_mp4(p),
    })

    result = asyncio.run(YT.download_video(
        "https://www.douyin.com/video/123",
        output_dir=douyin_env["out_dir"],
        filename="dl",
        extract_audio=False,
    ))

    from backend.services.video_utils import probe_stream_inventory
    landed = probe_stream_inventory(result["video_path"])
    assert landed.has_video is True
    assert landed.has_audio is True, "the fallback must yield a playable A/V file"
    assert landed.audio_codec == "aac"


@requires_ffmpeg
def test_fallback_fails_closed_when_every_candidate_is_video_only(
    douyin_env, monkeypatch
):
    """STEP 7.6: no verified rendition has audio -> clear error, no success."""
    primary = "https://v3-web.douyinvod.com/primary.mp4"
    a1 = "https://v3-web.douyinvod.com/a1.mp4"
    a2 = "https://v3-web.douyinvod.com/a2.mp4"
    _install_resolver(monkeypatch, primary, [a1, a2])
    fake = _install_yt_dlp(monkeypatch, douyin_env, {
        u: (lambda p: _write_video_only_mp4(p)) for u in (primary, a1, a2)
    })

    with pytest.raises(SourceMediaContractError):
        asyncio.run(YT.download_video(
            "https://www.douyin.com/video/123",
            output_dir=douyin_env["out_dir"],
            filename="dl",
            extract_audio=False,
        ))

    # Every verified candidate was actually TRIED before failing closed.
    assert set(fake.requested_urls) == {primary, a1, a2}


@requires_ffmpeg
def test_fallback_never_downloads_a_different_aweme(douyin_env, monkeypatch):
    """STEP 7.7 at the download layer: only resolver-verified URLs are used.

    The resolver is the identity gate; the downloader's fallback consumes only
    what that gate returned. A URL for another aweme must therefore never be
    fetched, even when the primary is video-only.
    """
    primary = "https://v3-web.douyinvod.com/primary.mp4?__vid=123"
    # The resolver would never hand this over, and the downloader must not
    # reach it on its own.
    forbidden = "https://v3-web.douyinvod.com/other-aweme.mp4?__vid=999"
    _install_resolver(monkeypatch, primary, [])
    fake = _install_yt_dlp(
        monkeypatch, douyin_env,
        {primary: lambda p: _write_video_only_mp4(p)},
    )

    with pytest.raises(SourceMediaContractError):
        asyncio.run(YT.download_video(
            "https://www.douyin.com/video/123",
            output_dir=douyin_env["out_dir"],
            filename="dl",
            extract_audio=False,
        ))

    assert forbidden not in fake.requested_urls
    for requested in fake.requested_urls:
        assert "__vid=999" not in requested


@requires_ffmpeg
def test_a_muxed_rendition_is_accepted_without_any_fallback(douyin_env, monkeypatch):
    """The gate must not disturb the normal, already-good path."""
    primary = "https://v3-web.douyinvod.com/primary.mp4"
    _install_resolver(monkeypatch, primary, [])
    fake = _install_yt_dlp(
        monkeypatch, douyin_env,
        {primary: lambda p: _write_av_mp4(p)},
    )

    result = asyncio.run(YT.download_video(
        "https://www.douyin.com/video/123",
        output_dir=douyin_env["out_dir"],
        filename="dl",
        extract_audio=False,
    ))

    assert Path(result["video_path"]).exists()
    assert fake.requested_urls == [primary], (
        "a playable download must not trigger any rendition fallback"
    )
    assert result["download_source"] == "douyin_cdp"


# ── the fallback budget is bounded ───────────────────────────────────────────

def test_audio_fallback_budget_is_bounded():
    """An unbounded fallback would fan out into N full CDN downloads."""
    assert 1 <= YT.MAX_DOUYIN_AUDIO_FALLBACKS <= 8
