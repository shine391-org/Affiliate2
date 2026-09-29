# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Singleton faster-whisper transcription service."""
import asyncio
import ctypes
from importlib import metadata
import logging
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_NVIDIA_RUNTIME_PACKAGES = (
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cuda-nvrtc-cu12",
)
_NVIDIA_DLL_HANDLES: list[object] = []
_NVIDIA_DLL_LIBRARIES: dict[Path, object] = {}
_NVIDIA_DLL_DIRECTORIES: set[Path] = set()
_PRELOAD_NVIDIA_DLLS = {
    "cublas64_12.dll",
    "cublaslt64_12.dll",
    "cudnn64_9.dll",
}


def register_nvidia_dll_directories() -> list[Path]:
    """Make NVIDIA wheel DLLs discoverable without copying them into CTranslate2.

    Python 3.8+ no longer searches arbitrary PATH entries for extension-module
    dependencies on Windows. NVIDIA's runtime wheels install their DLLs below
    ``site-packages/nvidia/*/bin``, so register those exact directories and keep
    the returned handles alive for the process lifetime.
    """
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return []

    candidates: set[Path] = set()
    preload_paths: set[Path] = set()
    for env_name in ("CUDA_PATH", "CUDA_HOME"):
        root = os.environ.get(env_name)
        if root:
            candidates.add(Path(root) / "bin")

    for package_name in _NVIDIA_RUNTIME_PACKAGES:
        try:
            distribution = metadata.distribution(package_name)
        except metadata.PackageNotFoundError:
            continue
        for entry in distribution.files or ():
            if not str(entry).lower().endswith(".dll"):
                continue
            dll_path = Path(distribution.locate_file(entry)).resolve()
            candidates.add(dll_path.parent)
            if dll_path.name.lower() in _PRELOAD_NVIDIA_DLLS:
                preload_paths.add(dll_path)

    registered: list[Path] = []
    for directory in sorted(candidates, key=lambda path: str(path).lower()):
        directory = directory.resolve()
        if not directory.is_dir() or directory in _NVIDIA_DLL_DIRECTORIES:
            continue
        try:
            handle = os.add_dll_directory(str(directory))
        except OSError as exc:
            logger.warning("Could not register CUDA DLL directory %s: %s", directory, exc)
            continue
        _NVIDIA_DLL_HANDLES.append(handle)
        _NVIDIA_DLL_DIRECTORIES.add(directory)
        registered.append(directory)
        logger.info("CUDA DLL directory registered: %s", directory)

    # CTranslate2 resolves cuBLAS lazily with LoadLibrary during the first
    # compute. On Windows that call does not consistently consult Python's DLL
    # directory cookies, so preload the small set of root runtime libraries by
    # absolute path. Their dependency DLLs are then resolved through the
    # registered directories above. Keep handles alive just like directory
    # cookies; no binaries are copied into another package.
    for dll_path in sorted(preload_paths, key=lambda path: path.name.lower()):
        if dll_path in _NVIDIA_DLL_LIBRARIES:
            continue
        try:
            _NVIDIA_DLL_LIBRARIES[dll_path] = ctypes.WinDLL(str(dll_path))
        except OSError as exc:
            logger.warning("Could not preload CUDA runtime library %s: %s", dll_path, exc)
    return registered


def _is_cuda_runtime_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    # Require a CUDA runtime/library signal. Generic words such as "gpu" or
    # "out of memory" can also describe invalid user/model configuration or a
    # process-wide allocation failure and must not silently change devices.
    return any(marker in message for marker in (
        "cuda",
        "cublas",
        "cudnn",
        "cudart",
        "cufft",
        "curand",
        "nvrtc",
    ))


register_nvidia_dll_directories()

WHISPER_QUALITY_MAP = {
    "fast":     "base",
    "balanced": "small",
    "accurate": "medium",
    "best":     "large-v3",
}

# Approximate download sizes for first-time use
WHISPER_MODEL_SIZES = {
    "base":     "~150 MB",
    "small":    "~500 MB",
    "medium":   "~1.5 GB",
    "large-v3": "~3 GB",
}


# Desktop resource policy: every Whisper model is evicted after a short idle
# grace period. Even "small" models keep sizeable native CTranslate2/CUDA
# allocations resident, and a failed/finished localization job must not leave
# multi-GB RSS pinned for the lifetime of the backend. A short grace still lets
# back-to-back jobs reuse the model without paying a reload for every clip.
_IDLE_EVICT_SECONDS = 45

# Min seconds between on_progress emissions during a streaming transcription.
# Module-level so tests can shrink it instead of monkeypatching time.monotonic
# (which asyncio's event loop also uses — patching it breaks loop timing).
_PROGRESS_EMIT_INTERVAL = 3.0

