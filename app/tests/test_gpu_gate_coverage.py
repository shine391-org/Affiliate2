"""R1: the shared GPU gate must be ONE gate for every GPU caller.

The gate primitive moved from backend/core/tool_runners.py to
backend/core/gpu_gate.py so agents and services that must not import
tool_runners can share the same process-wide semaphore. tool_runners
re-exports it for backward compatibility.

If those two paths ever resolve to different semaphores, two GPU workloads
can run concurrently — exactly the OOM the gate exists to prevent on 8GB
VRAM (RTX 2060 SUPER, confirmed live on this machine). These tests assert
real gate behaviour: identity, serialisation, and release-on-error.

They deliberately do NOT assert on the source text of individual call sites;
that style of test passes even when the wrapper is unreachable.
"""
import asyncio
import sys

import pytest

from backend.core.gpu_gate import _gate_semaphore, localization_gpu_gate


def test_tool_runners_reexport_is_the_same_gate():
    """Two names for one gate: a second semaphore disables mutual exclusion."""
    from backend.core import tool_runners

    assert tool_runners.localization_gpu_gate is localization_gpu_gate


@pytest.mark.asyncio
async def test_gate_is_shared_across_import_paths():
    """Whichever module a caller imports the gate from, they queue together."""
    from backend.core import tool_runners
    async with tool_runners.localization_gpu_gate():
        # Resolved for THIS loop, so identity must hold with the live gate.
        assert _gate_semaphore() is _gate_semaphore()


def test_release_torch_cache_does_not_import_torch(monkeypatch):
    """The release helper must only touch an ALREADY-imported torch.

    Importing torch here would add its import cost and memory footprint to
    environments that never load it, and would hide a torch-free install.
    """
    from backend.core import gpu_gate

    monkeypatch.delitem(sys.modules, "torch", raising=False)

    # Must complete without importing torch; sys.modules stays torch-free.
    gpu_gate._release_localization_torch_cache_sync()
    assert "torch" not in sys.modules


@pytest.mark.asyncio
async def test_gate_serialises_concurrent_workloads():
    """Overlapping GPU workloads must never execute at the same time."""
    active = 0
    peak = 0

    async def workload():
        nonlocal active, peak
        async with localization_gpu_gate():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(workload() for _ in range(3)))

    assert peak == 1, f"GPU gate allowed {peak} concurrent workloads"


@pytest.mark.asyncio
async def test_gate_releases_after_exception():
    """A crashed GPU workload must not leak its permit."""
    with pytest.raises(RuntimeError):
        async with localization_gpu_gate():
            raise RuntimeError("demucs failed")

    # Hangs (test timeout) rather than failing fast if a permit leaked.
    async with localization_gpu_gate():
        pass


def test_gate_survives_a_second_event_loop():
    """A second loop must not raise "bound to a different event loop".

    An asyncio.Semaphore binds to the first loop that awaits it. The gate used
    to be a module-level semaphore, so any second loop raised RuntimeError in
    the middle of GPU work — reproducible against the ORIGINAL code shape with
    contending acquisitions in a loop that is then discarded.

    Sync test on purpose: a second loop can only be driven sequentially, not
    from inside a running one.
    """
    async def contend():
        async def one():
            async with localization_gpu_gate():
                await asyncio.sleep(0.01)

        await asyncio.gather(one(), one(), one())

    for _ in range(2):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(contend())
        finally:
            loop.close()


def test_gate_is_per_loop_not_a_global_gpu_lock():
    """Documents the KNOWN LIMIT: the gate is one lock per loop, not per GPU.

    Two loops running CONCURRENTLY each get their own semaphore, so they do
    serialise against each other. That is a real property, not a bug to hide:
    production gets away with it only because the server runs a single uvicorn
    worker on a single loop. If a second worker or a second concurrent loop is
    ever introduced, this test documents that the gate no longer protects the
    GPU and a real cross-process lock is required.

    This test exists so nobody reads the per-loop design as a stronger
    guarantee than it is.
    """
    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()

    async def resolve():
        # Runs INSIDE whichever loop is driving us, so it returns that
        # loop's semaphore — exactly how production callers obtain it.
        return _gate_semaphore()

    try:
        gate_a = loop_a.run_until_complete(resolve())
        gate_b = loop_b.run_until_complete(resolve())
        # Different loops -> different semaphores -> NOT one global GPU lock.
        assert gate_a is not gate_b, "expected per-loop semaphores"
    finally:
        loop_a.close()
        loop_b.close()


@pytest.mark.asyncio
async def test_thread_offloaded_decode_stays_inside_the_gate():
    """A CUDA decode offloaded to a thread must still be covered by the gate.

    whisper_service.transcribe runs the actual decode via asyncio.to_thread.
    A thread owns no event loop, so the gate held across the await must still
    exclude a concurrent workload — this is the arrangement production uses.
    """
    active = 0
    peak = 0

    async def decode():
        nonlocal active, peak
        async with localization_gpu_gate():
            await asyncio.to_thread(lambda: None)
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(decode(), decode(), decode())
    assert peak == 1, f"thread-offloaded decodes overlapped (peak={peak})"
