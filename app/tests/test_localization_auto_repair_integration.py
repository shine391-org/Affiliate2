# SPDX-License-Identifier: AGPL-3.0-only
"""Integration coverage for the runner-level bounded auto-repair contract.

The existing unit tests pin ``synthesize_timed_dub``'s targeted-regeneration
and the C6 checkpoint behaviour, and ``test_phase_c_provider_binding`` covers a
dubbing TTS *transport* failure through the runner. What was NOT covered is the
runner wiring for the two recovery entry points the checklist calls out:

* ``LocalizationTimingError`` hard overflow -> retry the dubbing stage exactly
  once with ``regenerate_chunk_ids`` set to ONLY the actual overflow chunks,
  reusing every non-target raw chunk (``invalidate_from(delete_artifacts=False)``);
* translation transient failure -> retry the translation stage exactly once
  while PRESERVING ``translation/checkpoint.json`` (accepted batches are not
  resent);
* the second failure stops after 1/1 with no recursion;
* a deterministic provider/config error must NOT spend the single attempt.

These drive the real ``run_tool_translate`` runner and on-disk
``LocalizationRun``. Most decision-only cases stub whole media stages,
preflight and finalizers: SOURCE_BYTES is an identity fixture, not a video.
Canonical C6 cases below keep the real translator and failed Job persistence;
they stub only the AI reply and unrelated media stages. No LIVE provider is
claimed by this suite.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.config import Settings
from backend.core.localization import (
    LocalizationProfile,
    LocalizationSegment,
    LocalizationTranscript,
)
from backend.database import AsyncSessionLocal, Base, engine
from backend.models.job import Job
from backend.services import localization_service as svc
from backend.services.localization_service import LocalizationTimingError, TimedDubResult
from backend.services.localization_stage_service import LocalizationRun

UID = "local"
SOURCE_BYTES = b"auto-repair-source-media"


# ── schema / DB ─────────────────────────────────────────────────────────────


async def _create_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(scope="module", autouse=True)
def _schema():
    asyncio.run(_create_schema())


@pytest.fixture(autouse=True)
def _clean_rows():
    async def _wipe():
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            for row in (await db.execute(select(Job))).scalars().all():
                await db.delete(row)
            await db.commit()

    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Settings, "STORAGE_ROOT", property(lambda self: root))
    return root


async def _seed_job(job_id: str, *, status: str = "running"):
    async with AsyncSessionLocal() as db:
        db.add(Job(id=job_id, user_id=UID, job_type="tool:translate",
                   status=status, title=job_id))
        await db.commit()


# ── transcript / run fixtures ───────────────────────────────────────────────


def _transcript() -> LocalizationTranscript:
    return LocalizationTranscript(
        text="Hello Thank you",
        language="en",
        segments=[
            LocalizationSegment(
                id=f"s{i}", source_text="Hello", translated_text=text,
                source_start_ms=i * 3000, source_end_ms=i * 3000 + 1000,
                translation_status="success", qc_status="pass",
                overrides={"target_start_ms": i * 3000,
                           "target_end_ms": i * 3000 + 1000},
            )
            for i, text in enumerate(("Xin chào", "Cảm ơn"))
        ],
    )


def _profile(**updates) -> LocalizationProfile:
    base = LocalizationProfile(
        id="qa-repair", name="QA Repair",
        audio_mode="fast_duck", target_aspect="source",
        video_retime_mode="off", dub_rewrite_attempts=0,
        tts_provider="edge_tts", tts_voice="vi-VN-HoaiMyNeural",
    )
    return base.model_copy(update=updates)


def _make_run(job_id: str, storage: Path, *, mode="full_dub", request_extra=None,
              complete_upstream=("separation", "asr", "semantic", "translation")):
    source = storage / f"{job_id}-origin.mp4"
    source.write_bytes(SOURCE_BYTES)
    request = {
        "target_language": "Vietnamese", "mode": mode,
        "voice_id": "", "caption_style": "viral", "emoji_style": "moderate",
        "profile_id": None, "review_translation": False,
    }
    request.update(request_extra or {})
    run = LocalizationRun(
        job_id, source_path=str(source),
        effective_profile=_profile().model_dump(), request=request,
    )
    run.ensure_source_copy(source)
    tx = _transcript()
    for stage in complete_upstream:
        if stage == "separation":
            run.mark_complete_without_artifacts("separation")
        else:
            path = run.save_json(stage, "transcript.json", tx.model_dump())
            run.complete(stage, artifacts={"transcript": path})
    return run


def _patch_leaves(monkeypatch, tmp_path, *, synth):
    """Stub every leaf so only the runner's recovery decision is exercised."""
    from backend.core import tool_runners as runner
    from backend.services import caption_service, localization_qc_service, video_utils

    monkeypatch.setattr(runner, "_load_user_settings",
                        AsyncMock(return_value=SimpleNamespace(
                            ai_provider="compatible", localization_profiles_json=None)))
    monkeypatch.setattr(runner, "_tool_progress", AsyncMock())
    monkeypatch.setattr(runner, "_tool_success", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "_tool_fail", AsyncMock())

    def ok_preflight(*a, **k):
        # Called via asyncio.to_thread, so this MUST be sync.
        return SimpleNamespace(readable=True, has_video=True, has_audio=True)

    monkeypatch.setattr(video_utils, "probe_stream_inventory", ok_preflight)
    monkeypatch.setattr(video_utils, "probe_duration", lambda *a, **k: 12.0)
    monkeypatch.setattr(svc, "synthesize_timed_dub", synth)

    async def mix(source, audio, destination, *a, **k):
        Path(destination).write_bytes(b"mixed")

    monkeypatch.setattr(svc, "mix_dub_audio", mix)

    async def gen(segments, *, output_path, **k):
        Path(output_path).write_text("[Events]\n", encoding="utf-8")
        return output_path

    async def burn(video_path, ass_path, output_path, **k):
        Path(output_path).write_bytes(b"rendered")

    monkeypatch.setattr(caption_service, "generate_captions_ass", gen)
    monkeypatch.setattr(caption_service, "burn_captions", burn)
    monkeypatch.setattr(
        localization_qc_service, "evaluate_localization_qc",
        lambda **k: SimpleNamespace(as_dict=lambda: {
            "status": "NEEDS_REVIEW", "failures": [], "warnings": []}))
    monkeypatch.setattr("backend.api.tools.tool_out_path",
                        lambda job_id, suffix: tmp_path / f"out-{job_id}{suffix}")


