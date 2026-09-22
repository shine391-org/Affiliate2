# SPDX-License-Identifier: AGPL-3.0-only
from types import SimpleNamespace

from backend.services import video_utils
from backend.services import ytdlp_service


def test_default_download_ladder_is_best_first():
    assert ytdlp_service.FORMAT_FALLBACK_CHAIN
    assert "height<=" not in ytdlp_service.FORMAT_FALLBACK_CHAIN[0]


def test_probe_media_details_reports_real_stream_properties(monkeypatch):
    payload = """{"streams":[
      {"codec_type":"video","codec_name":"h264","width":1920,"height":1080,
       "avg_frame_rate":"30000/1001","bit_rate":"4100000"},
      {"codec_type":"audio","codec_name":"aac"}
    ],"format":{"duration":"12.5","bit_rate":"4300000","format_name":"mov,mp4"}}"""

    monkeypatch.setattr(
        video_utils.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=payload),
    )
    got = video_utils.probe_media_details("x.mp4")
    assert got["width"] == 1920
    assert got["height"] == 1080
    assert got["fps"] == 29.97
    assert got["video_codec"] == "h264"
    assert got["audio_codec"] == "aac"
    assert got["bitrate_bps"] == 4300000
    assert got["duration_seconds"] == 12.5


def test_probe_media_details_fails_closed_to_empty(monkeypatch):
    monkeypatch.setattr(
        video_utils.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=""),
    )
    assert video_utils.probe_media_details("bad.mp4") == {}
