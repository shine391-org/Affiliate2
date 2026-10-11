# SPDX-License-Identifier: AGPL-3.0-only
"""Execution-cleanup regressions for C1-C4 (offline; no live provider/DB).

C1  describe_executor must not attribute foreign class state to an unrecorded
    model instance, and _describe_executed must merge coherently.
C2  A TTS chunk deadline that expires DURING backoff must surface as the
    normalized chunk RuntimeError (with the real attempt count), never a bare
    TimeoutError.
C3  Zero-gap chunks (next chunk starts exactly at this chunk's end) must still
    get real FFmpeg bounded refit inside the existing quality caps.
C4  A provider writer thread that lands AFTER cancellation must never publish a
    partial/foreign artifact and must not mask Timeout/JobCancelledError.
"""
import array
import asyncio
import math
import os
import wave
from pathlib import Path

import pytest

from backend.core.exceptions import JobCancelledError
from backend.core.localization import LocalizationProfile, LocalizationSegment, LocalizationTranscript
from backend.services import localization_service as svc
from backend.services.tts_service import TTSProvider
from backend.services.video_utils import probe_duration as real_probe_duration
from backend.services.whisper_service import WhisperService


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _wav(path: Path, ms: int, *, silent: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 24000
    samples = array.array(
        "h",
        (0 if silent else round(3000 * math.sin(2 * math.pi * 440 * n / rate))
         for n in range(rate * ms // 1000)),
    )
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(samples.tobytes())
    return path


def _runtime() -> svc.TTSRuntimeConfig:
    return svc.TTSRuntimeConfig(provider=TTSProvider.OPENAI_COMPATIBLE, default_voice="fixture",
                                api_key="", model="fixture", base_url=None, region=None)


class _ForeignModel:
    """Stands in for a faster-whisper model instance carrying its own attrs."""

    def __init__(self, device=None, compute_type=None):
        if device is not None:
            self.device = device
        if compute_type is not None:
            self.compute_type = compute_type


@pytest.fixture
def whisper_class_state():
    saved = (WhisperService._model, WhisperService._loaded_model_name,
             WhisperService._loaded_device, WhisperService._loaded_compute_type,
             WhisperService._loaded_quality, dict(WhisperService._executor_by_model))
    WhisperService._model = None
    WhisperService._loaded_model_name = None
    WhisperService._loaded_device = None
    WhisperService._loaded_compute_type = None
    WhisperService._loaded_quality = None
    WhisperService._executor_by_model = type(WhisperService._executor_by_model)()
    try:
        yield
    finally:
        (WhisperService._model, WhisperService._loaded_model_name,
         WhisperService._loaded_device, WhisperService._loaded_compute_type,
         WhisperService._loaded_quality, recorded) = saved
        WhisperService._executor_by_model.clear()
        WhisperService._executor_by_model.update(recorded)


# --------------------------------------------------------------------------- #
# C1 - executor provenance for an UNRECORDED model instance
# --------------------------------------------------------------------------- #
def test_c1_unrecorded_instance_is_not_given_foreign_class_state(whisper_class_state):
    resident = _ForeignModel(device="cuda", compute_type="float16")
    WhisperService._model = resident
    WhisperService._loaded_model_name = "large-v3"
    WhisperService._loaded_device = "cuda"
    WhisperService._loaded_compute_type = "float16"

    unrecorded = _ForeignModel(device="cpu", compute_type="int8")
    record = WhisperService.describe_executor(unrecorded)

    # It may state what THIS instance proves, but must not claim the resident
    # singleton's identity or invent an executor for it.
    assert record["model"] in (None, "")
    assert record["device"] == "cpu"
    assert record["compute_type"] == "int8"
    assert record["fallback_used"] is False


def test_c1_partially_attributed_instance_is_coherent_after_merge(whisper_class_state):
    """A model with only ONE known attribute must not keep the foreign half."""
    WhisperService._loaded_model_name = "medium"
    WhisperService._loaded_device = "cuda"
    WhisperService._loaded_compute_type = "float16"

    base = WhisperService.describe_executor(_ForeignModel(device="cpu"))
    merged = svc_whisper_describe_executed(_ForeignModel(device="cpu"), base)
    assert merged["device"] == "cpu"
    # compute_type is unknown for this instance: it must be unknown, not float16.
    assert merged["compute_type"] in (None, "")
    assert merged["model"] in (None, "")


def svc_whisper_describe_executed(model, provenance):
    from backend.services.whisper_service import _describe_executed
    return _describe_executed(model, provenance)


def test_c1_recorded_executor_is_untouched_by_the_fix(whisper_class_state):
    resident = _ForeignModel(device="cuda", compute_type="float16")
    WhisperService._model = resident
    WhisperService._loaded_model_name = "large-v3"
    WhisperService._loaded_device = "cuda"
    WhisperService._loaded_compute_type = "float16"
    WhisperService._executor_by_model[resident] = {
        "model": "large-v3", "device": "cuda", "compute_type": "float16",
        "requested_device": "cuda", "fallback_used": True, "fallback_reason": "recorded",
    }
    record = WhisperService.describe_executor(resident)
    assert record["model"] == "large-v3"
    assert record["fallback_reason"] == "recorded"


def test_c1_unload_does_not_erase_a_live_instance_record(whisper_class_state):
    resident = _ForeignModel(device="cpu", compute_type="int8")
    WhisperService._model = resident
    WhisperService._loaded_model_name = "small"
    WhisperService._loaded_device = "cpu"
    WhisperService._loaded_compute_type = "int8"
    WhisperService._executor_by_model[resident] = {
        "model": "small", "device": "cpu", "compute_type": "int8",
        "requested_device": "cpu", "fallback_used": False, "fallback_reason": None,
    }
    assert WhisperService.unload() is True
    # The record is keyed BY the still-live instance, so it survives eviction.
    assert WhisperService.describe_executor(resident)["model"] == "small"


# --------------------------------------------------------------------------- #
# C2 - deadline exhausted during backoff
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_c2_backoff_budget_expiry_is_normalized(monkeypatch, tmp_path):
    calls = []

    async def always_retryable(text, **kwargs):
        calls.append(text)
        raise ConnectionError("TTS provider temporarily unavailable")

    monkeypatch.setattr("backend.services.tts_service.generate_tts", always_retryable)
    monkeypatch.setattr(svc, "_TTS_CHUNK_DEADLINE_S", 0.02)

    with pytest.raises(RuntimeError) as caught:
        await svc._generate_tts_chunk(
            "Lời dịch", runtime=_runtime(), voice="fixture",
            profile=LocalizationProfile(id="qa-c2", name="qa"), speaker_id=None,
            target_language="vi", output_path=tmp_path / "chunk.wav", attempts=3,
        )
    message = str(caught.value)
    assert "attempts" in message
    assert len(calls) < 3, f"deadline must stop the loop, not the attempt count: {calls}"
    # The normalized message must state the attempts ACTUALLY made.
    assert f"{len(calls)} attempt" in message


@pytest.mark.asyncio
async def test_c2_cancellation_during_backoff_is_not_masked(monkeypatch, tmp_path):
    calls = []

    async def always_retryable(text, **kwargs):
        calls.append(text)
        raise ConnectionError("TTS provider temporarily unavailable")

    async def cancelled():
        return len(calls) >= 1

    monkeypatch.setattr("backend.services.tts_service.generate_tts", always_retryable)
    monkeypatch.setattr(svc, "_TTS_CHUNK_DEADLINE_S", 30.0)
    with pytest.raises(JobCancelledError):
        await svc._generate_tts_chunk(
            "Lời dịch", runtime=_runtime(), voice="fixture",
            profile=LocalizationProfile(id="qa-c2b", name="qa"), speaker_id=None,
            target_language="vi", output_path=tmp_path / "chunk.wav", attempts=3,
            cancel_check=cancelled,
        )


# --------------------------------------------------------------------------- #
# C3 - zero-gap real-FFmpeg refit inside existing caps
# --------------------------------------------------------------------------- #
def _source_probe(seconds):
    return lambda path, *args, **kw: seconds if Path(path).name == "source.mp4" else real_probe_duration(path, *args, **kw)


@pytest.mark.asyncio
async def test_c3_zero_gap_chunk_refits_within_cap(tmp_path, monkeypatch):
    """Adjacent chunks (next_start == this chunk's end) still get real refit."""
    if not shutil_which("ffmpeg"):
        pytest.skip("FFmpeg required")
    raw_ms = 1900

    async def generate(text, *, output_path, **kwargs):
        _wav(output_path, raw_ms)
        return raw_ms

    monkeypatch.setattr(svc, "_generate_tts_chunk", generate)
    monkeypatch.setattr(svc, "_tts_config", lambda *a, **k: _runtime())
    monkeypatch.setattr("backend.services.video_utils.probe_duration", _source_probe(3.6))
    tx = LocalizationTranscript(segments=[
        LocalizationSegment(id="s1", source_text="a", translated_text="Lời một.",
                            source_start_ms=0, source_end_ms=1800,
                            translation_status="success",
                            overrides={"target_start_ms": 0, "target_end_ms": 1800}),
        LocalizationSegment(id="s2", source_text="b", translated_text="Lời hai.",
                            source_start_ms=1800, source_end_ms=3600,
                            translation_status="success",
                            overrides={"target_start_ms": 1800, "target_end_ms": 3600}),
    ])
    profile = LocalizationProfile(id="qa-c3", name="qa", dub_rewrite_attempts=0, video_retime_mode="off")
    result = await svc.synthesize_timed_dub(
        tx, source_media=tmp_path / "source.mp4", target_language="Vietnamese",
        profile=profile, user_settings=object(), work_dir=tmp_path / "work")
    measurements = result.transcript.metadata["dubbing"]["fitted_audio"]
    assert len(measurements) == 2
    for m in measurements:
        # Fits its own window, and the extra idle space is bounded by the
        # postfit rounding tolerance - never the next speaker's slot.
        assert m["measured_end_ms"] <= m["allowed_end_ms"] if "allowed_end_ms" in m else True


def shutil_which(name):
    import shutil
    return shutil.which(name)


# --------------------------------------------------------------------------- #
# C4 - late provider writer thread after cancellation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_c4_late_writer_thread_never_publishes_or_leaks(tmp_path, monkeypatch):
    """openai_tts_service writes from a SYNC SDK thread via asyncio.to_thread.

    Cancelling the await cannot kill that thread, so its write lands AFTER the
    atomic wrapper gave up. Nothing partial may be published and the cleanup
    must not raise.
    """
    if not shutil_which("ffprobe"):
        pytest.skip("FFprobe required")
    gate = threading_event()
    output = tmp_path / "chunk_0001.wav"
    _wav(output, 800)
    sidecar = svc._chunk_identity_path(output)
    before_bytes = output.read_bytes()

    async def late_writer(text, *, output_path, **kwargs):
        def _sync():
            gate.wait(5)
            _wav(Path(output_path), 800)
        # Same shape as the real provider: sync file write inside a worker thread.
        await asyncio.to_thread(_sync)
        raise TimeoutError("TTS provider timed out")

    monkeypatch.setattr("backend.services.tts_service.generate_tts", late_writer)
    monkeypatch.setattr(svc, "_TTS_REQUEST_TIMEOUT_S", 0.05)
    monkeypatch.setattr(svc, "_TTS_CHUNK_DEADLINE_S", 0.2)

    task = asyncio.ensure_future(svc._generate_tts_chunk_atomic(
        "Lời dịch", output_path=output, identity="new-input",
        runtime=_runtime(), voice="fixture",
        profile=LocalizationProfile(id="qa-c4", name="qa"), speaker_id=None,
        target_language="vi", attempts=2))
    await asyncio.sleep(0.3)
    gate.set()
    with pytest.raises((RuntimeError, TimeoutError)):
        await task
    # Cleanup no longer sleeps inside the failing call, so drive the bounded
    # sweeper deterministically instead of racing a name-substring scan.
    for _ in range(100):
        if any(".attempt-" in p.name or ".pending-" in p.name for p in tmp_path.iterdir()):
            await asyncio.sleep(0.02)
    await svc._sweep_late_artifacts()

    assert output.read_bytes() == before_bytes, "cancelled run must not replace the accepted chunk"
    assert sidecar.exists() is False or sidecar.read_text().find("new-input") < 0
    leftovers = [p.name for p in tmp_path.iterdir() if p != output and p != sidecar]
    assert leftovers == [], f"pending artifacts left behind: {leftovers}"


@pytest.mark.asyncio
async def test_c4_cancellation_error_class_survives_late_writer(tmp_path, monkeypatch):
    gate = threading_event()

    async def cancel():
        return True

    async def late_writer(text, *, output_path, **kwargs):
        def _sync():
            gate.wait(5)
            _wav(Path(output_path), 800)
        await asyncio.to_thread(_sync)
        return Path(output_path)

    monkeypatch.setattr("backend.services.tts_service.generate_tts", late_writer)
    output = tmp_path / "chunk.wav"
    with pytest.raises(JobCancelledError):
        await svc._generate_tts_chunk_atomic(
            "Lời dịch", output_path=output, identity="id",
            runtime=_runtime(), voice="fixture",
            profile=LocalizationProfile(id="qa-c4b", name="qa"), speaker_id=None,
            target_language="vi", attempts=1, cancel_check=cancel)
    gate.set()
    assert not output.exists()


def threading_event():
    import threading
    return threading.Event()


# --------------------------------------------------------------------------- #
# C4 - the real openai_tts_service offline SDK stub (no paid call)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_c4_openai_tts_streams_into_a_private_part_file(tmp_path, monkeypatch):
    """Offline SDK stub: max_retries=0, synchronous thread, partial write."""
    import backend.services.openai_tts_service as oai
    observed = {}

    class _StubResponse:
        def stream_to_file(self, path):
            Path(path).write_bytes(b"partial-audio")
            observed["wrote_to"] = str(path)

    class _StubSpeech:
        def create(self, **kwargs):
            observed["create"] = kwargs
            return _StubResponse()

    class _StubClient:
        def __init__(self, **kwargs):
            observed["client_kwargs"] = kwargs
            self.audio = type("A", (), {"speech": _StubSpeech()})()

    class _StubOpenAI:
        OpenAI = _StubClient

    monkeypatch.setitem(__import__("sys").modules, "openai", _StubOpenAI)
    target = tmp_path / "tts.mp3"
    await oai.generate_voice("Xin chào", voice_id="alloy", api_key="stub-key", output_path=target)
    assert observed["client_kwargs"].get("max_retries") == 0
    assert observed["wrote_to"] != str(target), "provider must stream to a private part file"
    assert target.read_bytes() == b"partial-audio"
    assert [p.name for p in tmp_path.iterdir()] == ["tts.mp3"], "part file must be renamed away"


@pytest.mark.asyncio
async def test_c4_cancelled_openai_tts_thread_publishes_nothing(tmp_path, monkeypatch):
    """Cancelling the await cannot kill the SYNC SDK thread: prove the late
    write lands on no published path and leaves no part/partial artifact."""
    import backend.services.openai_tts_service as oai
    started = threading_event()
    release = threading_event()
    target = tmp_path / "tts.mp3"

    class _StubResponse:
        def stream_to_file(self, path):
            started.set()
            release.wait(5)
            Path(path).write_bytes(b"late-partial-audio")

    class _StubSpeech:
        def create(self, **kwargs):
            return _StubResponse()

    class _StubClient:
        def __init__(self, **kwargs):
            self.audio = type("A", (), {"speech": _StubSpeech()})()

    class _StubOpenAI:
        OpenAI = _StubClient

    monkeypatch.setitem(__import__("sys").modules, "openai", _StubOpenAI)
    task = asyncio.ensure_future(
        oai.generate_voice("Xin chào", voice_id="alloy", api_key="stub-key", output_path=target))
    while not started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not target.exists(), "cancelled generation must not publish audio"
    release.set()
    for _ in range(100):
        if not list(tmp_path.iterdir()):
            break
        await asyncio.sleep(0.05)
    leftovers = [p.name for p in tmp_path.iterdir()]
    assert leftovers == [], f"cancelled provider left artifacts: {leftovers}"


# --------------------------------------------------------------------------- #
# C4 (2nd pass) - per-attempt isolation + producer-side cancellation fencing
# --------------------------------------------------------------------------- #
def _late_writer_provider(*, arm=None, ready=None, ms=700, gate_wait=5.0):
    """A provider whose SYNC writer thread lands AFTER the await was abandoned.

    Mirrors the real shape: the bytes are written from a worker thread the event
    loop cannot stop, so they only appear once `arm` is set.
    """
    seen = []

    async def provider(text, *, output_path, **kwargs):
        index = len(seen)
        seen.append(output_path)
        if index == 0:
            loop = asyncio.get_running_loop()

            def _sync():
                arm.wait(gate_wait)
                _wav(Path(output_path), ms)
                if ready is not None:
                    ready.set()

            loop.run_in_executor(None, _sync)
            raise ConnectionError("TTS provider temporarily unavailable")
        if arm is not None:
            arm.set()
        if ready is not None and not ready.wait(gate_wait):
            raise AssertionError("late writer never landed")
        return Path(output_path)

    provider.seen = seen
    return provider


@pytest.mark.asyncio
async def test_c4_retry2_cannot_adopt_retry1_late_bytes(tmp_path, monkeypatch):
    """NEGATIVE CONTROL: bytes a cancelled attempt's thread writes AFTER the
    next attempt already cleared the path must not be measured and published as
    the next attempt's success. Each attempt owns a private file."""
    import threading

    arm = threading.Event()
    landed = threading.Event()
    provider = _late_writer_provider(arm=arm, ready=landed, ms=700)
    monkeypatch.setattr("backend.services.tts_service.generate_tts", provider)
    monkeypatch.setattr(svc, "_TTS_CHUNK_DEADLINE_S", 20.0)
    output = tmp_path / "chunk_0001.wav"

    with pytest.raises(RuntimeError):
        await svc._generate_tts_chunk_atomic(
            "Lời dịch", output_path=output, identity="iso",
            runtime=_runtime(), voice="fixture",
            profile=LocalizationProfile(id="qa-c4c", name="qa"), speaker_id=None,
            target_language="vi", attempts=2)

    seen = provider.seen
    assert len(seen) == 2, f"expected a second attempt, saw {seen}"
    assert seen[0] != seen[1], "attempts must not share a provider output path"
    assert not output.exists(), "no attempt produced audio, nothing may be published"
    assert not svc._chunk_identity_path(output).exists()
    await svc._sweep_late_artifacts()


@pytest.mark.asyncio
async def test_c4_cancelled_run_leaves_published_bytes_and_identity_untouched(
    tmp_path, monkeypatch,
):
    """An already accepted chunk must survive a cancelled regeneration byte for
    byte, sidecar identity included."""
    gate = threading_event()
    output = _wav(tmp_path / "chunk_0002.wav", 500)
    svc._write_chunk_identity(output, "accepted-identity", 500)
    sidecar = svc._chunk_identity_path(output)
    before = output.read_bytes()
    before_identity = sidecar.read_text(encoding="utf-8")

    async def late_writer(text, *, output_path, **kwargs):
        def _sync():
            gate.wait(5)
            _wav(Path(output_path), 800)

        await asyncio.to_thread(_sync)
        raise TimeoutError("TTS provider timed out")

    monkeypatch.setattr("backend.services.tts_service.generate_tts", late_writer)
    monkeypatch.setattr(svc, "_TTS_REQUEST_TIMEOUT_S", 0.05)
    monkeypatch.setattr(svc, "_TTS_CHUNK_DEADLINE_S", 0.5)

    with pytest.raises((RuntimeError, TimeoutError)):
        await svc._generate_tts_chunk_atomic(
            "Lời dịch", output_path=output, identity="new-identity",
            runtime=_runtime(), voice="fixture",
            profile=LocalizationProfile(id="qa-c4d", name="qa"), speaker_id=None,
            target_language="vi", attempts=1)
    gate.set()

    assert output.read_bytes() == before
    assert sidecar.read_text(encoding="utf-8") == before_identity
    await svc._sweep_late_artifacts()
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "chunk_0002.wav", "chunk_0002.wav.input.json"], (
        f"orphan artifacts: {[p.name for p in tmp_path.iterdir()]}")


@pytest.mark.asyncio
async def test_c4_orphan_writer_artifact_is_swept_after_writer_completes(
    tmp_path, monkeypatch,
):
    """The abandoned attempt's own file is reaped once the writer thread that
    owns it finally finishes - without the failing call sleeping on a guess."""
    gate = threading_event()

    async def cancel():
        return True

    async def blocked_writer(text, *, output_path, **kwargs):
        def _sync():
            gate.wait(5)
            _wav(Path(output_path), 900)

        await asyncio.to_thread(_sync)
        return Path(output_path)

    monkeypatch.setattr("backend.services.tts_service.generate_tts", blocked_writer)
    output = tmp_path / "chunk_0003.wav"
    with pytest.raises(JobCancelledError):
        await svc._generate_tts_chunk_atomic(
            "Lời dịch", output_path=output, identity="orphan",
            runtime=_runtime(), voice="fixture",
            profile=LocalizationProfile(id="qa-c4e", name="qa"), speaker_id=None,
            target_language="vi", attempts=1, cancel_check=cancel)

    gate.set()  # the late writer now recreates the file it was cancelled writing
    for _ in range(200):
        if any(p.suffix == ".wav" and p != output for p in tmp_path.iterdir()):
            break
        await asyncio.sleep(0.01)
    await svc._sweep_late_artifacts()
    assert not output.exists()
    assert list(tmp_path.iterdir()) == [], (
        f"orphan attempt files left behind: {[p.name for p in tmp_path.iterdir()]}")


@pytest.mark.asyncio
async def test_c4_openai_cancelled_thread_fences_its_own_publish(tmp_path, monkeypatch):
    """openai_tts_service: the sync SDK thread checks the fence AFTER streaming
    and, when the await was abandoned, deletes its private part file itself
    instead of publishing it. No polling, no leaked part, no published audio."""
    import backend.services.openai_tts_service as oai

    release = threading_event()
    started = threading_event()
    wrote = threading_event()
    target = tmp_path / "tts.mp3"

    class _StubResponse:
        def stream_to_file(self, path):
            started.set()
            release.wait(5)
            Path(path).write_bytes(b"late-audio")
            wrote.set()

    class _StubSpeech:
        def create(self, **kwargs):
            return _StubResponse()

    class _StubClient:
        def __init__(self, **kwargs):
            self.audio = type("A", (), {"speech": _StubSpeech()})()

    class _StubOpenAI:
        OpenAI = _StubClient

    monkeypatch.setitem(__import__("sys").modules, "openai", _StubOpenAI)
    task = asyncio.ensure_future(
        oai.generate_voice("Xin chào", voice_id="alloy", api_key="stub-key",
                           output_path=target))
    while not started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    # Producer-side fence: the thread removes its own part file once it sees it
    # was abandoned. We wait for the write to actually land first, so the
    # assertion observes the post-write state rather than a pre-write one.
    assert wrote.wait(5)
    for _ in range(200):
        if not list(tmp_path.iterdir()):
            break
        await asyncio.sleep(0.01)
    assert not target.exists(), "cancelled generation must not publish audio"
    assert list(tmp_path.iterdir()) == [], (
        f"producer left artifacts: {[p.name for p in tmp_path.iterdir()]}")


@pytest.mark.asyncio
async def test_c4_openai_part_path_is_unique_per_call(tmp_path, monkeypatch):
    """Two concurrent generations must not share one static .part path."""
    import backend.services.openai_tts_service as oai

    seen = []

    class _StubResponse:
        def stream_to_file(self, path):
            seen.append(path)
            Path(path).write_bytes(b"audio")

    class _StubSpeech:
        def create(self, **kwargs):
            return _StubResponse()

    class _StubClient:
        def __init__(self, **kwargs):
            self.audio = type("A", (), {"speech": _StubSpeech()})()

    class _StubOpenAI:
        OpenAI = _StubClient

    monkeypatch.setitem(__import__("sys").modules, "openai", _StubOpenAI)
    await asyncio.gather(
        oai.generate_voice("một", voice_id="alloy", api_key="k",
                           output_path=tmp_path / "a.mp3"),
        oai.generate_voice("hai", voice_id="alloy", api_key="k",
                           output_path=tmp_path / "b.mp3"),
    )
    assert len(seen) == 2 and seen[0] != seen[1], f"shared part path: {seen}"
    assert (tmp_path / "a.mp3").read_bytes() == b"audio"
    assert (tmp_path / "b.mp3").read_bytes() == b"audio"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.mp3", "b.mp3"]


@pytest.mark.asyncio
async def test_c4_openai_normal_generation_publishes_and_cleans(tmp_path, monkeypatch):
    """POSITIVE CONTROL: the unfenced normal path still publishes real bytes,
    persists no part file, and returns the published path."""
    import backend.services.openai_tts_service as oai

    class _StubResponse:
        def stream_to_file(self, path):
            Path(path).write_bytes(b"good-audio")

    class _StubSpeech:
        def create(self, **kwargs):
            return _StubResponse()

    class _StubClient:
        def __init__(self, **kwargs):
            self.audio = type("A", (), {"speech": _StubSpeech()})()

    class _StubOpenAI:
        OpenAI = _StubClient

    monkeypatch.setitem(__import__("sys").modules, "openai", _StubOpenAI)
    target = tmp_path / "tts.mp3"
    result = await oai.generate_voice("Xin chào", voice_id="nova", api_key="k",
                                      output_path=target, speed=1.1)
    assert result == target
    assert target.read_bytes() == b"good-audio"
    assert [p.name for p in tmp_path.iterdir()] == ["tts.mp3"]