def _dub_result(localized, work_dir: Path) -> TimedDubResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    track = work_dir / "dub_track.wav"
    track.write_bytes(b"dub-track")
    return TimedDubResult(
        transcript=localized, dub_track=track, block_paths=[], issues=[],
        chunk_count=2, rewrite_count=0,
    )


async def _run(job_id, run):
    from backend.core.tool_runners import run_tool_translate
    await run_tool_translate(
        job_id, run.source_path, "Vietnamese", run.request["mode"],
        "", "viral", "none", cleanup_input=False,
    )


# ══════════════════════════════════════════════════════════════════════════
# Dubbing hard-overflow auto repair
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dubbing_hard_overflow_auto_repair_targets_only_overflow_chunk(
    storage, monkeypatch, tmp_path,
):
    """ON: retry dubbing once, regenerate ONLY the overflow chunk, reuse others."""
    run = _make_run("dub-overflow-on", storage,
                    request_extra={"auto_repair": True})
    await _seed_job(run.job_id)
    dub_dir = run.stage_dir("dubbing")
    dub_dir.mkdir(parents=True, exist_ok=True)
    non_target = dub_dir / "chunk_0001.wav"
    target = dub_dir / "chunk_0002.wav"
    non_target.write_bytes(b"raw-one")
    target.write_bytes(b"raw-two")
    non_target_before = (non_target.read_bytes(), non_target.stat().st_mtime_ns)

    synth_calls = []

    async def synth(localized, **kwargs):
        synth_calls.append(set(kwargs.get("regenerate_chunk_ids") or set()))
        if len(synth_calls) == 1:
            raise LocalizationTimingError(
                [{"chunk_id": 2, "reason": "timing_overflow",
                  "required_speed": 1.8, "speed_max": 1.30,
                  "emergency_speed_max": 1.35,
                  "segment_ids": ["s1"]}],
                rewrite_count=0,
                all_issues=[{"chunk_id": 2, "reason": "timing_overflow"}],
            )
        return _dub_result(localized, kwargs["work_dir"])

    _patch_leaves(monkeypatch, tmp_path, synth=synth)
    await _run(run.job_id, run)

    assert len(synth_calls) == 2, "dubbing must be retried exactly once"
    assert synth_calls[0] == set(), "initial attempt has no targeted regeneration"
    assert synth_calls[1] == {2}, "retry must regenerate ONLY the overflow chunk"
    # The non-target raw chunk survives the retry untouched (delete_artifacts=False).
    assert (non_target.read_bytes(), non_target.stat().st_mtime_ns) == non_target_before
    fresh = LocalizationRun.load(run.job_id)
    assert fresh.request["auto_repair_attempts"]["dubbing"] == 1
    # Targeted ids are cleared after a successful retry so a later rerun is full.
    assert not fresh.request.get("regenerate_chunk_ids")


