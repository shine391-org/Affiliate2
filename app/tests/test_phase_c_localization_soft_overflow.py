# SPDX-License-Identifier: AGPL-3.0-only
"""C7 bounded soft-overflow policy.

Normal ceiling 1.30 keeps existing behaviour. An emergency ceiling (default
1.35) is applied ONLY after rewrite, silence borrowing and bounded video
retime are exhausted, and never yields a silent quality downgrade: the chunk
fits, the overflow is persisted with both ceilings, and QC is forced to
NEEDS_REVIEW so the existing human acknowledgement gate stays in front of
Publish.
"""
from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import pytest

from backend.core.localization import (
    LocalizationProfile,
    LocalizationSegment,
    LocalizationTranscript,
)
from backend.services import localization_qc_service as qc
from backend.services import localization_service as svc

SAMPLE = Path("E:/Affiliate2/data/samples/local_sample1.mp4")  # real 15s MP4


def _write_wav_ms(path: Path, ms: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        import array
        import math
        samples = array.array("h", (round(3000 * math.sin(2 * math.pi * 440 * n / 16000))
                                  for n in range(16000 * ms // 1000)))
        handle.writeframes(samples.tobytes())


def _transcript(text: str = "Cảm ơn.") -> LocalizationTranscript:
    return LocalizationTranscript(
        language="Vietnamese",
        source="whisperx_alignment",
        segments=[LocalizationSegment(
            id="align-0001",
            speaker_id=None,
            source_text="Thank you.",
            translated_text=text,
            source_start_ms=13580,
            source_end_ms=13783,
            translation_status="success",
        )],
    )


class _StubUserSettings:
    preferred_tts_voice = "vi-VN-HoaiMyNeural"
    tts_provider = None
    tts_model = None
    tts_base_url = None
    tts_region = None
    tts_api_key = None


async def _run_dub(tmp_path, monkeypatch, *, measured_ms, retime="fallback", ceiling=1.12,
                   storage_root=None, profile_updates=None):
    """Run the real C7 planner/retime/fit path with only TTS stubbed."""
    from backend.config import settings
    import backend.services.tts_service as tts_service
    import backend.services.video_utils as video_utils

    monkeypatch.setattr(settings.__class__, "STORAGE_ROOT", property(lambda self: storage_root or tmp_path))

    async def stub_generate_tts(text, *, output_path, **kwargs):
        _write_wav_ms(Path(output_path), measured_ms)
        return str(output_path)

    monkeypatch.setattr(tts_service, "generate_tts", stub_generate_tts)
    real_probe = video_utils.probe_duration
    monkeypatch.setattr(video_utils, "probe_duration", lambda p, at: (
        15.0 if Path(p) == SAMPLE else real_probe(p, at)
    ))

    profile = LocalizationProfile.model_validate({
        "id": "qa",
        "name": "QA",
        "tts_provider": "edge_tts",
        "tts_voice": "vi-VN-HoaiMyNeural",
        "audio_mode": "hq_clean",
        "qc_profile": "strict",
        "target_aspect": "source",
        "video_retime_mode": retime,
        "video_retime_max_slowdown": ceiling,
        "dub_rewrite_attempts": 0,
        **(profile_updates or {}),
    })
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    transcript = _transcript()
    # Isolate the ceiling policy from silence allocation. This is the same
    # 1420ms pre-retime window previously obtained by spending all trailing
    # silence; an explicit window now prevents borrowing on either side.
    transcript.segments[0].overrides = {
        "target_start_ms": 13580, "target_end_ms": 15000,
    }
    return await svc.synthesize_timed_dub(
        transcript,
        source_media=SAMPLE,
        target_language="Vietnamese",
        profile=profile,
        user_settings=_StubUserSettings(),
        work_dir=work,
        fail_on_overflow=True,
        reuse_existing_chunks=False,
    )


@pytest.mark.asyncio
async def test_real_1_3132x_overflow_completes_and_records_both_ceilings(tmp_path, monkeypatch):
    """The real sample case: 2088ms speech in a 1590ms window needs 1.3132x."""
    result = await _run_dub(tmp_path, monkeypatch, measured_ms=2088)

    assert result.chunk_count == 1
    overflow = [
        issue for issue in result.issues if issue["reason"] == "timing_overflow"
    ]
    assert len(overflow) == 1
    issue = overflow[0]
    assert issue["chunk_id"] == 1
    assert issue["segment_ids"] == ["align-0001"]
    assert issue["required_speed"] == pytest.approx(1.3132, abs=1e-4)
    assert issue["speed_max"] == 1.30
    assert issue["emergency_speed_max"] == pytest.approx(1.35)
    assert issue["soft_overflow"] is True
    assert result.dub_track.exists()
    # The rendered chunk must actually fit its window now.
    assert result.chunks[0].speed_factor <= 1.35 + 1e-6


@pytest.mark.asyncio
async def test_over_emergency_ceiling_still_fails_hard(tmp_path, monkeypatch):
    with pytest.raises(svc.LocalizationTimingError) as excinfo:
        await _run_dub(tmp_path, monkeypatch, measured_ms=4000)
    issues = excinfo.value.issues
    assert [issue["chunk_id"] for issue in issues] == [1]
    assert issues[0]["required_speed"] > 1.35
    assert issues[0]["speed_max"] == 1.30
    assert issues[0].get("soft_overflow") is not True


@pytest.mark.asyncio
async def test_within_normal_ceiling_is_unchanged(tmp_path, monkeypatch):
    result = await _run_dub(tmp_path, monkeypatch, measured_ms=1800, retime="off", ceiling=1.0)
    assert not [
        issue for issue in result.issues if issue["reason"] == "timing_overflow"
    ]
    # 1800ms in the raw 203ms-sourced window still needs review, but no overflow.
    assert all(
        issue["reason"] != "timing_overflow_review" for issue in result.issues
    )


def _qc_report(tmp_path, monkeypatch, timing_issues):
    source = tmp_path / "s.mp4"
    final = tmp_path / "f.mp4"
    source.write_bytes(b"s")
    final.write_bytes(b"f")
    media = {
        "width": 1920, "height": 1080, "fps": 30.0, "video_codec": "h264",
        "audio_codec": "aac", "video_bitrate": 5_000_000, "audio_bitrate": 192_000,
        "bitrate": 5_192_000, "duration": 10.0, "container": "mov,mp4",
        "has_video": True, "has_audio": True,
    }
    monkeypatch.setattr(qc, "probe_media", lambda p: media)
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda p: -1.0)
    return qc.evaluate_localization_qc(
        source_video=source,
        final_video=final,
        transcript=LocalizationTranscript(segments=[LocalizationSegment(
            id="align-0001", source_text="Thank you.", translated_text="Cảm ơn.",
            source_start_ms=13580, source_end_ms=13783,
            target_start_ms=13580, target_end_ms=15170,
            translation_status="success", timing_status="adjusted",
            qc_status="review",
        )]),
        profile=LocalizationProfile(id="x", name="X", target_aspect="source"),
        timing_issues=timing_issues,
    )


SOFT_ISSUE = [{
    "chunk_id": 1,
    "reason": "timing_overflow",
    "required_speed": 1.3132,
    "speed_max": 1.30,
    "emergency_speed_max": 1.35,
    "soft_overflow": True,
    "segment_ids": ["align-0001"],
}]


def test_soft_overflow_qc_is_needs_review_with_explanation(
    monkeypatch, tmp_path,
):
    report = _qc_report(tmp_path, monkeypatch, SOFT_ISSUE)
    assert report.status == "NEEDS_REVIEW"
    assert report.failures == []
    warning = next(
        row for row in report.warnings if row["code"] == "timing_overflow_soft"
    )
    assert warning["required_speed"] == pytest.approx(1.3132, abs=1e-4)
    assert warning["speed_max"] == 1.30
    assert warning["emergency_speed_max"] == pytest.approx(1.35)
    assert warning["chunk_ids"] == [1]
    assert warning["segment_ids"] == ["align-0001"]


def test_soft_overflow_is_never_ready_even_under_a_standard_profile(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(qc, "probe_media", lambda p: {
        "width": 1920, "height": 1080, "fps": 30.0, "video_codec": "h264",
        "audio_codec": "aac", "video_bitrate": 5_000_000,
        "audio_bitrate": 192_000, "bitrate": 5_192_000, "duration": 10.0,
        "container": "mov,mp4", "has_video": True, "has_audio": True,
    })
    monkeypatch.setattr(qc, "audio_max_volume_db", lambda p: -1.0)
    source = tmp_path / "s.mp4"
    final = tmp_path / "f.mp4"
    source.write_bytes(b"s")
    final.write_bytes(b"f")
    report = qc.evaluate_localization_qc(
        source_video=source,
        final_video=final,
        transcript=LocalizationTranscript(segments=[LocalizationSegment(
            id="align-0001", source_text="Thank you.", translated_text="Cảm ơn.",
            source_start_ms=13580, source_end_ms=13783,
            target_start_ms=13580, target_end_ms=15170,
            translation_status="success", timing_status="adjusted",
            qc_status="pass",
        )]),
        profile=LocalizationProfile(
            id="x", name="X", target_aspect="source", qc_profile="standard",
        ),
        timing_issues=SOFT_ISSUE,
    )
    assert report.status == "NEEDS_REVIEW"


def test_hard_overflow_is_still_a_qc_failure(monkeypatch, tmp_path):
    report = _qc_report(tmp_path, monkeypatch, [{
        "chunk_id": 1,
        "reason": "timing_overflow",
        "required_speed": 1.41,
        "speed_max": 1.30,
        "segment_ids": ["align-0001"],
    }])
    assert report.status == "FAILED"
    assert "timing_overflow" in {row["code"] for row in report.failures}


def test_emergency_ceiling_is_configurable_but_bounded():
    profile = LocalizationProfile(id="x", name="X", dub_emergency_speed_max=1.5)
    assert profile.dub_emergency_speed_max == 1.5
    with pytest.raises(ValueError):
        LocalizationProfile(id="x", name="X", dub_emergency_speed_max=1.2)
    with pytest.raises(ValueError):
        LocalizationProfile(id="x", name="X", dub_emergency_speed_max=2.5)


def test_soft_overflow_artifact_cannot_publish_before_qc_acknowledgement():
    """Publish stays fail-closed: NEEDS_REVIEW never reaches a publishable state."""
    from backend.agents.uploader import localization_qc_publish_error
    from backend.models.generated_video import GeneratedVideo

    needs_review = GeneratedVideo(source_type="localization", review_status="qc_needs_review")
    assert localization_qc_publish_error(needs_review)

    from backend.services.localization_publish_service import _publishable_qc
    assert _publishable_qc({
        "qc_status": "NEEDS_REVIEW", "qc_human_approved": False,
    }) is False
    assert _publishable_qc({
        "qc_status": "NEEDS_REVIEW", "qc_human_approved": True,
    }) is True

def test_qc_approval_revision_changes_when_final_video_bytes_change(tmp_path):
    """A stale acknowledgement must not survive a rerender/version change."""
    from backend.api import localization as api

    stage = tmp_path / "stages"
    for name in ("qc", "final_render"):
        (stage / name).mkdir(parents=True)
    report = stage / "qc" / "report.json"
    video = stage / "final_render" / "final.mp4"
    report.write_bytes(b'{"status": "NEEDS_REVIEW"}')
    video.write_bytes(b"version-one")

    class FakeRun:
        def __init__(self, stamp):
            self.stamp = stamp

        def artifact(self, stage_name, artifact_name):
            return report if stage_name == "qc" else video

        def record(self, stage_name):
            return {
                "status": "complete",
                "completed_at": self.stamp,
                "artifacts": {},
            }

    original = api._qc_review_revision(FakeRun("2026-01-01T00:00:01"))
    assert original

    # A rerender produces new final-video bytes: the acknowledgement token the
    # user held is now stale, so the existing 409 guard must reject it.
    video.write_bytes(b"version-two-after-rerender")
    after_rerender = api._qc_review_revision(FakeRun("2026-01-01T00:01:02"))
    assert after_rerender and after_rerender != original
