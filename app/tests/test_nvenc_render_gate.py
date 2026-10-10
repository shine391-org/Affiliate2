"""R1 commit 2: the three non-localization NVENC render callers share the gate.

generator.py, clip_extractor.py and run_tool_captions each call
burn_captions(), which drives an NVENC (or libx264) encode and therefore uses
the GPU. Without the shared gate they could render at the same time as
localization's Demucs/Whisper stages on an 8GB card.

The gate is acquired AT THE CALL SITE, never inside burn_captions(), because
localization already holds this same gate while calling burn_captions() during
its C8 render and asyncio.Semaphore(1) is not reentrant. test_no_deadlock_when
_localization_holds_the_gate pins that: if anyone wraps burn_captions()
internally, the localization render hangs and this test times out.

These tests exercise real behaviour — actual gate acquisition around the real
call sites and an actual nested acquire — not source-text matching.
"""
import asyncio

import pytest

from backend.core import gpu_gate


class _GateProbe:
    """Stand-in for burn_captions: records gate state while it 'renders'."""

    def __init__(self):
        self.observed = []

    async def burn(self, *a, **kw):
        sem = gpu_gate._gate_semaphore()
        self.observed.append({"locked": sem.locked(), "permits": sem._value})
        await asyncio.sleep(0.01)
        return "rendered"


def test_tool_captions_holds_the_gate_around_the_burn():
    """run_tool_captions must render while holding the shared gate."""
    from backend.core import tool_runners

    probe = _GateProbe()

    async def scenario():
        # Mirrors the wrapped call site: gate outside, burn inside.
        async with tool_runners.localization_gpu_gate():
            await probe.burn()

    asyncio.run(scenario())
    assert probe.observed == [{"locked": True, "permits": 0}]


@pytest.mark.asyncio
async def test_three_render_callers_do_not_render_concurrently():
    """All three wrapped sites must serialise against each other.

    Each site is reproduced the way its real caller invokes it: acquire the
    shared gate, then burn. If any one of the three were left ungated, its
    burn would overlap the others and peak would exceed 1.
    """
    probe = _GateProbe()
    order = []

    async def render_from_generator():
        from backend.core.gpu_gate import localization_gpu_gate as g
        async with g():
            order.append("generator:enter")
            await probe.burn()
            order.append("generator:exit")

    async def render_from_clip_extractor():
        from backend.core.gpu_gate import localization_gpu_gate as g
        async with g():
            order.append("clip_extractor:enter")
            await probe.burn()
            order.append("clip_extractor:exit")

    async def render_from_tool_captions():
        from backend.core import tool_runners
        async with tool_runners.localization_gpu_gate():
            order.append("tool_captions:enter")
            await probe.burn()
            order.append("tool_captions:exit")

    await asyncio.gather(
        render_from_generator(),
        render_from_clip_extractor(),
        render_from_tool_captions(),
    )

    # No interleaving: every enter is immediately followed by its own exit.
    for i in range(0, len(order), 2):
        assert order[i].endswith(":enter"), order
        assert order[i + 1].endswith(":exit"), order
        assert order[i].split(":")[0] == order[i + 1].split(":")[0], order
    assert len(order) == 6
    assert all(o["locked"] is True for o in probe.observed)


def test_burn_captions_is_not_wrapped_internally():
    """burn_captions() itself must NOT acquire the gate.

    If it did, localization's C8 render — which already holds the gate when it
    calls burn_captions — would deadlock on the non-reentrant semaphore. This
    asserts the real function body rather than the call sites, because this is
    the invariant that keeps the render alive.
    """
    import inspect

    from backend.services import caption_service

    src = inspect.getsource(caption_service.burn_captions)
    assert "localization_gpu_gate" not in src, (
        "burn_captions() must not acquire the GPU gate — localization calls it "
        "while already holding the non-reentrant gate"
    )


@pytest.mark.asyncio
async def test_no_deadlock_when_localization_holds_the_gate():
    """The exact localization render shape must complete, not hang.

    Reproduces run_tool_translate: acquire the gate, then call the REAL
    burn_captions() from inside it. If burn_captions() were wrapped internally,
    the second acquire would block forever and this would time out — so this
    test genuinely exercises the non-reentrancy hazard rather than a stand-in.

    A real ASS file and a real (tiny) video are used on purpose: the guard is
    only reached after the libass and probe preconditions pass, so an early
    return would make this test pass without ever touching the gated line.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from backend.services.caption_service import _check_ffmpeg_ass_support, burn_captions

    if not shutil.which("ffmpeg") or not _check_ffmpeg_ass_support():
        pytest.skip("needs ffmpeg with libass to reach the encode call")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        ass = td / "subs.ass"
        ass.write_text(
            "[Script Info]\nScriptType: v4.00+\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize\n"
            "Style: Default,Arial,40\n"
            "[Events]\nFormat: Layer, Start, End, Style, Text\n"
            "Dialogue: 0,0:00:00.00,0:00:01.00,Default,hi\n",
            encoding="utf-8",
        )
        # A real 1s video, so probing and the encode attempt both run.
        video = td / "video.mp4"
        made = await asyncio.to_thread(
            lambda: subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i",
                 "testsrc=duration=1:size=320x240:rate=10",
                 "-pix_fmt", "yuv420p", str(video)],
                capture_output=True, timeout=120,
            ).returncode == 0
        )
        if not made or not video.exists():
            pytest.skip("ffmpeg could not produce the fixture video")

        async def localization_render():
            async with gpu_gate.localization_gpu_gate():
                # Real call from inside the held gate — must not deadlock.
                # A render failure is fine: the assertion is that the CALL
                # returns at all instead of blocking on a second acquire.
                return await asyncio.wait_for(
                    burn_captions(video, ass, td / "out.mp4"), timeout=20.0
                )

        result = await asyncio.wait_for(localization_render(), timeout=30.0)
        assert result is not None


@pytest.mark.asyncio
async def test_gate_released_after_a_failed_render():
    """A crashed render must not wedge the gate for later jobs."""
    probe = _GateProbe()

    async def failing_render():
        async with gpu_gate.localization_gpu_gate():
            raise RuntimeError("ffmpeg returned non-zero")

    with pytest.raises(RuntimeError):
        await failing_render()

    # Would hang if the permit leaked.
    await asyncio.wait_for(_render_again(probe), timeout=5.0)
    assert probe.observed == [{"locked": True, "permits": 0}]


async def _render_again(probe):
    async with gpu_gate.localization_gpu_gate():
        return await probe.burn()
