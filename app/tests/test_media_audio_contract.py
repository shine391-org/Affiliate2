# SPDX-License-Identifier: AGPL-3.0-only
"""Source video with no audio stream must fail before any GPU work starts.

Real production failure these tests encode (job b100d1cd, 2026-09-29): a Douyin
download succeeded and stored a 1024x576 h264 file with ZERO audio streams.
Nothing complained at download time. The job then died inside Demucs with

    Stream map '0:a:0' matches no streams

AFTER htdemucs_ft had already been loaded onto the GPU — an expensive,
completely wasted unit of work whose error named a codec option instead of
telling the user what to do.

Two boundaries are covered:
  A. download-time  — a Douyin "video" download must be a playable full video
  B. preflight-time — a full_dub localization source must have audio, checked
                      before Demucs is instantiated
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from backend.core.exceptions import (
    MissingSourceAudioError,
    SourceMediaContractError,
)
from backend.services.video_utils import probe_stream_inventory


# ── ffmpeg-backed fixtures ───────────────────────────────────────────────────

def _ffmpeg_available() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


requires_ffmpeg = pytest.mark.skipif(
    not _ffmpeg_available(), reason="ffmpeg/ffprobe not on PATH"
)


def _make_video_only_mp4(path: Path, seconds: int = 1) -> Path:
    """A real, readable mp4 that genuinely has no audio stream."""
    import subprocess
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:s=320x240:r=10:d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True, capture_output=True,
    )
    return path


def _make_av_mp4(path: Path, seconds: int = 1) -> Path:
    """A real, readable mp4 with BOTH a video and an audio stream."""
    import subprocess
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:s=320x240:r=10:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True, capture_output=True,
    )
    return path


# ── STEP 6: the reusable validation contract ─────────────────────────────────

@requires_ffmpeg
def test_inventory_reports_a_video_only_file_as_not_playable(tmp_path):
    f = _make_video_only_mp4(tmp_path / "video_only.mp4")
    inv = probe_stream_inventory(f)

    assert inv.readable is True, "a valid video-only mp4 IS readable"
    assert inv.has_video is True
    assert inv.has_audio is False
    assert inv.is_playable_full_video() is False
    assert inv.video_codec == "h264"
    assert inv.audio_codec is None
    assert "âm thanh" in inv.describe_missing()


@requires_ffmpeg
def test_inventory_accepts_a_muxed_av_file(tmp_path):
    f = _make_av_mp4(tmp_path / "av.mp4")
    inv = probe_stream_inventory(f)

    assert inv.readable is True
    assert inv.has_video is True
    assert inv.has_audio is True
    assert inv.is_playable_full_video() is True
    assert inv.video_codec == "h264"
    assert inv.audio_codec == "aac"
    assert inv.describe_missing() == ""


def test_inventory_of_a_missing_file_is_unreadable_not_merely_silent(tmp_path):
    inv = probe_stream_inventory(tmp_path / "does_not_exist.mp4")

    # readable=False is the distinction that stops a caller from reporting
    # "this file has no audio" about a file it never managed to open.
    assert inv.readable is False
    assert inv.is_playable_full_video() is False


# ── Regression: the exact production failure, at the probe level ────────────

@requires_ffmpeg
def test_regression_production_failure_shape_is_detected(tmp_path):
    """The real failing artifact's measured signature: video, no audio.

    Guards the diagnosis itself: if a future change made the probe report
    audio=True for this shape, the localization preflight would go green and
    the original 0:a:0 failure would return with no test noticing.
    """
    f = _make_video_only_mp4(tmp_path / "bbe59ac9.mp4")
    inv = probe_stream_inventory(f)

    assert inv.has_video and not inv.has_audio
    assert inv.width == 320 and inv.height == 240
    assert inv.duration is not None and inv.duration > 0


# ── STEP 4B: localization preflight runs BEFORE Demucs ───────────────────────

async def _ensure_schema() -> None:
    """Create the DB tables the runner reads, in the conftest-isolated temp DB.

    A subset run of this file never triggers the FastAPI lifespan that would
    normally call init_db(), so without this the very first UserSettings read
    dies with "no such table: user_settings" — a harness gap, not a product
    finding.
    """
    from backend.database import init_db

    await init_db()


def _create_job_row(job_id: str) -> None:
    """Create the Job row the way the API layer does before starting a tool.

    `update_job_status` returns silently when the row is absent, so a test
    that skips this sees no recorded failure and concludes the gate did not
    fire — when in fact it fired and simply had nowhere to write.
    """
    import sqlite3

    from backend.config import settings as real_settings

    db_path = str(real_settings.DATABASE_URL).replace(
        "sqlite+aiosqlite:///", ""
    ).lstrip("/")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO jobs "
            "(id, user_id, job_type, status, title, progress_pct) "
            "VALUES (?, 'local', 'tool:translate', 'pending', 'qa', 0)",
            (job_id,),
        )
        conn.commit()
    finally:
        conn.close()


class _ExplodingDemucs:
    """Any attempt to construct Demucs is a test failure.

    Instantiating the model is the expensive, GPU-consuming act the preflight
    exists to avoid, so the test asserts it never happens rather than asserting
    on a returned flag.
    """

    def __init__(self, *a, **k):
        raise AssertionError(
            "Demucs was instantiated for a source with no audio stream — "
            "the preflight gate did not run first"
        )


@pytest.fixture
def dub_environment(monkeypatch, tmp_path):
    """A full_dub tool_runners environment with no GPU and no AI providers."""
    from backend.config import settings as real_settings
    from backend.core import tool_runners as TR
    from backend.services import localization_stage_service as stage

    # Isolate storage: the manifest writes under settings.STORAGE_ROOT.
    class _SettingsStub:
        STORAGE_ROOT = None  # set per-instance below
        _sep_module = None    # set per-instance below

        def __getattr__(self, name):
            return getattr(real_settings, name)

    stub = _SettingsStub()
    stub.STORAGE_ROOT = tmp_path / "storage"
    monkeypatch.setattr("backend.config.settings", stub)
    monkeypatch.setattr(stage, "settings", stub)
    monkeypatch.setattr(TR, "settings", stub, raising=False)

    # The runner reads UserSettings from the DB. conftest redirects
    # VIRALMINT_DATA_DIR to a temp dir whose schema is created by
    # init_db(); a subset run that never triggers it has no tables at all.
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _ensure_schema()
    )

    # Demucs must never load. Patch the module attribute the runner imports
    # lazily inside the separation stage.
    import backend.services.audio_separation_service as sep
    monkeypatch.setattr(sep, "Separator", _ExplodingDemucs, raising=False)
    monkeypatch.setattr(
        sep, "separate_vocals_background",
        lambda *a, **k: _ExplodingDemucs(),
        raising=False,
    )

    # Provider preflight would otherwise demand live ElevenLabs/OpenRouter
    # credentials; the A/V gate must be reached without them.
    async def _ok_tts_preflight(*a, **k):
        return None

    monkeypatch.setattr(
        "backend.services.localization_service.validate_localization_tts_preflight",
        _ok_tts_preflight, raising=False,
    )
    # Exposed so a test that legitimately REACHES separation can relax the
    # Demucs guard via the same monkeypatch (auto-undone at teardown) instead
    # of reassigning the module attribute and leaking it into later tests.
    stub._sep_module = monkeypatch
    return stub


def _run_translate(source: Path, mode: str, job_id: str):
    """Run a translation and return the Job row the runner wrote.

    `run_tool_translate` deliberately does NOT re-raise: it records the failure
    on the Job row (status/error_message) and emits a `job_failed` websocket
    event, because the runner is driven by a background task that has no
    caller to raise to. So the observable contract is the RECORDED job, not a
    raised exception — asserting on `pytest.raises` here would test a call
    shape the product does not have.
    """
    from backend.core.tool_runners import run_tool_translate
    _create_job_row(job_id)
    asyncio.run(run_tool_translate(
        job_id=job_id,
        in_path=source,
        target_language="Vietnamese",
        mode=mode,
        voice_id="",
        caption_style="viral",
        emoji_style="auto",
        target_aspect="source",
        user_id="local",
        profile_id="vi-affiliate-hq",
    ))
    return _read_job(job_id)


def _read_job(job_id: str) -> dict:
    import sqlite3

    from backend.config import settings as real_settings

    db_url = str(real_settings.DATABASE_URL)
    db_path = db_url.replace("sqlite+aiosqlite:///", "").lstrip("/")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status, error_message, current_step FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else {}


@requires_ffmpeg
def test_full_dub_on_a_video_only_source_fails_before_demucs(dub_environment, tmp_path):
    source = _make_video_only_mp4(tmp_path / "silent.mp4")

    job = _run_translate(source, "full_dub", "qa-preflight-silent")

    assert job.get("status") == "failed", (
        f"a video-only full_dub source must fail, got: {job}"
    )
    # Demucs is patched to explode on construction. Had the preflight not
    # fired first, the recorded error would be that AssertionError instead.
    assert "Demucs was instantiated" not in (job.get("error_message") or ""), (
        "Demucs was loaded for a source with no audio — preflight did not run first"
    )
    assert "audio" in (job.get("error_message") or "")


@requires_ffmpeg
def test_preflight_error_is_actionable_and_hides_the_ffmpeg_traceback(
    dub_environment, tmp_path
):
    source = _make_video_only_mp4(tmp_path / "silent2.mp4")

    job = _run_translate(source, "full_dub", "qa-preflight-message")
    message = job.get("error_message") or ""

    assert job.get("status") == "failed"
    # The user must be told what to DO.
    assert "không có luồng audio" in message
    assert "tải lại" in message
    assert "chỉ phụ đề" in message
    # And must NOT be handed the raw ffmpeg option failure that this replaces.
    assert "0:a:0" not in message
    assert "matches no streams" not in message
    assert "Failed to set value" not in message


@requires_ffmpeg
def test_preflight_runs_before_any_manifest_or_provider_work_is_touched(
    dub_environment, tmp_path
):
    """The gate is genuinely FIRST — it cannot be a late, cosmetic check.

    A fresh run with a video-only source must fail before the profile is
    materialized, which is where a missing AI key or a TTS credential would
    otherwise surface first. That ordering is the whole point of the fix: the
    user is told their SOURCE is unusable, not that some provider is
    unconfigured.
    """
    source = _make_video_only_mp4(tmp_path / "silent_manifest.mp4")
    job_id = "qa-preflight-first"

    job = _run_translate(source, "full_dub", job_id)

    assert job.get("status") == "failed"
    assert "audio" in (job.get("error_message") or "")

    # No manifest was ever created: the run died before persisting anything.
    manifest = (
        dub_environment.STORAGE_ROOT / "localization_jobs" / job_id / "manifest.json"
    )
    assert not manifest.exists(), (
        "the preflight must fire before the localization run is created, so a "
        "video-only source is reported without any provider setup at all"
    )


@requires_ffmpeg
def test_resumed_run_records_the_audio_failure_on_the_separation_stage(
    dub_environment, tmp_path
):
    """A RESUMED run has a manifest, so the verdict must be recorded on it.

    The fresh-run path raises before a manifest exists. This pins the resume
    path, where one does — the stage record is what a human reads to find out
    why a job stopped.
    """
    import json

    source = _make_video_only_mp4(tmp_path / "silent_resume.mp4")
    job_id = "qa-preflight-resume"

    # Create the run the way a first attempt would, then re-enter.
    from backend.services import localization_stage_service as stage
    run = stage.LocalizationRun(
        job_id,
        source_path=str(source),
        effective_profile={"id": "vi-affiliate-hq"},
        request={"mode": "full_dub"},
    )
    run.ensure_source_copy(source)
    assert run.manifest_path.exists()

    job = _run_translate(source, "full_dub", job_id)
    assert job.get("status") == "failed"

    data = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    assert data["stages"]["separation"]["status"] == "failed"
    assert "audio" in (data["stages"]["separation"]["error"] or "")


@requires_ffmpeg
def test_a_source_with_audio_passes_the_preflight_gate(dub_environment, tmp_path):
    """The gate must not reject a legitimate full_dub source.

    Asserted by the gate's OWN decision, not by how far the run got. Reaching
    Demucs is legitimate here (the source has audio), so the Demucs guard is
    disabled for this test and the assertion is the single thing that matters:
    the run was not rejected for missing audio.
    """
    import backend.services.audio_separation_service as sep

    source = _make_av_mp4(tmp_path / "with_audio.mp4")

    # Demucs is allowed to load: it is the preflight's job to have let us
    # reach it. The real assertion is the recorded error below.
    dub_environment._sep_module.setattr(
        sep, "separate_vocals_background",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reached-separation")),
    )

    job = _run_translate(source, "full_dub", "qa-preflight-good")
    message = job.get("error_message") or ""

    assert "không có luồng audio" not in message, (
        "a source WITH audio must not be rejected by the audio preflight; "
        f"got: {message[:300]}"
    )


@requires_ffmpeg
def test_subtitle_only_mode_does_not_require_audio(dub_environment, tmp_path):
    """Scope guard: a silent clip is legitimate input for a subtitle-only run.

    The requirement is deliberately scoped to full_dub. If this ever fails,
    the gate has been widened into a global 'all media needs audio' rule.
    """
    source = _make_video_only_mp4(tmp_path / "silent_sub.mp4")

    job = _run_translate(source, "subtitle", "qa-preflight-subtitle")
    message = job.get("error_message") or ""

    assert "không có luồng audio" not in message, (
        "subtitle-only mode must not be gated on the presence of audio"
    )


# ── Localization source copy must preserve the stream inventory ─────────────

@requires_ffmpeg
def test_source_copy_preserves_stream_inventory(tmp_path, monkeypatch):
    """STEP 7.8: the persisted source snapshot must not gain or lose streams.

    The real failed job's source.mp4 and its origin video were byte-identical
    (same sha256), so the copy itself is blameless — but the invariant is what
    makes the diagnosis provable next time, so it is pinned here.
    """
    from backend.services import localization_stage_service as stage
    from backend.config import settings as real_settings

    class _SettingsStub:
        def __getattr__(self, name):
            return getattr(real_settings, name)

    stub = _SettingsStub()
    stub.STORAGE_ROOT = tmp_path / "storage"
    monkeypatch.setattr(stage, "settings", stub)

    origin = _make_video_only_mp4(tmp_path / "origin.mp4", seconds=1)
    before = probe_stream_inventory(origin)

    run = stage.LocalizationRun("qa-copy-job", source_path=str(origin))
    copied = run.ensure_source_copy(origin)

    assert copied != origin
    after = probe_stream_inventory(copied)
    assert after.has_video == before.has_video
    assert after.has_audio == before.has_audio
    assert after.video_codec == before.video_codec
    assert after.audio_codec == before.audio_codec

    run2 = stage.LocalizationRun("qa-copy-job-av", source_path=str(_make_av_mp4(tmp_path / "origin_av.mp4")))
    copied_av = run2.ensure_source_copy(tmp_path / "origin_av.mp4")
    assert probe_stream_inventory(copied_av).is_playable_full_video() is True


# ── Error taxonomy ───────────────────────────────────────────────────────────

def test_contract_error_is_a_viralmint_error_so_job_handlers_unchanged():
    from backend.core.exceptions import ViralMintError

    assert issubclass(SourceMediaContractError, ViralMintError)
    assert issubclass(MissingSourceAudioError, SourceMediaContractError)
    # Deliberately NOT a DownloadError: nothing failed to download.
    from backend.core.exceptions import DownloadError

    assert not issubclass(SourceMediaContractError, DownloadError)


def test_contract_error_message_names_source_and_action():
    err = MissingSourceAudioError("C:/videos/bbe59ac9-166.mp4")
    assert "bbe59ac9-166.mp4" in str(err)
    assert "tải lại" in str(err)
