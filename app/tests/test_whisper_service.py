# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""WhisperService model resolution, HF cache location, and idle eviction.

The bug these pin: transcribe() used to call load() with NO quality argument,
so it always re-resolved to "balanced". A caller that had deliberately
pre-loaded a bigger model (the analyzer does, for the user's whisper_quality
setting) had it thrown away and reloaded as "small" — the user's choice was
silently ignored AND the load was paid for twice. Quality is explicit now.

No real model is ever loaded here: faster_whisper.WhisperModel is stubbed.
"""
import asyncio
import sys
import threading
import types

import pytest

from backend.services import whisper_service as ws_mod
from backend.services.whisper_service import WhisperService, WHISPER_QUALITY_MAP


class _FakeModel:
    """Stands in for faster_whisper.WhisperModel; records what was asked for."""

    def __init__(self, name, device=None, compute_type=None):
        self.name = name
        self.calls = []

    def transcribe(self, path, **kw):
        self.calls.append(kw)
        seg = types.SimpleNamespace(
            start=0.0, end=1.0, text=" hello ",
            words=[types.SimpleNamespace(word="hello", start=0.0, end=1.0, probability=0.9)],
        )
        info = types.SimpleNamespace(language="en", language_probability=0.99)
        return iter([seg]), info


@pytest.fixture(autouse=True)
def _stub_faster_whisper(monkeypatch):
    """Install a fake faster_whisper module and reset the singleton's state."""
    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = _FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    WhisperService._model = None
    WhisperService._loaded_quality = None
    WhisperService._loaded_model_name = None
    WhisperService._loaded_device = None
    WhisperService._evict_task = None
    yield
    WhisperService._model = None
    WhisperService._loaded_quality = None
    WhisperService._loaded_model_name = None
    WhisperService._loaded_device = None
    WhisperService._evict_task = None


@pytest.fixture(autouse=True)
def _always_has_audio(monkeypatch):
    async def _yes(_path):
        return True
    monkeypatch.setattr("backend.services.ffmpeg_service.has_audio_stream", _yes)


# ── quality resolution ─────────────────────────────────────────────────────

def test_load_maps_quality_to_model_name():
    assert WhisperService.load("best").name == WHISPER_QUALITY_MAP["best"]
    assert WhisperService.load("fast").name == WHISPER_QUALITY_MAP["fast"]


def test_transcribe_honours_explicit_quality(tmp_path):
    """The regression: a pre-loaded "best" model must not be swapped for "small".

    Mirrors the analyzer's shape — pre-load the user's quality, then transcribe.
    """
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")

    WhisperService.load("best")
    assert WhisperService._loaded_quality == "best"

    asyncio.run(ws_mod.whisper_service.transcribe(audio, quality="best"))
    # Still large-v3 — no silent downgrade, and no second load.
    assert WhisperService._loaded_quality == "best"
    assert WhisperService._model.name == WHISPER_QUALITY_MAP["best"]


def test_transcribe_defaults_to_balanced(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    asyncio.run(ws_mod.whisper_service.transcribe(audio))
    assert WhisperService._loaded_quality == "balanced"


def test_timing_only_selects_greedy_and_vad(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    asyncio.run(ws_mod.whisper_service.transcribe(audio, timing_only=True))
    kw = WhisperService._model.calls[-1]
    assert kw["beam_size"] == 1 and kw["vad_filter"] is True


def test_default_decode_stays_accurate(tmp_path):
    # Read-facing transcripts must keep beam search and no VAD gating.
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    asyncio.run(ws_mod.whisper_service.transcribe(audio))
    kw = WhisperService._model.calls[-1]
    assert kw["beam_size"] == 5 and kw["vad_filter"] is False


def test_transcribe_rejects_audioless_input(tmp_path, monkeypatch):
    async def _no(_path):
        return False
    monkeypatch.setattr("backend.services.ffmpeg_service.has_audio_stream", _no)
    audio = tmp_path / "silent.mp4"
    audio.write_bytes(b"x")
    with pytest.raises(ValueError):
        asyncio.run(ws_mod.whisper_service.transcribe(audio))


# ── HF cache location ──────────────────────────────────────────────────────

def test_hub_dir_honours_hf_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "custom"))
    assert WhisperService._hub_dir() == tmp_path / "custom" / "hub"