@pytest.mark.asyncio
async def test_dubbing_second_hard_overflow_stops_after_one_repair(
    storage, monkeypatch, tmp_path,
):
    """ON: when the retry also overflows, stop at 1/1 with no recursion."""
    run = _make_run("dub-overflow-stop", storage,
                    request_extra={"auto_repair": True})
    await _seed_job(run.job_id)
    from backend.core import tool_runners as runner

    synth_calls = []

    async def synth(localized, **kwargs):
        synth_calls.append(True)
        raise LocalizationTimingError(
            [{"chunk_id": 2, "reason": "timing_overflow",
              "required_speed": 1.9, "speed_max": 1.30,
              "emergency_speed_max": 1.35, "segment_ids": ["s1"]}],
            all_issues=[{"chunk_id": 2, "reason": "timing_overflow"}],
        )

    _patch_leaves(monkeypatch, tmp_path, synth=synth)
    await _run(run.job_id, run)

    assert len(synth_calls) == 2, "exactly one auto-repair attempt (1/1), no recursion"
    runner._tool_fail.assert_awaited_once()
    fresh = LocalizationRun.load(run.job_id)
    assert fresh.request["auto_repair_attempts"]["dubbing"] == 1
    assert fresh.record("dubbing")["status"] == "failed"


@pytest.mark.asyncio
async def test_dubbing_hard_overflow_auto_repair_off_fails_without_retry(
    storage, monkeypatch, tmp_path,
):
    """OFF: the same hard overflow fails immediately with no retry."""
    run = _make_run("dub-overflow-off", storage,
                    request_extra={"auto_repair": False})
    await _seed_job(run.job_id)
    from backend.core import tool_runners as runner

    synth_calls = []

    async def synth(localized, **kwargs):
        synth_calls.append(True)
        raise LocalizationTimingError(
            [{"chunk_id": 2, "reason": "timing_overflow",
              "required_speed": 1.9, "speed_max": 1.30,
              "emergency_speed_max": 1.35, "segment_ids": ["s1"]}],
            all_issues=[{"chunk_id": 2, "reason": "timing_overflow"}],
        )

    _patch_leaves(monkeypatch, tmp_path, synth=synth)
    await _run(run.job_id, run)

    assert len(synth_calls) == 1, "auto_repair=False must not retry"
    runner._tool_fail.assert_awaited_once()
    fresh = LocalizationRun.load(run.job_id)
    assert not fresh.request.get("auto_repair_attempts")
    assert fresh.record("dubbing")["status"] == "failed"


@pytest.mark.asyncio
async def test_dubbing_deterministic_error_does_not_spend_the_auto_repair(
    storage, monkeypatch, tmp_path,
):
    """A non-retryable dubbing error fails immediately, leaving 0 attempts spent."""
    run = _make_run("dub-deterministic", storage,
                    request_extra={"auto_repair": True})
    await _seed_job(run.job_id)
    from backend.core import tool_runners as runner

    synth_calls = []

    async def synth(localized, **kwargs):
        synth_calls.append(True)
        # Non-retryable TTS configuration error (retryable_tts_error -> False).
        raise RuntimeError("invalid voice: selected voice is not configured")

    _patch_leaves(monkeypatch, tmp_path, synth=synth)
    await _run(run.job_id, run)

    assert len(synth_calls) == 1, "deterministic error must not waste the attempt"
    runner._tool_fail.assert_awaited_once()
    fresh = LocalizationRun.load(run.job_id)
    assert not fresh.request.get("auto_repair_attempts")