# Only transcriptions of audio AT LEAST this long take the serialization gate.
# The gate exists to stop two BATCH transcriptions (analyze + clip extraction
# on 30-minute-plus audio) from thrashing the machine. Short interactive
# transcriptions must NEVER wait on it — Smart Video and the caption tools
# align captions on ~1-minute TTS output mid-pipeline, and head-of-line
# blocking those behind a 30-minute batch job would stall them for tens of
# minutes. Short jobs run concurrently exactly as before the gate existed:
# they interleave inside ctranslate2 at generation-window granularity and
# finish fast. Unknown duration (ffprobe failed) counts as short — the safe
# default is the old concurrent behaviour.
_SERIALIZE_MIN_SECONDS = 120.0


def resolve_whisper_model(quality: str = "balanced", model_name: str | None = None) -> str:
    """Resolve the exact model requested by a profile/caller."""
    explicit = (model_name or "").strip()
    if explicit:
        return explicit
    if quality not in WHISPER_QUALITY_MAP:
        raise ValueError(f"Unknown Whisper quality: {quality}")
    return WHISPER_QUALITY_MAP[quality]


class WhisperService:
    _model = None
    _loaded_quality = None
    _loaded_model_name = None
    _loaded_device = None
    _lock = threading.Lock()
    _last_used = 0.0
    _evict_task = None
    _active_transcriptions = 0
    # Serializes the transcription COMPUTE (not the model load) for long
    # audio. The model is created with ctranslate2's default num_workers=1,
    # so two concurrent transcribe() calls never truly ran in parallel
    # anyway — they queued invisibly inside the library while BOTH calling
    # threads burned CPU on audio decode + word-timestamp assembly,
    # saturating the machine. An explicit gate makes the queueing observable
    # and keeps the waiting job's decode work from starting until it can
    # actually run.
    _transcribe_gate = threading.Lock()

    @classmethod
    def _hub_dir(cls) -> Path:
        """Resolve the HuggingFace hub cache directory.

        MUST honour ``HF_HOME``: it's the standard way to relocate the model
        cache, and anyone self-hosting on a machine where ``$HOME`` is small or
        ephemeral will have set it. Hardcoding ``~/.cache/huggingface`` made
        ``is_model_cached()`` read a tree that faster-whisper never writes to in
        that setup, so freshly-downloaded models were reported as missing and a
        second full copy got downloaded.
        """
        hf_home = os.environ.get("HF_HOME")
        base = Path(hf_home) if hf_home else Path.home() / ".cache" / "huggingface"
        return base / "hub"

    @classmethod
    def _select_device(cls) -> tuple[str, str]:
        """Detect whether CUDA is available for ctranslate2."""
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda", "float16"
        except Exception as e:
            logger.debug("Whisper CUDA detection failed: %s", e)
        return "cpu", "int8"

    @classmethod
    def is_model_cached(cls, quality: str = "balanced", model_name: str | None = None) -> bool:
        """Check if the exact Whisper model is already downloaded."""
        model_name = resolve_whisper_model(quality, model_name)
        model_dir = cls._hub_dir() / f"models--Systran--faster-whisper-{model_name}"
        if not model_dir.exists():
            return False
        # Check for .incomplete files — model is still downloading
        blobs = model_dir / "blobs"
        if blobs.exists():
            for f in blobs.iterdir():
                if f.suffix == ".incomplete":
                    return False
        return True

    @classmethod
    def load(cls, quality: str = "balanced", model_name: str | None = None):
        model, _device = cls.load_with_device(quality, model_name=model_name)
        return model

    @classmethod
    def load_with_device(cls, quality: str = "balanced", model_name: str | None = None):
        """Return ``(model, device)`` captured atomically under the singleton lock.

        The device has to travel WITH the model instance. `_loaded_device` is
        class-level mutable state: when a CUDA compute failure makes one job
        swap the singleton for a CPU model, every other in-flight caller that
        is still iterating the old CUDA model sees "cpu" if it asks the class
        later. Deciding the CUDA→CPU fallback from that global made a second
        concurrent failure re-raise instead of reusing the CPU singleton the
        first job had just built.
        """
        resolved_model = resolve_whisper_model(quality, model_name)
        with cls._lock:
            if cls._model is None or cls._loaded_model_name != resolved_model:
                register_nvidia_dll_directories()
                from faster_whisper import WhisperModel
                device, compute_type = cls._select_device()
                logger.info("Loading Whisper model: %s (quality=%s, device=%s, compute=%s)",
                            resolved_model, quality, device, compute_type)
                try:
                    cls._model = WhisperModel(
                        resolved_model,
                        device=device,
                        compute_type=compute_type,
                    )
                    cls._loaded_device = device
                except Exception as exc:
                    if device != "cpu" and _is_cuda_runtime_error(exc):
                        logger.warning(
                            "Failed to initialize Whisper on %s (%s): %s. Falling back to CPU int8.",
                            device, compute_type, exc,
                        )
                        cls._model = WhisperModel(
                            resolved_model,
                            device="cpu",
                            compute_type="int8",
                        )
                        cls._loaded_device = "cpu"
                    else:
                        raise
                cls._loaded_quality = quality
                cls._loaded_model_name = resolved_model
                logger.info("Whisper model '%s' loaded successfully on %s", resolved_model, cls._loaded_device)
            cls._last_used = time.monotonic()
            return cls._model, cls._loaded_device

    @classmethod
    def _replace_failed_cuda_model(cls, failed_model, resolved_model: str, quality: str, exc: BaseException):
        """Drop a broken CUDA singleton and replace it with CPU/int8 once."""
        with cls._lock:
            if (
                cls._model is not failed_model
                and cls._model is not None
                and cls._loaded_model_name == resolved_model
                and cls._loaded_device == "cpu"
            ):
                return cls._model

            logger.warning(
                "Whisper CUDA compute failed for '%s': %s. Retrying once on CPU int8.",
                resolved_model,
                exc,
            )
            cls._model = None
            cls._loaded_device = None
            from faster_whisper import WhisperModel
            cls._model = WhisperModel(
                resolved_model,
                device="cpu",
                compute_type="int8",
            )
            cls._loaded_quality = quality
            cls._loaded_model_name = resolved_model
            cls._loaded_device = "cpu"
            return cls._model

    @classmethod
    def unload(cls) -> bool:
        """Drop the resident model when no transcription is using it."""
        with cls._lock:
            if cls._model is None or cls._active_transcriptions > 0:
                return False
            freed = cls._loaded_model_name or cls._loaded_quality
            cls._model = None
            cls._loaded_quality = None
            cls._loaded_model_name = None
            cls._loaded_device = None
        import gc
        gc.collect()
        # Do not import torch just to clean Whisper. If Demucs/WhisperX already
        # loaded it in this process, release only UNUSED CUDA allocator blocks.
        # Active tensors remain valid.
        torch = sys.modules.get("torch")
        try:
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        logger.info("Whisper model '%s' evicted after %.0fs idle", freed, _IDLE_EVICT_SECONDS)
        return True

    @classmethod
    async def _evict_when_idle(cls):
        """Free the resident model after a short period with no active users."""
        try:
            while True:
                with cls._lock:
                    model_name = cls._loaded_model_name
                    last = cls._last_used
                    active = cls._active_transcriptions
                if model_name is None:
                    return
                if active > 0:
                    # A previous job's sweeper may still be alive while the next
                    # transcription reuses the singleton. Never evict underneath
                    # an active caller; re-check shortly instead.
                    await asyncio.sleep(1)
                    continue
                idle = time.monotonic() - last
                if idle >= _IDLE_EVICT_SECONDS:
                    cls.unload()
                    return
                await asyncio.sleep(min(15, max(1, _IDLE_EVICT_SECONDS - idle + 1)))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never let the sweeper kill anything
            logger.debug("Whisper idle-evict sweeper stopped: %s", e)

    @classmethod
    def _arm_evictor(cls):
        if cls._loaded_model_name is None:
            return
        if cls._evict_task is not None and not cls._evict_task.done():
            return
        try:
            cls._evict_task = asyncio.get_running_loop().create_task(cls._evict_when_idle())
        except RuntimeError:
            pass  # no running loop (sync context) — skip, not worth failing over

    async def transcribe(
        self,
        audio_path: str | Path,
        language: str = None,
        quality: str = "balanced",
        model_name: str | None = None,
        timing_only: bool = False,
        on_progress=None,
    ) -> dict:
        """
        Transcribe audio file. Returns:
        { "text": "...", "language": "en", "segments": [...] }

        `quality` is explicit on purpose. It used to be implicit — transcribe()
        called load() with no argument, which meant two things went wrong at
        once: a caller that had deliberately pre-loaded a bigger model (the
        analyzer does, for its `whisper_quality` setting) had it immediately
        thrown away and RE-loaded as "small", silently ignoring the user's
        choice and paying for the load twice; and conversely, whatever model an
        unrelated earlier job left resident could be reused by a later one.

        `timing_only=True` selects a greedy + VAD decode. Use it when the caller
        only needs word timings for caption alignment over clean, single-speaker
        audio (e.g. our own TTS output) rather than a publishable transcript —
        it is substantially faster on CPU for a negligible accuracy cost on that
        kind of input. Leave it False for anything the user will read.

        `on_progress(fraction)` (optional, sync, 0.0–0.99) fires from the
        worker thread as segments stream out of the decoder, throttled to one
        call every ~3 s. Callers bridging to async progress emitters should use
        `asyncio.run_coroutine_threadsafe` with a loop captured BEFORE the call
        (same pattern as yt-dlp's progress hook). Transcription used to consume
        the whole segment generator in one gulp, so a 40-minute audio showed a
        frozen progress bar for the entire 15–30 minute transcription — which
        is indistinguishable from a hung job.
        """
        # Pre-check: confirm the file actually has an audio stream before
        # invoking whisper. Without this, faster-whisper crashes with a
        # cryptic "tuple index out of range" when given a silent file
        # (e.g. video-only mp4 from a downloader that missed the audio
        # track).
        from backend.services.ffmpeg_service import has_audio_stream
        if not await has_audio_stream(Path(audio_path)):
            raise ValueError(f"No audio stream found in {audio_path}")

        resolved_model = resolve_whisper_model(quality, model_name)
        # `initial_device` is the device THIS invocation's model was loaded on.
        # It is captured together with the model, before any compute happens, so
        # the CUDA→CPU fallback below cannot be flipped by another job swapping
        # the class-level singleton to CPU mid-flight.
        model, initial_device = self.load_with_device(quality, model_name=model_name)

        # Duration serves two decisions: the progress fraction denominator AND
        # whether this transcription is long enough to take the batch gate.
        # ~50 ms of ffprobe next to a transcription that runs seconds to tens
        # of minutes.
        from backend.services.video_utils import probe_duration
        total_duration = await asyncio.to_thread(probe_duration, Path(audio_path))

        def _consume(active_model):
            segments_gen, info = active_model.transcribe(
                str(audio_path),
                beam_size=1 if timing_only else 5,
                vad_filter=timing_only,
                language=language,
                word_timestamps=True,
            )
            # Stream the generator instead of list()-ing it so progress can be
            # reported as the decode advances through the audio. Keeping this
            # inside the retry boundary is required because CTranslate2 may
            # defer its first CUDA compute until generator iteration.
            segments = []
            last_emit = 0.0
            best_fraction = 0.0
            for s in segments_gen:
                segments.append(s)
                if on_progress is not None and total_duration > 0:
                    now = time.monotonic()
                    if now - last_emit >= _PROGRESS_EMIT_INTERVAL:
                        fraction = min(0.99, float(s.end or 0.0) / total_duration)
                        if fraction > best_fraction:
                            best_fraction = fraction
                            last_emit = now
                            try:
                                on_progress(fraction)
                            except Exception:
                                pass  # progress UI must never break transcription
            text = " ".join([s.text.strip() for s in segments])
            return {
                "text": text,
                "language": info.language,
                "language_probability": info.language_probability,
                "segments": [
                    {
                        "start": s.start,
                        "end": s.end,
                        "text": s.text.strip(),
                        "words": [
                            {"word": w.word, "start": float(w.start), "end": float(w.end), "probability": float(w.probability)}
                            for w in (s.words or [])
                        ],
                    }
                    for s in segments
                ],
            }

        def _run():
            # Gate only BATCH-length audio (see _SERIALIZE_MIN_SECONDS —
            # short/interactive transcriptions must never queue here).
            gate = (type(self)._transcribe_gate
                    if total_duration >= _SERIALIZE_MIN_SECONDS else None)
            if gate is not None and not gate.acquire(blocking=False):
                logger.info(
                    "Whisper busy — %s (%.0fs) waits for the active batch "
                    "transcription to finish",
                    Path(audio_path).name, total_duration,
                )
                gate.acquire()
            try:
                try:
                    return _consume(model)
                except Exception as exc:
                    # Decide from the device this invocation STARTED on, not from
                    # the class-level singleton. A concurrent job that already
                    # replaced the CUDA model with CPU would otherwise make this
                    # handler re-raise instead of reusing that CPU singleton.
                    if initial_device != "cuda" or not _is_cuda_runtime_error(exc):
                        raise
                    cpu_model = type(self)._replace_failed_cuda_model(
                        model,
                        resolved_model,
                        quality,
                        exc,
                    )
                    return _consume(cpu_model)
            finally:
                if gate is not None:
                    gate.release()

        with type(self)._lock:
            type(self)._active_transcriptions += 1
        try:
            return await asyncio.to_thread(_run)
        finally:
            with type(self)._lock:
                type(self)._active_transcriptions = max(
                    0, type(self)._active_transcriptions - 1
                )
                type(self)._last_used = time.monotonic()
            type(self)._arm_evictor()


whisper_service = WhisperService()