def test_hub_dir_falls_back_to_home(monkeypatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    assert WhisperService._hub_dir().parts[-3:] == (".cache", "huggingface", "hub")


def test_is_model_cached_reads_the_hf_home_tree(monkeypatch, tmp_path):
    """Reporting a present model as missing is what caused a second ~GB
    download, so this must follow HF_HOME rather than a hardcoded path."""
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert WhisperService.is_model_cached("balanced") is False
    (tmp_path / "hub" / f"models--Systran--faster-whisper-{WHISPER_QUALITY_MAP['balanced']}").mkdir(parents=True)
    assert WhisperService.is_model_cached("balanced") is True


def test_is_model_cached_false_while_still_downloading(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    blobs = (tmp_path / "hub"
             / f"models--Systran--faster-whisper-{WHISPER_QUALITY_MAP['balanced']}" / "blobs")
    blobs.mkdir(parents=True)
    (blobs / "part.incomplete").write_bytes(b"")
    assert WhisperService.is_model_cached("balanced") is False


# ── idle eviction ──────────────────────────────────────────────────────────

def test_unload_frees_a_resident_model():
    WhisperService.load("best")
    assert WhisperService.unload() is True
    assert WhisperService._model is None
    assert WhisperService.unload() is False  # idempotent


def test_evictor_drops_a_heavy_model_once_idle(monkeypatch):
    monkeypatch.setattr(ws_mod, "_IDLE_EVICT_SECONDS", 0)
    WhisperService.load("best")           # large-v3 → heavy
    asyncio.run(WhisperService._evict_when_idle())
    assert WhisperService._model is None


def test_evictor_leaves_light_models_resident(monkeypatch):
    monkeypatch.setattr(ws_mod, "_IDLE_EVICT_SECONDS", 0)
    WhisperService.load("fast")           # base → cheap to keep hot
    asyncio.run(WhisperService._evict_when_idle())
    assert WhisperService._model is not None


def test_arm_evictor_is_a_noop_without_a_running_loop():
    # Called from sync contexts too; must not raise there.
    WhisperService.load("best")
    WhisperService._arm_evictor()
    assert WhisperService._evict_task is None


def test_explicit_model_override_is_not_replaced_by_quality(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    asyncio.run(ws_mod.whisper_service.transcribe(
        audio, quality="best", model_name="custom-whisper-model"
    ))
    assert WhisperService._loaded_quality == "best"
    assert WhisperService._loaded_model_name == "custom-whisper-model"
    assert WhisperService._model.name == "custom-whisper-model"


def test_unknown_quality_fails_loudly_without_explicit_model():
    with pytest.raises(ValueError, match="Unknown Whisper quality"):
        WhisperService.load("magic")


def test_windows_nvidia_dll_directories_are_registered_and_handles_are_kept(
    monkeypatch, tmp_path,
):
    bin_dir = tmp_path / "nvidia" / "cublas" / "bin"
    bin_dir.mkdir(parents=True)
    dll = bin_dir / "cublas64_12.dll"
    dll.write_bytes(b"dll")

    class FakeDistribution:
        files = [type("F", (), {"__str__": lambda self: "nvidia/cublas/bin/cublas64_12.dll"})()]

        @staticmethod
        def locate_file(_file):
            return dll

    handles = []
    loaded = []
    monkeypatch.setattr(ws_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        ws_mod.metadata,
        "distribution",
        lambda name: FakeDistribution() if name == "nvidia-cublas-cu12" else (_ for _ in ()).throw(ws_mod.metadata.PackageNotFoundError),
    )
    monkeypatch.setattr(ws_mod.os, "add_dll_directory", lambda path: handles.append(path) or object())
    monkeypatch.setattr(ws_mod.ctypes, "WinDLL", lambda path: loaded.append(path) or object())
    monkeypatch.setattr(ws_mod, "_NVIDIA_DLL_HANDLES", [])
    monkeypatch.setattr(ws_mod, "_NVIDIA_DLL_LIBRARIES", {})
    monkeypatch.setattr(ws_mod, "_NVIDIA_DLL_DIRECTORIES", set())

    registered = ws_mod.register_nvidia_dll_directories()

    assert registered == [bin_dir]
    assert handles == [str(bin_dir)]
    assert loaded == [str(dll)]
    assert len(ws_mod._NVIDIA_DLL_HANDLES) == 1
    assert len(ws_mod._NVIDIA_DLL_LIBRARIES) == 1


def test_cuda_constructor_non_runtime_error_is_not_swallowed(monkeypatch):
    constructed = []

    class BrokenModel:
        def __init__(self, *_args, **kwargs):
            constructed.append(kwargs.get("device"))
            raise ValueError("unsupported GPU language option")

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = BrokenModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(WhisperService, "_select_device", classmethod(lambda cls: ("cuda", "float16")))

    with pytest.raises(ValueError, match="unsupported GPU language option"):
        WhisperService.load("fast")

    assert constructed == ["cuda"]


def test_cuda_compute_failure_retries_once_on_cpu_and_replaces_singleton(monkeypatch, tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"real-path-placeholder")
    constructed = []

    class LazyCudaFailureModel:
        def __init__(self, name, device=None, compute_type=None):
            self.name = name
            self.device = device
            self.compute_type = compute_type
            constructed.append((device, compute_type))

        def transcribe(self, _path, **_kwargs):
            info = types.SimpleNamespace(language="en", language_probability=0.99)
            if self.device == "cuda":
                def fail_during_iteration():
                    WhisperService._last_used = 1.0
                    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                    yield  # pragma: no cover
                return fail_during_iteration(), info
            seg = types.SimpleNamespace(
                start=0.0, end=1.0, text=" cpu success ",
                words=[types.SimpleNamespace(word="cpu", start=0.0, end=1.0, probability=0.9)],
            )
            return iter([seg]), info

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = LazyCudaFailureModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(WhisperService, "_select_device", classmethod(lambda cls: ("cuda", "float16")))
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *_args, **_kwargs: 1.0)

    result = asyncio.run(ws_mod.whisper_service.transcribe(audio, quality="fast"))

    assert result["text"] == "cpu success"
    assert constructed == [("cuda", "float16"), ("cpu", "int8")]
    assert WhisperService._loaded_device == "cpu"
    assert WhisperService._model.device == "cpu"
    assert WhisperService._last_used > 1.0


def test_concurrent_cuda_failures_both_fall_back_and_reuse_one_cpu_singleton(monkeypatch, tmp_path):
    """Two short transcriptions must not race on the CUDA→CPU fallback.

    `_loaded_device` is class-level mutable state. Job A's lazy CUDA failure
    replaces the singleton with CPU, so a job B that fails afterwards would
    read "cpu" from the global and re-raise — losing a job that a serial
    scheduler would have completed. Both must succeed with ONE cpu model.
    """
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"real-path-placeholder")
    constructed = []

    both_iterating = threading.Barrier(2, timeout=10)
    cpu_built = threading.Event()
    role_lock = threading.Lock()
    roles = []

    class RacingCudaModel:
        def __init__(self, name, device=None, compute_type=None):
            self.name = name
            self.device = device
            self.compute_type = compute_type
            constructed.append((device, compute_type))
            if device == "cpu":
                cpu_built.set()

        def transcribe(self, _path, **_kwargs):
            info = types.SimpleNamespace(language="en", language_probability=0.99)
            if self.device == "cuda":
                def fail_during_iteration():
                    # Both jobs hold the SAME cuda model and reach iteration.
                    both_iterating.wait()
                    with role_lock:
                        roles.append(1)
                        first = len(roles) == 1
                    if first:
                        # Job A fails first and swaps the singleton for CPU.
                        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                    # Job B only fails once A's replacement is visible, which is
                    # exactly the window where the old global check went wrong.
                    assert cpu_built.wait(10), "job A never replaced the singleton with CPU"
                    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                    yield  # pragma: no cover
                return fail_during_iteration(), info
            seg = types.SimpleNamespace(
                start=0.0, end=1.0, text=" cpu success ",
                words=[types.SimpleNamespace(word="cpu", start=0.0, end=1.0, probability=0.9)],
            )
            return iter([seg]), info

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = RacingCudaModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(WhisperService, "_select_device", classmethod(lambda cls: ("cuda", "float16")))
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *_args, **_kwargs: 1.0)

    results, errors = {}, {}

    def _run(tag):
        try:
            results[tag] = asyncio.run(ws_mod.whisper_service.transcribe(audio, quality="fast"))
        except BaseException as exc:  # noqa: BLE001 — reported below
            errors[tag] = exc

    threads = [threading.Thread(target=_run, args=(tag,)) for tag in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
        assert not thread.is_alive(), "transcription thread deadlocked"

    assert errors == {}, f"CUDA exception leaked: {errors!r}"
    assert set(results) == {"A", "B"}
    assert results["A"]["text"] == "cpu success"
    assert results["B"]["text"] == "cpu success"
    # One cuda model, ONE cpu replacement — the second job reuses it.
    assert constructed == [("cuda", "float16"), ("cpu", "int8")]
    assert WhisperService._loaded_device == "cpu"
    assert WhisperService._model.device == "cpu"


def test_non_cuda_compute_failure_is_not_retried(monkeypatch, tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"real-path-placeholder")
    constructed = []

    class BadAudioModel:
        def __init__(self, name, device=None, compute_type=None):
            self.name = name
            self.device = device
            constructed.append(device)

        def transcribe(self, _path, **_kwargs):
            info = types.SimpleNamespace(language="en", language_probability=0.99)
            def fail_during_iteration():
                raise ValueError("invalid audio frame")
                yield  # pragma: no cover
            return fail_during_iteration(), info

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = BadAudioModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(WhisperService, "_select_device", classmethod(lambda cls: ("cuda", "float16")))
    monkeypatch.setattr("backend.services.video_utils.probe_duration", lambda *_args, **_kwargs: 1.0)

    with pytest.raises(ValueError, match="invalid audio frame"):
        asyncio.run(ws_mod.whisper_service.transcribe(audio, quality="fast"))

    assert constructed == ["cuda"]
    assert WhisperService._loaded_device == "cuda"