# ══════════════════════════════════════════════════════════════════════════
# Translation failure auto repair
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_translation_transient_failure_auto_repairs_once_and_preserves_checkpoint(
    storage, monkeypatch, tmp_path,
):
    """ON: transient C6 failure retries once, preserving translation/checkpoint.json."""
    run = _make_run("trans-repair-on", storage,
                    request_extra={"auto_repair": True},
                    complete_upstream=("separation", "asr", "semantic"))
    await _seed_job(run.job_id)
    from backend.core import tool_runners as runner
    from backend.services import caption_service, localization_qc_service, video_utils

    monkeypatch.setattr(runner, "_load_user_settings",
                        AsyncMock(return_value=SimpleNamespace(
                            ai_provider="compatible", localization_profiles_json=None)))
    monkeypatch.setattr(runner, "_tool_progress", AsyncMock())
    monkeypatch.setattr(runner, "_tool_success", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "_tool_fail", AsyncMock())
    monkeypatch.setattr(video_utils, "probe_stream_inventory",
                        lambda *a, **k: SimpleNamespace(
                            readable=True, has_video=True, has_audio=True))
    monkeypatch.setattr(video_utils, "probe_duration", lambda *a, **k: 12.0)

    trans_calls = []

    async def translate(transcript, **kwargs):
        trans_calls.append(True)
        # The stage writes its resumable checkpoint, then fails transiently.
        ckpt = Path(kwargs["checkpoint_dir"]) / "checkpoint.json"
        ckpt.parent.mkdir(parents=True, exist_ok=True)
        ckpt.write_text(json.dumps({"batches": {"0": {"items": [
            {"id": "s0", "text": "Xin chào"}]}}}), encoding="utf-8")
        if len(trans_calls) == 1:
            raise RuntimeError("503 Service Unavailable: provider overloaded")
        out = transcript.model_copy(deep=True)
        for seg in out.segments:
            seg.translated_text = "Xin chào"
        return out

    monkeypatch.setattr(svc, "translate_transcript", translate)

    async def synth(localized, **kwargs):
        return _dub_result(localized, kwargs["work_dir"])

    monkeypatch.setattr(svc, "synthesize_timed_dub", synth)

    async def mix(source, audio, destination, *a, **k):
        Path(destination).write_bytes(b"mixed")

    monkeypatch.setattr(svc, "mix_dub_audio", mix)

    async def gen(segments, *, output_path, **k):
        Path(output_path).write_text("[Events]\n", encoding="utf-8")
        return output_path

    async def burn(video_path, ass_path, output_path, **k):
        Path(output_path).write_bytes(b"rendered")

    monkeypatch.setattr(caption_service, "generate_captions_ass", gen)
    monkeypatch.setattr(caption_service, "burn_captions", burn)
    monkeypatch.setattr(
        localization_qc_service, "evaluate_localization_qc",
        lambda **k: SimpleNamespace(as_dict=lambda: {
            "status": "NEEDS_REVIEW", "failures": [], "warnings": []}))
    monkeypatch.setattr("backend.api.tools.tool_out_path",
                        lambda job_id, suffix: tmp_path / f"out-{job_id}{suffix}")

    await _run(run.job_id, run)

    assert len(trans_calls) == 2, "translation must be retried exactly once"
    ckpt = run.stage_dir("translation") / "checkpoint.json"
    assert ckpt.exists(), "accepted translation checkpoint must survive the retry"
    assert json.loads(ckpt.read_text())["batches"]["0"]["items"] == [
        {"id": "s0", "text": "Xin chào"}]
    fresh = LocalizationRun.load(run.job_id)
    assert fresh.request["auto_repair_attempts"]["translation"] == 1


