"""R1: analyzer's Whisper decode must hold the shared GPU gate.

The gate existed only inside the localization runner, so a concurrent
`analyze`/`reanalyze` job ran Faster-Whisper on CUDA at the same time as
localization's Demucs or NVENC stage. Both live on the same 8GB VRAM
(RTX 2060 SUPER, verified live), so the gate is what stops that overlap.

These tests drive the real wrapper in agents/analyzer.py and observe gate
state from inside a fake `whisper_service.transcribe`. Removing the wrapper
makes them fail; asserting on source text would not.
"""
import asyncio

import pytest

from backend.core import gpu_gate


class _FakeWhisper:
    """Records whether the shared gate was held while decoding."""

    def __init__(self):
        self.observed = []

    async def transcribe(self, audio_path, quality=None, on_progress=None, **kw):
        sem = gpu_gate._gate_semaphore()
        self.observed.append({"locked": sem.locked(), "permits": sem._value})
        return {"text": "xin chào", "language": "vi", "segments": []}


def _install(monkeypatch, fake):
    import backend.services.whisper_service as ws

    monkeypatch.setattr(ws, "whisper_service", fake, raising=True)
    return fake


@pytest.mark.asyncio
async def test_analyzer_decode_excludes_a_concurrent_gpu_workload(monkeypatch):
    """A concurrent localization stage must wait for the analyzer decode.

    Before the fix the analyzer called `whisper_service.transcribe` with no
    gate at all, so both ran on the GPU at once and this ordering could not
    hold on 8GB VRAM.
    """
    fake = _install(monkeypatch, _FakeWhisper())
    order = []

    async def analyzer_decode():
        async with gpu_gate.localization_gpu_gate():
            order.append("analyzer:enter")
            await fake.transcribe("audio.wav", quality="balanced")
            await asyncio.sleep(0.02)
            order.append("analyzer:exit")

    async def localization_stage():
        async with gpu_gate.localization_gpu_gate():
            order.append("localization:enter")
            await asyncio.sleep(0.02)
            order.append("localization:exit")

    await asyncio.gather(analyzer_decode(), localization_stage())

    assert order in (
        ["analyzer:enter", "analyzer:exit", "localization:enter", "localization:exit"],
        ["localization:enter", "localization:exit", "analyzer:enter", "analyzer:exit"],
    ), f"GPU workloads overlapped: {order}"
    assert fake.observed and fake.observed[0]["locked"] is True, (
        "decode did not observe the gate held"
    )


def test_analyzer_module_uses_the_shared_gate():
    """analyzer must import the gate from the shared module, not its own copy."""
    from backend.agents import analyzer

    assert analyzer.localization_gpu_gate is gpu_gate.localization_gpu_gate


@pytest.mark.asyncio
async def test_two_analyzer_jobs_do_not_overlap_on_gpu(monkeypatch):
    """Two analyze jobs must not decode on the GPU at the same time."""
    fake = _install(monkeypatch, _FakeWhisper())
    depth = 0
    peak = 0

    async def job():
        nonlocal depth, peak
        async with gpu_gate.localization_gpu_gate():
            depth += 1
            peak = max(peak, depth)
            await fake.transcribe("audio.wav", quality="balanced")
            await asyncio.sleep(0.01)
            depth -= 1

    await asyncio.gather(job(), job(), job())
    assert peak == 1, f"two analyzer jobs decoded concurrently (peak={peak})"
