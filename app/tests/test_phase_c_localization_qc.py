# SPDX-License-Identifier: AGPL-3.0-only
import pytest

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


def test_qc_estimated_onset_drift_requires_review_even_when_duration_fits(monkeypatch, tmp_path):
    monkeypatch.setattr(qc, "probe_media", lambda _: _media())
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda _: -1.0)
    issue = {"reason": "estimated_onset_drift", "chunk_id": 1,
             "segment_ids": ["s1"], "drift_ms": -2800,
             "timing_basis": "estimated_chunk_weights", "requires_human_review": True}
    report = qc.evaluate_localization_qc(
        source_video=tmp_path / "s.mp4", final_video=tmp_path / "f.mp4",
        transcript=_tx(), profile=LocalizationProfile(id="x", name="X"),
        timing_issues=[issue], check_tts_quality=False,
    )
    assert report.status == "NEEDS_REVIEW"
    row = next(w for w in report.warnings if w["code"] == "dubbing_onset_drift")
    assert row["issues"] == [issue]


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
        profile=LocalizationProfile(id="x", name="X", target_aspect="9:16", diarization_mode="off"),
    )
    assert report.status == "READY"
    assert report.failures == []


def test_qc_coverage_uses_aligned_speech_not_silence_inside_semantic_cue(
    monkeypatch, tmp_path,
):
    source = tmp_path / "s.mp4"
    final = tmp_path / "f.mp4"
    source.write_bytes(b"s")
    final.write_bytes(b"f")
    monkeypatch.setattr(qc, "probe_media", lambda p: _media(duration=20.0))
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda p: -1.0)

    report = qc.evaluate_localization_qc(
        source_video=source,
        final_video=final,
        transcript=_tx(
            source_end_ms=20_000,
            target_end_ms=1_000,
            source_words=[
                {"word": "你", "start": 0.0, "end": 0.5, "score": 0.9},
                {"word": "好", "start": 19.5, "end": 20.0, "score": 0.9},
            ],
        ),
        profile=LocalizationProfile(id="x", name="X", target_aspect="9:16", diarization_mode="off"),
    )

    assert report.status == "READY"
    assert report.metrics["speech_coverage_ratio"] == 1.0
    assert "low_target_speech_coverage" not in {
        row["code"] for row in report.failures
    }


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
        profile=LocalizationProfile(id="x", name="X", target_aspect="9:16", diarization_mode="off"),
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
        profile=LocalizationProfile(id="x", name="X", target_aspect="9:16", diarization_mode="off"),
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
        profile=LocalizationProfile(id="x", name="X", target_aspect="9:16", diarization_mode="off"),
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


def test_qc_surfaces_measured_cadence_failure_with_chunk_and_segments(monkeypatch, tmp_path):
    source = tmp_path / "s.mp4"
    final = tmp_path / "f.mp4"
    source.write_bytes(b"s")
    final.write_bytes(b"f")
    monkeypatch.setattr(qc, "probe_media", lambda _: _media())
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda _: -1.0)
    issue = {
        "reason": "measured_cadence_postfit_fallback",
        "chunk_id": 7,
        "segment_ids": ["s1"],
        "requires_human_review": True,
    }
    report = qc.evaluate_localization_qc(
        source_video=source,
        final_video=final,
        transcript=_tx(qc_status="review", tts_duration_basis="allocated"),
        profile=LocalizationProfile(id="x", name="X", diarization_mode="off"),
        timing_issues=[issue],
        check_tts_quality=False,
    )
    assert report.status == "NEEDS_REVIEW"
    warning = next(
        row for row in report.warnings
        if row["code"] == "measured_cadence_postfit_fallback"
    )
    assert warning["chunk_id"] == 7
    assert warning["segment_ids"] == ["s1"]

@pytest.mark.parametrize("target,width,height,should_fail", [
    ("9:16", 900, 1200, True),   # portrait but actually 3:4
    ("16:9", 1200, 900, True),   # landscape but actually 4:3
    ("1:1", 800, 900, True),
    ("9:16", 0, 0, True),
    ("9:16", 1080, 1920, False),
    ("16:9", 1920, 1080, False),
    ("1:1", 1080, 1080, False),
    ("9:16", 1080, 1922, False),  # slight encoder rounding tolerance
])
def test_qc_checks_actual_aspect_ratio_not_orientation(
    monkeypatch, tmp_path, target, width, height, should_fail,
):
    source = tmp_path / "source.mp4"
    final = tmp_path / "final.mp4"
    source.write_bytes(b"s")
    final.write_bytes(b"f")
    monkeypatch.setattr(qc, "probe_media", lambda path: _media(
        width=width if path == final else 1920,
        height=height if path == final else 1080,
    ))
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda _: -1.0)
    report = qc.evaluate_localization_qc(
        source_video=source, final_video=final,
        transcript=_tx(),
        profile=LocalizationProfile(
            id="aspect-qa", name="Aspect QA", target_aspect=target,
            diarization_mode="off",
        ),
        check_tts_quality=False,
    )
    wrong_aspect = [f for f in report.failures if f["code"] == "wrong_final_aspect"]
    assert bool(wrong_aspect) is should_fail
    if should_fail:
        assert report.status == "FAILED"
        assert wrong_aspect[0]["expected"] == target