@pytest.mark.asyncio
async def test_translation_deterministic_error_does_not_spend_the_auto_repair(
    storage, monkeypatch, tmp_path,
):
    """A bad-key/config C6 error fails immediately without a retry."""
    run = _make_run("trans-deterministic", storage,
                    request_extra={"auto_repair": True},
                    complete_upstream=("separation", "asr", "semantic"))
    await _seed_job(run.job_id)
    from backend.core import tool_runners as runner
    from backend.services import video_utils

    monkeypatch.setattr(runner, "_load_user_settings",
                        AsyncMock(return_value=SimpleNamespace(
                            ai_provider="compatible", localization_profiles_json=None)))
    monkeypatch.setattr(runner, "_tool_progress", AsyncMock())
    monkeypatch.setattr(runner, "_tool_success", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "_tool_fail", AsyncMock())
    monkeypatch.setattr(video_utils, "probe_stream_inventory",
                        lambda *a, **k: SimpleNamespace(
                            readable=True, has_video=True, has_audio=True))

    trans_calls = []

    async def translate(transcript, **kwargs):
        trans_calls.append(True)
        raise RuntimeError("Invalid API key for the translation provider")

    monkeypatch.setattr(svc, "translate_transcript", translate)

    await _run(run.job_id, run)

    assert len(trans_calls) == 1, "deterministic C6 error must not retry"
    runner._tool_fail.assert_awaited_once()
    fresh = LocalizationRun.load(run.job_id)
    assert not fresh.request.get("auto_repair_attempts")


@pytest.mark.parametrize("status,message", [
    (401, "Unauthorized"),
    (403, "Forbidden"),
    (404, "Not Found"),
    (None, "Invalid model: the requested model does not exist"),
])
@pytest.mark.asyncio
async def test_translation_deterministic_error_does_not_spend_attempt(
    storage, monkeypatch, tmp_path, status, message,
):
    """A deterministic translation error must fail without a retry.

    The message deliberately omits every literal `deterministic_markers` token so
    the only signal is the HTTP status / error class — exactly the shape a real
    provider returns.
    """
    run = _make_run(f"trans-det-{status}", storage,
                    request_extra={"auto_repair": True},
                    complete_upstream=("separation", "asr", "semantic"))
    await _seed_job(run.job_id)
    from backend.core import tool_runners as runner
    from backend.services import video_utils

    monkeypatch.setattr(runner, "_load_user_settings",
                        AsyncMock(return_value=SimpleNamespace(
                            ai_provider="compatible", localization_profiles_json=None)))
    monkeypatch.setattr(runner, "_tool_progress", AsyncMock())
    monkeypatch.setattr(runner, "_tool_success", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "_tool_fail", AsyncMock())
    monkeypatch.setattr(video_utils, "probe_stream_inventory",
                        lambda *a, **k: SimpleNamespace(
                            readable=True, has_video=True, has_audio=True))

    trans_calls = []

    class ProviderError(RuntimeError):
        if status is not None:
            status_code = status

    async def translate(transcript, **kwargs):
        trans_calls.append(True)
        raise ProviderError(message)

    monkeypatch.setattr(svc, "translate_transcript", translate)

    await _run(run.job_id, run)

    assert len(trans_calls) == 1, (
        f"a deterministic translation error ({status}: {message}) must not spend "
        f"the auto-repair attempt"
    )
    fresh = LocalizationRun.load(run.job_id)
    assert not fresh.request.get("auto_repair_attempts")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
@pytest.mark.parametrize("attribute,wrapped", [("status_code", False), ("code", True)])
async def test_canonical_c6_deterministic_status_skips_stage_repair(
    storage, monkeypatch, tmp_path, status, attribute, wrapped,
):
    """C6's own exhausted-route wrapper must retain the original status signal."""
    from backend.core import tool_runners as runner

    calls = []

    class ConfigAI:
        provider = SimpleNamespace(value="compatible")
        model = "configured-model"
        connection_name = "test-configured-route"
        max_retries = 0

        async def chat(self, **kwargs):
            calls.append(kwargs["messages"])
            error = RuntimeError("Provider rejected configuration")
            setattr(error, attribute, str(status))
            if wrapped:
                raise RuntimeError("Provider route failed") from error
            raise error

    ai = ConfigAI()
    monkeypatch.setattr(svc, "_profile_ai_route_clients_compat", lambda *args: [ai])
    run = _make_run(f"cfg-real-{status}-{attribute}", storage,
                    request_extra={"auto_repair": True},
                    complete_upstream=("separation", "asr", "semantic"))
    await _seed_job(run.job_id)
    before = {stage: dict(run.record(stage)) for stage in ("separation", "asr", "semantic")}
    real_fail = runner._tool_fail
    _patch_leaves(monkeypatch, tmp_path, synth=AsyncMock())
    monkeypatch.setattr(runner, "_tool_fail", real_fail)
    await _run(run.job_id, run)
    fresh = LocalizationRun.load(run.job_id)
    assert len(calls) == 1
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, run.job_id)
        assert job is not None
        assert str(job.status) == "failed"
        assert str(job.error_message)
    assert fresh.record("translation")["status"] == "failed"
    assert not fresh.request.get("auto_repair_attempts")
    assert {stage: fresh.record(stage) for stage in before} == before


