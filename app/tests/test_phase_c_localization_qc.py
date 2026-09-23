# SPDX-License-Identifier: AGPL-3.0-only
from backend.core.localization import LocalizationProfile, LocalizationSegment, LocalizationTranscript
from backend.services import localization_qc_service as qc


def _tx(**segment_updates):
    base = dict(
        id="s1",
        source_text="hello",
        translated_text="xin chào",
        source_start_ms=0,
        source_end_ms=1000,
        target_start_ms=0,
        target_end_ms=1000,
        translation_status="success",
        timing_status="fit",
        qc_status="pass",
    )
    base.update(segment_updates)
    return LocalizationTranscript(segments=[LocalizationSegment(**base)])


def _media(width=1080, height=1920, duration=10.0):
    return {
        "width": width,
        "height": height,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "video_bitrate": 5_000_000,
        "audio_bitrate": 192_000,
        "bitrate": 5_192_000,
        "duration": duration,
        "container": "mov,mp4",
        "has_video": True,
        "has_audio": True,
    }


def test_qc_ready_when_media_timing_and_translation_are_clean(monkeypatch, tmp_path):
    source = tmp_path / "s.mp4"
    final = tmp_path / "f.mp4"
    source.write_bytes(b"s")
    final.write_bytes(b"f")
    monkeypatch.setattr(qc, "probe_media", lambda p: _media(
        width=1920 if p == source else 1080,
        height=1080 if p == source else 1920,
    ))
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda p: -1.0)
    report = qc.evaluate_localization_qc(
        source_video=source,
        final_video=final,
        transcript=_tx(),
        profile=LocalizationProfile(id="x", name="X", target_aspect="9:16"),
    )
    assert report.status == "READY"
    assert report.failures == []


def test_qc_fails_untranslated_and_low_target_coverage(monkeypatch, tmp_path):
    source = tmp_path / "s.mp4"
    final = tmp_path / "f.mp4"
    source.write_bytes(b"s")
    final.write_bytes(b"f")
    monkeypatch.setattr(qc, "probe_media", lambda p: _media())
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda p: -1.0)
    report = qc.evaluate_localization_qc(
        source_video=source,
        final_video=final,
        transcript=_tx(
            translated_text=None,
            target_start_ms=None,
            target_end_ms=None,
            translation_status="untranslated",
            timing_status="pending",
            qc_status="review",
        ),
        profile=LocalizationProfile(id="x", name="X", target_aspect="9:16"),
    )
    codes = {row["code"] for row in report.failures}
    assert report.status == "FAILED"
    assert "untranslated_segments" in codes
    assert "low_target_speech_coverage" in codes


def test_qc_keeps_speed_warning_as_review_not_fake_pass(monkeypatch, tmp_path):
    source = tmp_path / "s.mp4"
    final = tmp_path / "f.mp4"
    source.write_bytes(b"s")
    final.write_bytes(b"f")
    monkeypatch.setattr(qc, "probe_media", lambda p: _media())
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda p: -1.0)
    report = qc.evaluate_localization_qc(
        source_video=source,
        final_video=final,
        transcript=_tx(qc_status="review"),
        profile=LocalizationProfile(id="x", name="X", target_aspect="9:16"),
        timing_issues=[{"reason": "speed_warning", "chunk_id": 1}],
    )
    assert report.status == "NEEDS_REVIEW"



def test_qc_requires_review_when_global_video_retime_was_used(monkeypatch, tmp_path):
    source = tmp_path / "s.mp4"
    final = tmp_path / "f.mp4"
    source.write_bytes(b"s")
    final.write_bytes(b"f")

    def media(path):
        return _media(
            width=1920 if path == source else 1080,
            height=1080 if path == source else 1920,
            duration=10.0 if path == source else 10.8,
        )

    monkeypatch.setattr(qc, "probe_media", media)
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda p: -1.0)
    report = qc.evaluate_localization_qc(
        source_video=source,
        final_video=final,
        transcript=_tx(),
        profile=LocalizationProfile(id="x", name="X", target_aspect="9:16"),
        video_slowdown=1.08,
    )
    assert report.status == "NEEDS_REVIEW"
    assert "video_retimed" in {row["code"] for row in report.warnings}



def test_qc_requires_review_for_edge_full_dub_preview_tier(monkeypatch, tmp_path):
    source = tmp_path / "s.mp4"
    final = tmp_path / "f.mp4"
    source.write_bytes(b"s")
    final.write_bytes(b"f")
    monkeypatch.setattr(qc, "probe_media", lambda p: _media(
        width=1920 if p == source else 1080,
        height=1080 if p == source else 1920,
    ))
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda p: -1.0)

    report = qc.evaluate_localization_qc(
        source_video=source,
        final_video=final,
        transcript=_tx(),
        profile=LocalizationProfile(
            id="x", name="X", target_aspect="9:16", tts_provider="edge_tts"
        ),
        check_tts_quality=True,
    )
    assert report.status == "NEEDS_REVIEW"
    assert "preview_tts_provider" in {row["code"] for row in report.warnings}


def test_captions_only_does_not_warn_about_tts_provider(monkeypatch, tmp_path):
    source = tmp_path / "s.mp4"
    final = tmp_path / "f.mp4"
    source.write_bytes(b"s")
    final.write_bytes(b"f")
    monkeypatch.setattr(qc, "probe_media", lambda p: _media(
        width=1920 if p == source else 1080,
        height=1080 if p == source else 1920,
    ))
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda p: -1.0)

    report = qc.evaluate_localization_qc(
        source_video=source,
        final_video=final,
        transcript=_tx(),
        profile=LocalizationProfile(
            id="x", name="X", target_aspect="9:16", tts_provider="edge_tts"
        ),
        check_tts_quality=False,
    )
    assert "preview_tts_provider" not in {row["code"] for row in report.warnings}



def test_qc_requires_review_when_dubbing_rewrite_ai_was_unavailable(
    monkeypatch, tmp_path,
):
    source = tmp_path / "s.mp4"
    final = tmp_path / "f.mp4"
    source.write_bytes(b"s")
    final.write_bytes(b"f")
    monkeypatch.setattr(qc, "probe_media", lambda p: _media())
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda p: -1.0)

    report = qc.evaluate_localization_qc(
        source_video=source,
        final_video=final,
        transcript=_tx(),
        profile=LocalizationProfile(
            id="x",
            name="X",
            target_aspect="source",
        ),
        timing_issues=[{
            "chunk_id": 1,
            "reason": "rewrite_ai_unavailable",
            "error_type": "TimeoutError",
        }],
    )

    assert report.status == "NEEDS_REVIEW"
    assert "dubbing_rewrite_degraded" in {
        item["code"] for item in report.warnings
    }
