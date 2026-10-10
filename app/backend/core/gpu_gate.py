# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Shared GPU concurrency gate for heavy local workloads.

Lives in its own module (not in tool_runners.py) so that services and agents
which must not import tool_runners — notably analyzer.py, generator.py and
clip_extractor.py — can share the same process-wide semaphore without
creating an import cycle. tool_runners re-exports these names for backward
compatibility; both import paths resolve to the SAME semaphore instance, so
every GPU workload in the process serialises through exactly one gate.

The gate serialises GPU-heavy work: Demucs separation, Faster-Whisper /
WhisperX transcription, and NVENC render. Concurrency = 1, sized for 8GB VRAM.

This module owns only the primitive. It deliberately does NOT release the
torch allocator cache on exit — callers that want that release do it
explicitly while still holding the gate, so the release happens at the stage
boundary that actually needs the memory back.

The semaphore is created per event loop, not once at import. An
``asyncio.Semaphore`` binds itself to the first loop that awaits it and
raises "bound to a different event loop" for any later loop. Production runs
one loop for the whole process, so a module-level semaphore happened to work;
but any second loop (a test harness, an embedded runner, a future worker)
turned that into a RuntimeError in the middle of a GPU job. Per-loop
semaphores keep the invariant "one GPU workload at a time" intact within a
loop while removing the cross-loop crash. Semaphores are keyed by the loop
and dropped when it closes, so a restarting loop does not inherit a stale
gate that another loop could still be holding.

SCOPE — THIS GATE IS PROCESS-LOCAL AND SINGLE-LOOP, NOT A GLOBAL GPU LOCK.

It serialises GPU work only within one event loop of one process. It does NOT
protect the GPU across:

  * multiple OS processes (e.g. `uvicorn --workers N`), or
  * multiple event loops running concurrently inside one process.

Both of those would give each its own semaphore and could run two GPU
workloads at once. That is acceptable today because the shipped server runs
`uvicorn backend.main:app` with no `--workers` and no `--reload`, so exactly
one loop exists, and no product code calls `asyncio.run()` or
`new_event_loop()` — every GPU workload is awaited on that single loop, with
the CUDA decode itself offloaded to a plain thread via `asyncio.to_thread`
(a thread owns no loop, so it stays covered by the gate held across the
await). IF THE DEPLOYMENT EVER GAINS A SECOND WORKER OR A SECOND CONCURRENT
LOOP, THIS GATE STOPS BEING A GPU LOCK AND MUST BE REPLACED WITH A REAL
CROSS-PROCESS ONE (e.g. a file lock or a named semaphore).
"""
import asyncio
import gc
import logging
import sys
import weakref
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# GPU concurrency gate (concurrency = 1) for heavy local workloads
# (Demucs separation, Faster-Whisper / WhisperX, and NVENC render) on 8GB VRAM
_LOCALIZATION_GPU_GATES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def _gate_semaphore() -> asyncio.Semaphore:
    """Return the gate semaphore for the running loop, creating it on first use.

    Tests and diagnostics use this to reach the LIVE gate for the current
    loop; production callers should use `localization_gpu_gate()` so the
    semaphore is always resolved for the loop actually running the workload.
    """
    loop = asyncio.get_running_loop()
    sem = _LOCALIZATION_GPU_GATES.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(1)
        _LOCALIZATION_GPU_GATES[loop] = sem
    return sem


@asynccontextmanager
async def localization_gpu_gate():
    sem = _gate_semaphore()
    await sem.acquire()
    try:
        yield
    finally:
        sem.release()


def _release_localization_torch_cache_sync() -> None:
    """Release unused PyTorch/Demucs allocator memory after GPU audio work.

    This runs while the localization GPU gate is still held, after Demucs and
    alignment/diarization have finished. It never frees live tensors; CUDA's
    empty_cache() only returns UNUSED allocator blocks. Keeping those blocks
    warm beyond the ASR/alignment boundary made failed jobs pin memory for no
    useful downstream stage.
    """
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is None:
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        logger.debug("Could not release localization torch cache", exc_info=True)