@pytest.mark.asyncio
async def test_translation_auto_repair_off_fails_without_retry(
    storage, monkeypatch, tmp_path,
):
    """OFF: a transient C6 failure fails immediately with no retry."""
    run = _make_run("trans-repair-off", storage,
                    request_extra={"auto_repair": False},
                    complete_upstream=("separation", "asr", "semantic"))
    await _seed_job(run.job_id)
    from backend.core import tool_runners as runner
    from backend.services import video_utils

    monkeypatch.setattr(runner, "_load_user_settings",
                        AsyncMock(return_value=SimpleNamespace(
                            ai_provider="compatible", localization_profiles_json=None)))
    monkeypatch.setattr(runner, "_tool_progress", AsyncMock())
    monkeypatch.setattr(runner, "_tool_success", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "_tool_fail", AsyncMock())
    monkeypatch.setattr(video_utils, "probe_stream_inventory",
                        lambda *a, **k: SimpleNamespace(
                            readable=True, has_video=True, has_audio=True))

    trans_calls = []

    async def translate(transcript, **kwargs):
        trans_calls.append(True)
        raise RuntimeError("503 Service Unavailable: provider overloaded")

    monkeypatch.setattr(svc, "translate_transcript", translate)

    await _run(run.job_id, run)

    assert len(trans_calls) == 1, "auto_repair=False must not retry translation"
    runner._tool_fail.assert_awaited_once()
    fresh = LocalizationRun.load(run.job_id)
    assert not fresh.request.get("auto_repair_attempts")


@pytest.mark.asyncio
async def test_translation_transient_wrapped_cause_still_retries(
    storage, monkeypatch, tmp_path,
):
    """A transient failure wrapped in another exception must still auto-repair."""
    run = _make_run("trans-wrapped", storage,
                    request_extra={"auto_repair": True},
                    complete_upstream=("separation", "asr", "semantic"))
    await _seed_job(run.job_id)
    from backend.core import tool_runners as runner
    from backend.services import caption_service, localization_qc_service, video_utils

    monkeypatch.setattr(runner, "_load_user_settings",
                        AsyncMock(return_value=SimpleNamespace(
                            ai_provider="compatible", localization_profiles_json=None)))
    monkeypatch.setattr(runner, "_tool_progress", AsyncMock())
    monkeypatch.setattr(runner, "_tool_success", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "_tool_fail", AsyncMock())
    monkeypatch.setattr(video_utils, "probe_stream_inventory",
                        lambda *a, **k: SimpleNamespace(
                            readable=True, has_video=True, has_audio=True))
    monkeypatch.setattr(video_utils, "probe_duration", lambda *a, **k: 12.0)

    trans_calls = []

    async def translate(transcript, **kwargs):
        trans_calls.append(True)
        if len(trans_calls) == 1:
            cause = TimeoutError("read timeout")
            raise RuntimeError("translation failed") from cause
        out = transcript.model_copy(deep=True)
        return out

    monkeypatch.setattr(svc, "translate_transcript", translate)

    async def synth(localized, **kwargs):
        return _dub_result(localized, kwargs["work_dir"])

    monkeypatch.setattr(svc, "synthesize_timed_dub", synth)

    async def mix(source, audio, destination, *a, **k):
        Path(destination).write_bytes(b"mixed")

    monkeypatch.setattr(svc, "mix_dub_audio", mix)

    async def gen(segments, *, output_path, **k):
        Path(output_path).write_text("[Events]\n", encoding="utf-8")
        return output_path

    async def burn(video_path, ass_path, output_path, **k):
        Path(output_path).write_bytes(b"rendered")

    monkeypatch.setattr(caption_service, "generate_captions_ass", gen)
    monkeypatch.setattr(caption_service, "burn_captions", burn)
    monkeypatch.setattr(
        localization_qc_service, "evaluate_localization_qc",
        lambda **k: SimpleNamespace(as_dict=lambda: {
            "status": "NEEDS_REVIEW", "failures": [], "warnings": []}))
    monkeypatch.setattr("backend.api.tools.tool_out_path",
                        lambda job_id, suffix: tmp_path / f"out-{job_id}{suffix}")

    await _run(run.job_id, run)

    assert len(trans_calls) == 2, "a transient wrapped cause must still auto-repair"
    fresh = LocalizationRun.load(run.job_id)
    assert fresh.request["auto_repair_attempts"]["translation"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_repair", [False, True])
async def test_measured_c7_runner_hard_overflow_preserves_accepted_rewrite_and_ownership(
    storage, monkeypatch, tmp_path, auto_repair,
):
    """LOCAL media integration: real C7/FFprobe/fit, leaf AI/TTS are doubles.

    A generated tone measures 1.8s in an explicitly locked 1.0s target window.
    Accepted shorter wording still cannot fit; ON must retry only chunk 2 once
    and retain the valid sibling plus the accepted script and original owners.
    This proves recovery mechanics, not LIVE provider behavior or prosody.
    """
    import hashlib
    import shutil
    import subprocess
    import sys
    from backend.core import tool_runners as runner
    from backend.services import tts_service, video_utils

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe required for measured media integration")
    source = tmp_path / "measured-source.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=320x180:d=7",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=7", "-c:v", "libx264",
        "-preset", "ultrafast", "-threads", "1", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(source),
    ], check=True, capture_output=True)
    monkeypatch.setattr(sys.modules[__name__], "SOURCE_BYTES", source.read_bytes())
    run = _make_run(f"measured-hard-{auto_repair}", storage,
                    request_extra={"auto_repair": auto_repair})
    await _seed_job(run.job_id)
    profile = _profile(dub_min_cue_duration_ms=250, dub_max_chunk_size=1,
                       dub_gap_tolerance_ms=0, dub_rewrite_attempts=1)
    run.data["effective_profile"] = profile.model_dump(mode="json")
    tx = _transcript()
    tx.segments[0].speaker_id = "speaker-A"
    tx.segments[1].speaker_id = "speaker-B"
    original = "Cảm ơn mọi người đã xem video này"
    tx.segments[1].translated_text = original
    path = run.save_json("translation", "transcript.json", tx.model_dump())
    run.complete("translation", artifacts={"transcript": path})
    run._write()
    upstream = {stage: dict(run.record(stage)) for stage in ("separation", "asr", "semantic", "translation")}
    real_synth = svc.synthesize_timed_dub
    real_fail = runner._tool_fail
    real_preflight = video_utils.probe_stream_inventory
    real_duration = video_utils.probe_duration
    real_plan = svc.build_plan
    planned_runs = []
    generated = []
    measured = []
    attempts = []
    snapshots = []

    def observe_plan(*args, **kwargs):
        result = real_plan(*args, **kwargs)
        planned_runs.append(result)
        return result

    async def voices(*args, **kwargs):
        return [{"voice_id": "vi-VN-HoaiMyNeural", "gender": "female"},
                {"voice_id": "vi-VN-NamMinhNeural", "gender": "male"}]

    async def tone(text, *, output_path, **kwargs):
        generated.append({"text": text, "voice": kwargs.get("voice_id")})
        duration = 0.8 if text == "Xin chào" else 1.8
        await asyncio.to_thread(subprocess.run, [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"sine=frequency=440:sample_rate=24000:duration={duration}",
            "-c:a", "pcm_s16le", "-f", "wav", str(output_path),
        ], check=True, capture_output=True)
        return output_path

    class RewriteAI:
        provider = SimpleNamespace(value="compatible")
        model = "recorded-rewrite"
        connection_name = "recorded"
        max_retries = 0

        async def chat(self, **kwargs):
            return json.dumps([{"id": "s1", "text": "Cảm ơn"}], ensure_ascii=False)

    def raw_snapshot(work):
        return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in work.glob("chunk_0001.wav*")}

    async def synth(localized, **kwargs):
        attempts.append(set(kwargs.get("regenerate_chunk_ids") or set()))
        work = kwargs["work_dir"]
        if len(attempts) == 2:
            assert raw_snapshot(work) == snapshots[0]
        try:
            return await real_synth(localized, **kwargs)
        except LocalizationTimingError as exc:
            measured.append(exc.issues)
            if len(attempts) == 1:
                snapshots.append(raw_snapshot(work))
                assert snapshots[0]
            raise

    _patch_leaves(monkeypatch, tmp_path, synth=synth)
    monkeypatch.setattr(runner, "_tool_fail", real_fail)
    monkeypatch.setattr(video_utils, "probe_stream_inventory", real_preflight)
    monkeypatch.setattr(video_utils, "probe_duration", real_duration)
    monkeypatch.setattr(svc, "build_plan", observe_plan)
    monkeypatch.setattr(tts_service, "list_voices", voices)
    monkeypatch.setattr(tts_service, "generate_tts", tone)
    monkeypatch.setattr(svc, "_profile_ai_route_clients_compat", lambda *args: [RewriteAI()])
    await _run(run.job_id, run)
    fresh = LocalizationRun.load(run.job_id)
    assert attempts == ([set(), {2}] if auto_repair else [set()])
    assert all({issue["chunk_id"] for issue in issues} == {2} for issues in measured)
    assert all(issue["required_speed"] > 1.35 for issues in measured for issue in issues)
    assert raw_snapshot(run.stage_dir("dubbing")) == snapshots[0]
    assert sum(it["text"] == "Xin chào" for it in generated) == 1
    assert [it["text"] for it in generated if it["text"] != "Xin chào"] == (
        [original, "Cảm ơn", "Cảm ơn"] if auto_repair else [original, "Cảm ơn"])
    assert fresh.request.get("auto_repair_attempts", {}).get("dubbing", 0) == int(auto_repair)
    assert {stage: fresh.record(stage) for stage in upstream} == upstream
    work = run.stage_dir("dubbing")
    # Hard failure does not export a final plan/chunks artifact. Observe the
    # REAL planner's unchanged return objects instead of inventing those files.
    assert len(planned_runs) == len(attempts)
    for plan, chunks in planned_runs:
        assert [(chunk.id, [plan[i].segment_id for i in chunk.item_indexes])
                for chunk in chunks] == [(1, ["s0"]), (2, ["s1"])]
        assert [(item.segment_id, item.speaker_id) for item in plan] == [("s0", "speaker-A"), ("s1", "speaker-B")]
        assert plan[1].spoken_text == "Cảm ơn"
        assert plan[1].original_text == original
    plan, chunks = planned_runs[-1]
    state = json.loads((run.root / "dubbing_state.json").read_text())
    assert state["accepted_text"] == {"s1": "Cảm ơn"}
    assert state["rewrite_count"] == 1
    for chunk in chunks:
        actual = await svc._audio_duration_ms(work / f"chunk_{chunk.id:04d}.wav")
        assert abs(actual - chunk.actual_duration_ms) <= 2
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, run.job_id)
        assert job is not None
        assert str(job.status) == "failed"
        assert str(job.error_message)
    (work / "qa-measured-proof.json").write_text(json.dumps({
        "verification": "LOCAL FFmpeg integration; leaf AI/TTS doubles",
        "auto_repair": auto_repair, "attempts": [sorted(ids) for ids in attempts],
        "measured_issues": measured, "accepted_text": state["accepted_text"],
        "rewrite_count": state["rewrite_count"], "non_target_unchanged": True,
        "upstream_timestamps_unchanged": True,
        "ownership": [{"chunk_id": c.id, "segment_ids": [plan[i].segment_id for i in c.item_indexes],
                       "actual_ms": c.actual_duration_ms} for c in chunks],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
