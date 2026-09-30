# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""Async runners for the modular /api/tools utilities (OSS variant).

Each runner is a thin wrapper around an existing OSS service (ffmpeg /
whisper / caption / tts / sfx / thumbnail) and follows the pattern:

    try:
        _tool_progress(...) multiple times
        ... do work ...
        _tool_success(job_id, out_path, inputs_to_cleanup, user_id)
    except Exception as e:
        _tool_fail(job_id, e, inputs_to_cleanup, user_id)

OSS adaptation vs the SaaS variant:
  - No billing — every `require_balance` / `bill` call is removed. Users
    bring their own keys.
  - Text-AI tools (hook-analysis / translate / metadata / auto-chapters)
    route through the user's own key via backend.core.ai_provider.get_ai_client.
  - Voiceover + dubbing use the local (free) Edge TTS service.
  - ffmpeg / whisper work runs locally against the OSS services. A few
    helpers the SaaS variant pulled from cloud-only services (silence
    removal, watermark, audio enhance, hook analysis) are implemented
    self-contained here.
"""
import asyncio
import logging
import math
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Shared helpers ─────────────────────────────────────────────────────────

async def _probe_aspect_ratio(video_path: Path) -> str:
    """Return '9:16' / '16:9' / '1:1' for the file's real dimensions.

    Delegates to `video_utils.aspect_label` — the probe here used to be binary
    ("is it landscape?"), so a SQUARE video got 9:16 caption geometry: wrong
    margins and font size, and since the safe-zone floor it would also get the
    vertical chrome inset a 1:1 feed post doesn't need. Same repair
    clip_extractor already received. Defaults to 9:16 on probe failure.
    """
    from backend.services.video_utils import aspect_label
    return await asyncio.to_thread(aspect_label, video_path)


# 9:16 as a ratio, with a 2% tolerance so 1079x1920 or 1080x1918 still counts.
_VERTICAL_RATIO = 9 / 16


def _is_already_vertical_sync(video_path: Path) -> bool:
    """True only when the source is 9:16 or NARROWER — i.e. nothing to crop.

    The old test was "portrait" (w <= h), which made Reframe a no-op on square
    (1080x1080) and 4:5 (1080x1350) sources: they came back byte-identical with
    an "already vertical" notice, even though both have side content a 9:16
    crop would remove. Only a source at or below the 9:16 ratio is genuinely
    done. Unreadable dimensions fall back to False so the reframe still runs —
    re-encoding a video we can't measure is cheaper than silently doing nothing.
    """
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=10,
        )
        w, h = map(int, r.stdout.strip().split(",")[:2])
        if not w or not h:
            return False
        return (w / h) <= _VERTICAL_RATIO * 1.02
    except Exception:
        return False


async def _is_already_vertical(video_path: Path) -> bool:
    return await asyncio.to_thread(_is_already_vertical_sync, video_path)


def _localization_source_media_preflight_sync(source_path: Path) -> dict:
    """Reject a proven video-only source before loading Demucs/Whisper.

    Unknown/unprobeable metadata is left to the canonical stage error paths;
    only a successful ffprobe proving zero audio streams fails here.
    """
    from backend.services.video_utils import probe_media_details

    details = probe_media_details(source_path)
    if details and details.get("has_audio") is False:
        raise RuntimeError(
            "Source media has no audio stream. Localization requires audible "
            "speech/audio; choose or re-download a verified media variant with audio."
        )
    return details


# GPU concurrency gate (concurrency = 1) for heavy local localization workloads
# (Demucs separation, Faster-Whisper / WhisperX, and NVENC render) on 8GB VRAM
from contextlib import asynccontextmanager

_LOCALIZATION_GPU_GATE = asyncio.Semaphore(1)


@asynccontextmanager
async def localization_gpu_gate():
    await _LOCALIZATION_GPU_GATE.acquire()
    try:
        yield
    finally:
        _LOCALIZATION_GPU_GATE.release()


def _release_localization_torch_cache_sync() -> None:
    """Release unused PyTorch/Demucs allocator memory after C5/C8 GPU audio work.

    This runs while the localization GPU gate is still held, after Demucs and
    alignment/diarization have finished. It never frees live tensors; CUDA's
    empty_cache() only returns UNUSED allocator blocks. Keeping those blocks
    warm beyond the ASR/alignment boundary made failed jobs pin memory for no
    useful downstream stage.
    """
    import gc
    import sys

    gc.collect()
    torch = sys.modules.get("torch")
    if torch is None:
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        logger.debug("Could not release localization torch cache", exc_info=True)


def _cleanup_paths(*paths: Path):
    """Best-effort delete of scratch files. Never raises."""
    for p in paths:
        if not p:
            continue
        try:
            resolved = Path(p).resolve()
            from backend.config import settings
            storage_root = settings.STORAGE_ROOT.resolve()
            protected = [
                storage_root / "downloads",
                storage_root / "videos",
                storage_root / "localization_jobs",
            ]
            if hasattr(settings, "DATA_DIR"):
                protected.append((settings.DATA_DIR / "data" / "samples").resolve())
            if any(resolved == pr or resolved.is_relative_to(pr) for pr in protected):
                logger.warning("Prevented deletion of protected media asset: %s", resolved)
                continue
            resolved.unlink(missing_ok=True)
        except Exception:
            pass


async def _tool_progress(job_id: str, pct: float, step: str, user_id: str):
    """DB status + WS progress. The Tools UI listens on WS only —
    update_job_status alone is invisible to the frontend."""
    from backend.agents.job_helper import update_job_status
    from backend.core.ws_manager import ws_manager
    await update_job_status(job_id, "running", progress_pct=pct, current_step=step)
    await ws_manager.send_progress(job_id, pct, step, user_id)


# Demucs owns the separation stage (job 6% → 15%); ASR starts at 16%.
SEPARATION_PROGRESS_START = 6
SEPARATION_PROGRESS_END = 15


def separation_job_progress(fraction: float) -> float:
    """Map a normalized 0..1 separation fraction onto the 6→15 job window.

    The end is exclusive: a completed separation reports 15 and ASR takes over
    from 16, so a full 1.0 must never equal or exceed the ASR start.
    """
    try:
        frac = float(fraction)
    except (TypeError, ValueError):
        frac = 0.0
    if frac != frac:  # NaN
        frac = 0.0
    frac = min(1.0, max(0.0, frac))
    return SEPARATION_PROGRESS_START + frac * (SEPARATION_PROGRESS_END - SEPARATION_PROGRESS_START)


def separation_step_text(fraction: float) -> str:
    try:
        pct = int(min(1.0, max(0.0, float(fraction))) * 100)
    except (TypeError, ValueError):
        pct = 0
    return f"Đang tách giọng/nền HQ · {pct}%"


def _separation_progress_forwarder(job_id: str, user_id: str, previous: list[float]):
    """Bridge Demucs' worker-thread callback to the async progress emitter.

    Mirrors the Whisper progress bridge in clip_extractor: the callback runs on
    a Demucs worker thread, so it schedules the coroutine on the loop. The
    `previous` cell holds the last emitted percent so a late/duplicated
    callback can never walk the job backwards.
    """
    loop = asyncio.get_running_loop()

    def _forward(fraction: float) -> None:
        pct = separation_job_progress(fraction)
        if pct < previous[0]:
            return
        previous[0] = pct
        asyncio.run_coroutine_threadsafe(
            _tool_progress(job_id, pct, separation_step_text(fraction), user_id),
            loop,
        )

    return _forward


async def _tool_success(
    job_id: str, out_path: Path, cleanup: list[Path], user_id: str,
    extra_output: dict | None = None,
    expect: dict | None = None,
) -> bool:
    """Validate the artifact, mark job success, emit job_complete, delete
    input scratch files. Returns True when the artifact was delivered, False
    when the validation gate failed the job instead.

    `expect` (all optional): {"min_duration": float, "orientation": str,
    "audio": bool} — assertions the RUNNER makes about its own output, which
    are fatal on mismatch precisely because the runner declared them. This is
    the hook the geometry class of bug needs: a blur-fill that shrank the
    picture, or a reframe that came back landscape, is invisible to an exit
    code and obvious to ffprobe.

    Returning bool matters for runners that do more work after this call —
    they must be able to tell "delivered" from "gate-failed".
    """
    from backend.agents.job_helper import update_job_status
    from backend.core.exceptions import GenerationError
    from backend.core.ws_manager import ws_manager
    from backend.services.output_validator import validate_output

    # "Verify the artifact before you call it done", as code, at the one point
    # every file-producing tool passes through. A runner that finishes without
    # raising has NOT proved it made something playable — Audio Enhance once
    # reported success over a 0-byte mp3, and each such bug got its own fix
    # afterwards, which means the next one waits for the next audit. Only
    # unambiguous breakage is fatal here (see output_validator's docstring);
    # soft findings never block.
    verdict = await validate_output(out_path, expect)
    if not verdict.ok:
        logger.warning(
            "tool job %s produced an invalid artifact (%s): %s",
            job_id, verdict.fatal_issues[0].check, out_path,
        )
        await _tool_fail(
            job_id, GenerationError(verdict.message()), cleanup, user_id,
            "output_validation",
        )
        return False

    output_data = {"file": str(out_path)}
    if extra_output:
        output_data.update(extra_output)
    accepted = await update_job_status(
        job_id, "success", progress_pct=100, current_step="Done",
        output_data=output_data,
    )
    if not accepted:
        logger.info(
            "tool job %s terminal success suppressed because canonical state is paused/cancelled",
            job_id,
        )
        _cleanup_paths(*cleanup)
        return False
    await ws_manager.send({
        "type": "job_complete", "job_id": job_id,
        "result": {
            "download_url": f"/api/tools/download/{job_id}",
            **(extra_output or {}),
        },
    }, user_id)
    _cleanup_paths(*cleanup)
    return True


async def _tool_fail(job_id: str, err: Exception, cleanup: list[Path], user_id: str, tool: str):
    """Mark job failed, emit job_failed, delete input scratch files."""
    from backend.agents.job_helper import update_job_status
    from backend.core.ws_manager import ws_manager
    logger.error("TASK FAIL  tool:%s | job=%s: %s", tool, job_id[:8], err, exc_info=True)
    accepted = await update_job_status(job_id, "failed", error_message=str(err))
    if accepted:
        await ws_manager.send({"type": "job_failed", "job_id": job_id, "error": str(err)}, user_id)
    else:
        logger.info(
            "tool job %s failure event suppressed because canonical state is paused/cancelled",
            job_id,
        )
    _cleanup_paths(*cleanup)


async def _load_user_settings(user_id: str):
    """Load the UserSettings row for AI-provider routing. Returns None if absent."""
    from backend.database import AsyncSessionLocal
    from backend.models.user_settings import UserSettings
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        row = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
        return row.scalar_one_or_none()


def _strip_json_fence(text: str) -> str:
    """Strip an optional ```json ... ``` fence the model occasionally wraps."""
    out = (text or "").strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[-1] if "\n" in out else out[3:]
        if out.endswith("```"):
            out = out[:-3]
        if out.lstrip().lower().startswith("json"):
            out = out.lstrip()[4:].lstrip()
    return out.strip()


# ── Captions ───────────────────────────────────────────────────────────────

async def run_tool_captions(job_id: str, in_path: Path, style: str, emoji_style: str, user_id: str = "local"):
    from backend.api.tools import tool_out_path
    logger.info("TASK START tool:captions | job=%s style=%s", job_id[:8], style)
    cleanup = [in_path]
    try:
        await _tool_progress(job_id, 5, "Transcribing audio...", user_id)
        from backend.services.whisper_service import whisper_service
        tx = await whisper_service.transcribe(str(in_path))
        segments = tx.get("segments", [])
        if not segments:
            raise ValueError("No speech detected — nothing to caption")

        await _tool_progress(job_id, 45, f"Generating {style} captions...", user_id)
        from backend.services.caption_service import generate_captions_ass, burn_captions
        aspect = await _probe_aspect_ratio(in_path)
        ass_path = await generate_captions_ass(segments, style=style, aspect_ratio=aspect, emoji_style=emoji_style)
        cleanup.append(ass_path)

        await _tool_progress(job_id, 75, "Burning captions into video...", user_id)
        out_path = tool_out_path(job_id, ".mp4")
        await burn_captions(in_path, ass_path, out_path)
        if not out_path.exists():
            raise RuntimeError("Caption burn produced no output")

        await _tool_success(job_id, out_path, cleanup, user_id)
        logger.info("TASK DONE  tool:captions | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "captions")


# ── Reframe (to vertical 9:16) ─────────────────────────────────────────────

async def run_tool_reframe(job_id: str, in_path: Path, user_id: str = "local"):
    from backend.api.tools import tool_out_path
    from backend.core.ws_manager import ws_manager
    import shutil

    logger.info("TASK START tool:reframe | job=%s", job_id[:8])
    cleanup = [in_path]
    try:
        out_path = tool_out_path(job_id, ".mp4")

        # If the source is already 9:16 (or narrower) there's nothing to
        # reframe — return it unchanged with an info constraint warning.
        # NOT "portrait": square and 4:5 sources DO have side content to crop.
        if await _is_already_vertical(in_path):
            await _tool_progress(job_id, 50, "Source already vertical...", user_id)
            await asyncio.to_thread(shutil.copy2, str(in_path), str(out_path))
            await ws_manager.send_constraint_warning(
                constraint="reframe_noop",
                message="Source is already vertical — returned unchanged.",
                severity="info",
                user_id=user_id,
            )
        else:
            await _tool_progress(job_id, 40, "Reframing to 9:16 (blur-fill)...", user_id)
            from backend.services.ffmpeg_service import convert_aspect_ratio
            result_path = await convert_aspect_ratio(
                in_path, target_aspect="9:16", method="blur_fill", output_path=out_path,
            )
            if result_path != out_path and Path(result_path).exists():
                await asyncio.to_thread(shutil.move, str(result_path), str(out_path))
            if not out_path.exists():
                raise RuntimeError("Reframe produced no output")

        # The one thing this tool exists to guarantee. Geometry is invisible
        # to an exit code and obvious to ffprobe: a blur-fill that shrank the
        # picture or a convert that came back landscape both "succeed".
        await _tool_success(job_id, out_path, cleanup, user_id,
                            expect={"orientation": "portrait"})
        logger.info("TASK DONE  tool:reframe | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "reframe")


# ── Audio enhance (denoise + loudness normalize) ───────────────────────────

# The denoise half of the chain. highpass/lowpass trim rumble + hiss, afftdn
# removes broadband noise. Kept separate from loudnorm because the measurement
# pass has to run THROUGH it — see _measure_loudness.
AUDIO_DENOISE_FILTER = "highpass=f=80,lowpass=f=12000,afftdn=nr=12"

# EBU R128 targets. -16 LUFS matches YouTube/TikTok playback reference within
# ~2 LU.
_TARGET_I = -16.0
_TARGET_TP = -1.5

# Dynamic single-pass fallback — used only when the measurement pass fails.
AUDIO_ENHANCE_FILTER = (
    f"{AUDIO_DENOISE_FILTER},loudnorm=I={_TARGET_I}:TP={_TARGET_TP}:LRA=11"
)


async def _measure_loudness(path: Path, through_denoise: bool = False) -> dict | None:
    """Pass 1: decode audio only and return loudnorm's measurement JSON
    (input_i / input_tp / input_lra / input_thresh / target_offset), or None
    when the file can't be measured. No video decode, no output written.

    `through_denoise` prefixes the measurement with the same denoise chain the
    apply pass uses. The enhance path MUST measure this way: loudnorm receives
    the denoised signal, and feeding it raw-audio measurements makes the
    linear gain wrong (the denoiser changes the level between measurement and
    application). Skip decisions measure raw (the default) — "is this file
    already at target AS-IS" is a question about the file, not about the
    filter chain.
    """
    import json as _json
    import re as _re

    af = f"loudnorm=I={_TARGET_I}:TP={_TARGET_TP}:LRA=11:print_format=json"
    if through_denoise:
        af = f"{AUDIO_DENOISE_FILTER},{af}"

    def _run():
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-vn", "-sn", "-dn",
             "-i", str(path), "-af", af, "-f", "null", "-"],
            capture_output=True, text=True, timeout=600,
        )
        # loudnorm prints its JSON block mid-stderr; ffmpeg appends the mux
        # summary AFTER it, so an end-anchored match never fires. Take the
        # last {...} block that parses and carries the expected keys.
        for blob in reversed(_re.findall(r"\{[^{}]*\}", r.stderr)):
            try:
                d = _json.loads(blob)
                for k in ("input_i", "input_tp", "input_lra", "input_thresh"):
                    # loudnorm reports "-inf" for digital silence. Note that
                    # float("-inf") PARSES — a bare float() call is not the
                    # guard it looks like — and feeding -inf into the linear
                    # filter yields a NaN target and an ffmpeg the arg parser
                    # rejects. Demand a finite number.
                    if not math.isfinite(float(d[k])):
                        raise ValueError(f"{k} is not finite: {d[k]!r}")
                return d
            except (KeyError, ValueError):
                continue
        return None

    try:
        return await asyncio.to_thread(_run)
    except Exception:
        return None


def _is_already_normalized(measured: dict | None) -> bool:
    """True when the audio is close enough to target that enhancement can only
    do harm: within 1 LU of -16 LUFS with true peak at or under -1.0 dBTP.
    Conservative on purpose — an unmeasurable file is NOT "already done"."""
    if not measured:
        return False
    try:
        i, tp = float(measured["input_i"]), float(measured["input_tp"])
    except (KeyError, ValueError):
        return False
    if not (math.isfinite(i) and math.isfinite(tp)):
        return False   # digital silence reads as -inf, which is not "done"
    return abs(i - _TARGET_I) <= 1.0 and tp <= -1.0


def _linear_filter(measured: dict) -> str:
    """The pass-2 filter chain: denoise + linear (fixed-gain) loudnorm fed
    with the pass-1 measurement.

    Single-pass loudnorm is not a less accurate version of the same thing — it
    is a DIFFERENT algorithm. One pass runs in DYNAMIC mode, continuously
    riding the gain, which is audible as pumping/breathing on real content and
    crushes loudness range (an already-normalized clip measured LRA 1.4 → 1.0
    → 0.8 across repeat passes while the level stayed at -16). Linear mode
    applies a single gain offset computed from the measurement — transparent,
    and it preserves the source's dynamics.

    ffmpeg SILENTLY reverts linear=true to dynamic mode in two cases
    (af_loudnorm.c), and both must be pre-empted here or high-dynamics content
    gets crushed again:

      * measured_LRA above the LRA target → raise the target to the
        measurement (in linear mode LRA is only this revert threshold).
      * the needed gain would push the measured true peak past TP → ffmpeg
        does NOT apply less gain, it flips to dynamic. Pre-clamp the target
        level to what the TP headroom allows: quieter-but-clean, one fixed
        gain, no pumping.
    """
    try:
        input_i = float(measured["input_i"])
        input_tp = float(measured["input_tp"])
        lra_target = min(50.0, max(11.0, float(measured["input_lra"]) + 1.0))
    except (KeyError, ValueError):
        input_i, input_tp, lra_target = -24.0, -6.0, 11.0
    gain_needed = _TARGET_I - input_i
    headroom = (_TARGET_TP - 0.1) - input_tp  # 0.1 dB margin under the revert check
    eff_target_i = input_i + min(gain_needed, headroom)
    # loudnorm rejects I outside [-70, -5]; attenuation (negative gain) is fine.
    eff_target_i = max(-70.0, min(-5.0, eff_target_i))
    return (
        f"{AUDIO_DENOISE_FILTER},"
        f"loudnorm=I={eff_target_i:.2f}:TP={_TARGET_TP}:LRA={lra_target:g}"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":linear=true"
    )


async def run_tool_audio_enhance(job_id: str, in_path: Path, user_id: str = "local"):
    from backend.api.tools import tool_out_path
    from backend.core.ws_manager import ws_manager
    from backend.services.clip_extractor import _has_video_stream
    from backend.services.ffmpeg_service import has_audio_stream
    logger.info("TASK START tool:audio_enhance | job=%s", job_id[:8])
    cleanup = [in_path]
    try:
        # Nothing to enhance without an audio track — say so instead of
        # handing ffmpeg a filtergraph it can't satisfy and surfacing its
        # stderr as the failure reason.
        if not await has_audio_stream(in_path):
            await _tool_fail(
                job_id, RuntimeError("This file has no audio track to enhance."),
                cleanup, user_id, "audio_enhance",
            )
            return

        # Measure first — enhancing already-normalized audio is a harmful
        # no-op. It cannot improve the level and it costs dynamics (another
        # denoise smear + another compression pass) plus a lossy re-encode
        # generation. Same pass-through contract as the no-audio-track case
        # above, with a warning that says why.
        raw = await _measure_loudness(in_path)
        if _is_already_normalized(raw):
            import shutil
            out_path = tool_out_path(job_id, in_path.suffix.lower() or ".mp4")
            await asyncio.to_thread(shutil.copy2, str(in_path), str(out_path))
            await ws_manager.send_constraint_warning(
                constraint="audio_enhance_noop",
                message=(f"Audio is already at target loudness "
                         f"({raw['input_i']} LUFS) — returned unchanged. "
                         "Re-enhancing normalized audio would only degrade it."),
                severity="info",
                user_id=user_id,
            )
            await _tool_success(job_id, out_path, cleanup, user_id)
            logger.info("TASK DONE  tool:audio_enhance (no-op, already normalized) | job=%s",
                        job_id[:8])
            return

        await _tool_progress(job_id, 20, "Denoising + normalizing audio...", user_id)
        has_video = await asyncio.to_thread(_has_video_stream, in_path)
        # The container follows what we ENCODE: an audio-only source (the tool
        # accepts podcasts) has no video stream to copy, and re-encoding a
        # .webm to H.264 is rejected outright by the WebM muxer.
        out_path = tool_out_path(job_id, ".mp4" if has_video else ".mp3")

        # Pass 1 of two: measure the DENOISED signal. loudnorm receives
        # afftdn's output, so measuring the raw audio makes the linear gain
        # wrong — the denoiser changes the level between measurement and
        # application. On failure we fall back to the old single-pass filter,
        # which normalizes but in dynamic mode.
        measured = await _measure_loudness(in_path, through_denoise=True)
        af = _linear_filter(measured) if measured else AUDIO_ENHANCE_FILTER

        def _enhance():
            # highpass/lowpass trim rumble + hiss; afftdn denoise; loudnorm
            # applies ONE fixed gain from the pass-1 measurement (linear=true)
            # instead of continuously riding it, which is what made repeat
            # passes audibly pump and crushed loudness range.
            base = ["ffmpeg", "-y", "-i", str(in_path), "-af", af]
            if not has_video:
                res = subprocess.run(
                    base + ["-c:a", "libmp3lame", "-q:a", "2", str(out_path)],
                    capture_output=True, text=True, timeout=900,
                )
                if res.returncode != 0:
                    raise RuntimeError(f"Audio enhance failed: {res.stderr[:400]}")
                return
            # Two passes: `-c:v copy` into .mp4 is invalid for VP8/VP9, and
            # .webm is both an accepted extension and what most screen
            # recorders export — that combination used to fail the job with a
            # raw "Could not find tag for codec vp9" and leave a 0-byte stub.
            # The re-encode pass works for any source we accept.
            attempts = (
                ("copy", ["-c:v", "copy"]),
                ("re-encode", ["-c:v", "libx264", "-preset", "veryfast",
                               "-crf", "20", "-pix_fmt", "yuv420p"]),
            )
            tail = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                    str(out_path)]
            last_err = ""
            for label, vargs in attempts:
                res = subprocess.run(base + vargs + tail,
                                     capture_output=True, text=True, timeout=900)
                if res.returncode == 0:
                    return
                last_err = res.stderr[-400:]
                logger.warning("audio enhance (%s pass) failed: %s", label, last_err)
                # A failed pass leaves a 0-byte/partial file behind; drop it so
                # the next pass isn't fooled by it and a failure can't be
                # mistaken for a result.
                try:
                    out_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise RuntimeError(f"Audio enhance failed: {last_err}")
        await asyncio.to_thread(_enhance)

        # Never report success over a missing/empty file — the download would
        # be 0 bytes and the job would still read "success".
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError("Audio enhance produced no output")

        await _tool_success(job_id, out_path, cleanup, user_id)
        logger.info("TASK DONE  tool:audio_enhance | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "audio_enhance")


# ── Watermark (logo overlay) ───────────────────────────────────────────────

_WATERMARK_POS = {
    "top-left":     "{m}:{m}",
    "top-right":    "main_w-overlay_w-{m}:{m}",
    "bottom-left":  "{m}:main_h-overlay_h-{m}",
    "bottom-right": "main_w-overlay_w-{m}:main_h-overlay_h-{m}",
}


async def run_tool_watermark(
    job_id: str, in_path: Path, logo_path: Path,
    position: str, opacity: float, size_pct: float,
    user_id: str = "local",
):
    from backend.api.tools import tool_out_path
    logger.info("TASK START tool:watermark | job=%s pos=%s", job_id[:8], position)
    cleanup = [in_path, logo_path]
    try:
        await _tool_progress(job_id, 20, "Applying watermark...", user_id)
        out_path = tool_out_path(job_id, ".mp4")

        opacity = max(0.05, min(1.0, float(opacity)))
        size_pct = max(2.0, min(40.0, float(size_pct)))
        margin = 24
        overlay_xy = _WATERMARK_POS.get(position, _WATERMARK_POS["bottom-right"]).format(m=margin)

        def _apply():
            # Scale the logo to size_pct of the video width, apply opacity via
            # colorchannelmixer on the alpha channel, then overlay.
            fc = (
                f"[1:v]format=rgba,colorchannelmixer=aa={opacity:.3f},"
                f"scale=iw*{size_pct/100.0:.4f}*main_w/iw:-1[wm];"
                f"[0:v][wm]overlay={overlay_xy}:format=auto"
            )
            # main_w isn't available inside the [1:v] chain; use a two-step
            # scale referencing the main input width via scale2ref instead.
            fc = (
                f"[1:v][0:v]scale2ref=w=iw*{size_pct/100.0:.4f}:h=ow/mdar[wm][base];"
                f"[wm]format=rgba,colorchannelmixer=aa={opacity:.3f}[wmo];"
                f"[base][wmo]overlay={overlay_xy}:format=auto"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", str(in_path),
                "-i", str(logo_path),
                "-filter_complex", fc,
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-c:a", "copy",
                str(out_path),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if res.returncode != 0:
                raise RuntimeError(f"Watermark render failed: {res.stderr[:400]}")
        await asyncio.to_thread(_apply)
        if not out_path.exists():
            raise RuntimeError("Watermark rendering failed — the logo or video may be invalid")

        await _tool_success(job_id, out_path, cleanup, user_id)
        logger.info("TASK DONE  tool:watermark | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "watermark")


# ── Remove silence (FFmpeg silencedetect → trim gaps) ──────────────────────

def _parse_silences(stderr: str) -> list[tuple[float, float]]:
    """Parse ffmpeg silencedetect stderr into [(start, end), ...] ranges."""
    silences: list[tuple[float, float]] = []
    cur_start = None
    for line in stderr.splitlines():
        line = line.strip()
        if "silence_start:" in line:
            try:
                cur_start = float(line.split("silence_start:")[1].strip().split()[0])
            except (ValueError, IndexError):
                cur_start = None
        elif "silence_end:" in line and cur_start is not None:
            try:
                end = float(line.split("silence_end:")[1].strip().split()[0].rstrip("|"))
                silences.append((cur_start, end))
            except (ValueError, IndexError):
                pass
            cur_start = None
    return silences


async def run_tool_remove_silence(job_id: str, in_path: Path, user_id: str = "local"):
    from backend.api.tools import tool_out_path
    from backend.core.ws_manager import ws_manager
    from backend.services.clip_extractor import (
        _has_video_stream, _silence_removal_timeout,
    )
    from backend.services.video_utils import probe_duration
    import shutil

    logger.info("TASK START tool:remove_silence | job=%s", job_id[:8])
    cleanup = [in_path]
    try:
        has_video = await asyncio.to_thread(_has_video_stream, in_path)
        # Audio-only sources are a first-class input here (the tool is pointed
        # at podcasts): they have no video stream to re-encode and come back
        # as mp3.
        out_path = tool_out_path(job_id, ".mp4" if has_video else ".mp3")

        await _tool_progress(job_id, 15, "Detecting silence...", user_id)

        # Both passes read the WHOLE timeline, so their runtime scales with the
        # source. The old flat caps (600s detect / 900s cut) were sized for
        # short clips; on a long recording they fired as a raw TimeoutExpired
        # carrying the entire ffmpeg argv into the UI.
        source_duration = await asyncio.to_thread(probe_duration, in_path, 0.0)
        timeout_s = _silence_removal_timeout(source_duration)

        def _detect():
            cmd = [
                "ffmpeg", "-i", str(in_path),
                "-af", "silencedetect=noise=-35dB:d=0.6",
                "-f", "null", "-",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
            return res.stderr
        stderr = await asyncio.to_thread(_detect)
        silences = _parse_silences(stderr)

        duration = await asyncio.to_thread(probe_duration, in_path, 0.0)
        # Build the keep-segments = the gaps between detected silences.
        keep: list[tuple[float, float]] = []
        cursor = 0.0
        for s_start, s_end in silences:
            if s_start - cursor > 0.05:
                keep.append((cursor, s_start))
            cursor = s_end
        if duration > 0 and duration - cursor > 0.05:
            keep.append((cursor, duration))

        if not silences or not keep:
            # Returned UNCHANGED, so the output keeps the SOURCE's container —
            # naming a copied .webm/.m4a ".mp4"/".mp3" would hand the browser
            # a content-type the bytes don't match. `out_path` above is named
            # for what the cut pass would ENCODE; nothing is encoded here.
            noop_path = tool_out_path(job_id, in_path.suffix.lower() or out_path.suffix)
            await asyncio.to_thread(shutil.copy2, str(in_path), str(noop_path))
            await ws_manager.send_constraint_warning(
                constraint="silence_noop",
                message="No removable silence detected — returned unchanged.",
                severity="info",
                user_id=user_id,
            )
            await _tool_success(job_id, noop_path, cleanup, user_id)
            logger.info("TASK DONE  tool:remove_silence (noop) | job=%s", job_id[:8])
            return

        await _tool_progress(job_id, 60, f"Cutting {len(silences)} silent gaps...", user_id)

        def _cut():
            # Build a single filter_complex that trims+concats the keep ranges.
            v_parts, a_parts = [], []
            for i, (a, b) in enumerate(keep):
                v_parts.append(
                    f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}];"
                )
                a_parts.append(
                    f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}];"
                )
            n = len(keep)
            if has_video:
                concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
                fc = (
                    "".join(v_parts) + "".join(a_parts)
                    + f"{concat_inputs}concat=n={n}:v=1:a=1[vout][aout]"
                )
                codec_args = [
                    "-map", "[vout]", "-map", "[aout]",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                ]
            else:
                concat_inputs = "".join(f"[a{i}]" for i in range(n))
                fc = ("".join(a_parts)
                      + f"{concat_inputs}concat=n={n}:v=0:a=1[aout]")
                codec_args = ["-map", "[aout]", "-c:a", "libmp3lame", "-q:a", "2"]
            cmd = [
                "ffmpeg", "-y", "-i", str(in_path),
                "-filter_complex", fc, *codec_args,
                str(out_path),
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
            except subprocess.TimeoutExpired as e:
                # The default repr embeds the whole argv (absolute paths, the
                # entire filtergraph) and reaches the user verbatim.
                raise RuntimeError(
                    f"Silence removal timed out after {timeout_s // 60} min on a "
                    f"{source_duration / 60:.0f} min source. Trim it into shorter "
                    f"pieces and run them separately."
                ) from e
            if res.returncode != 0:
                raise RuntimeError(f"Silence cut failed: {res.stderr[:400]}")
        await asyncio.to_thread(_cut)

        await _tool_success(job_id, out_path, cleanup, user_id)
        logger.info("TASK DONE  tool:remove_silence | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "remove_silence")


# ── Merge clips ────────────────────────────────────────────────────────────

_MERGE_CLIPS_TARGETS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1":  (1080, 1080),
}


def _probe_dims_sync(video_path: Path) -> tuple[int, int]:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True, timeout=10,
    )
    w, h = map(int, r.stdout.strip().split(","))
    return w, h


def _resolve_merge_target(first_clip: Path, target_aspect: str) -> tuple[int, int, str]:
    """Pick (width, height, label) for the merge output. `auto` snaps the
    first clip's aspect to the nearest of 9:16/16:9/1:1."""
    if target_aspect in _MERGE_CLIPS_TARGETS:
        w, h = _MERGE_CLIPS_TARGETS[target_aspect]
        return w, h, target_aspect
    try:
        sw, sh = _probe_dims_sync(first_clip)
        ratio = sw / sh if sh else 1.0
    except Exception:
        ratio = 9 / 16
    options = {"9:16": 9 / 16, "1:1": 1.0, "16:9": 16 / 9}
    label = min(options.keys(), key=lambda k: abs(options[k] - ratio))
    w, h = _MERGE_CLIPS_TARGETS[label]
    return w, h, label


def _crop_to_aspect_sync(src: Path, dst: Path, target_w: int, target_h: int):
    """Scale+center-crop a clip to exactly target_w x target_h and re-encode
    to a uniform codec so the concat is clean.

    "Normalized" has to mean the stream LAYOUT too, not just codec and fps. A
    SILENT source has no audio for `-c:a aac` to encode, so it normalized to
    video-only — and the concat demuxer takes its layout from the FIRST file.
    A silent intro card in front of a talking video therefore merged to a file
    with NO audio stream at all: the user's audio silently discarded, job
    "success". In the other order the audio track ended early. Giving every
    clip a real (silent) track heals both, and the crossfade path with it.
    """
    from backend.services.ffmpeg_service import has_audio_stream_sync

    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},setsar=1"
    )
    src_has_audio = has_audio_stream_sync(src)

    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if not src_has_audio:
        # An INFINITE silence source — it must be bounded, or the encode
        # never ends. `-shortest` bounds it to the video stream.
        cmd += ["-f", "lavfi", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += ["-vf", vf]
    if not src_has_audio:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += [
        "-r", "30",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        str(dst),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"Clip normalize failed: {res.stderr[:400]}")


async def run_tool_merge_clips(
    job_id: str,
    in_paths: list[Path],
    target_aspect: str = "auto",
    user_id: str = "local",
    transition: str = "none",
):
    """Stitch N user clips into one video. Sequential per-clip re-encode keeps
    RAM/CPU bounded (parallel ffmpegs thrash)."""
    from backend.api.tools import tool_out_path
    from backend.services.ffmpeg_service import stitch_clips

    logger.info(
        "TASK START tool:merge_clips | job=%s clips=%d target=%s",
        job_id[:8], len(in_paths), target_aspect,
    )
    cleanup: list[Path] = list(in_paths)
    stitch_transition = "fade" if transition == "crossfade" else "none"
    try:
        await _tool_progress(job_id, 5, "Probing clip dimensions...", user_id)
        target_w, target_h, label = await asyncio.to_thread(
            _resolve_merge_target, in_paths[0], target_aspect,
        )
        logger.info("tool:merge_clips | job=%s target=%dx%d (%s)", job_id[:8], target_w, target_h, label)

        normalized: list[Path] = []
        n = len(in_paths)
        for i, src in enumerate(in_paths):
            pct_start = 10 + (70 * i / n)
            await _tool_progress(job_id, pct_start, f"Cropping clip {i+1}/{n} to {label}...", user_id)
            norm_path = src.with_name(f"{src.stem}_n.mp4")
            await asyncio.to_thread(_crop_to_aspect_sync, src, norm_path, target_w, target_h)
            normalized.append(norm_path)
            cleanup.append(norm_path)

        await _tool_progress(job_id, 85, f"Stitching {n} clips...", user_id)
        out_path = tool_out_path(job_id, ".mp4")
        result = await stitch_clips(normalized, output_path=out_path, transition=stitch_transition)
        if result != out_path and Path(result).exists():
            import shutil
            await asyncio.to_thread(shutil.move, str(result), str(out_path))

        await _tool_success(job_id, out_path, cleanup, user_id)
        logger.info("TASK DONE  tool:merge_clips | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "merge_clips")


# ── Translate (captions + optional Edge-TTS dub) ───────────────────────────

_LANG_NAME_MAP = {
    "vi": "Vietnamese",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "nl": "Dutch",
    "pl": "Polish",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "tr": "Turkish",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
}


async def run_tool_translate(
    job_id: str,
    in_path: Path,
    target_language: str,
    mode: str,
    voice_id: str,
    caption_style: str,
    emoji_style: str,
    user_id: str = "local",
    target_aspect: str | None = None,
    profile_id: str | None = None,
    profile_override: dict | None = None,
    review_translation: bool | None = None,
    cleanup_input: bool | None = None,
    mask_box: dict | None = None,
    caption_placement: dict | None = None,
    downloaded_video_id: str | None = None,
    auto_repair: bool | None = None,
):
    """Canonical resumable Phase-C localization runner.

    Expensive stages persist under storage/localization_jobs/<job_id>. A rerun
    resumes completed artifacts or invalidates from a user-selected downstream
    stage; there is no second job system.
    """
    import json as _json
    import shutil
    from dataclasses import asdict

    from backend.agents.job_helper import job_cancelled
    from backend.api.tools import tool_out_path
    from backend.config import settings
    from backend.core.exceptions import JobCancelledError
    from backend.core.localization import LocalizationProfile, LocalizationTranscript
    from backend.services.localization_qc_service import evaluate_localization_qc
    from backend.services.localization_service import (
        caption_segments_from_localization,
        materialize_localization_profile,
        mix_dub_audio,
        persist_dubbing_rewrites,
        prepare_semantic_transcript,
        resolve_localization_profile,
        synthesize_timed_dub,
        transcribe_for_localization,
        translate_transcript,
        validate_localization_tts_preflight,
    )
    from backend.services.localization_stage_service import LocalizationRun

    async def _cancel_checkpoint(label: str) -> None:
        if await job_cancelled(job_id):
            raise JobCancelledError(f"Localization cancelled before {label}")

    target_language = _LANG_NAME_MAP.get(
        target_language.strip().lower(), target_language.strip()
    )
    is_dub = mode == "full_dub"
    request_snapshot = {
        "target_language": target_language,
        "mode": mode,
        "voice_id": voice_id,
        "caption_style": caption_style,
        "emoji_style": emoji_style,
        "target_aspect": target_aspect,
        "profile_id": profile_id,
        "review_translation": bool(review_translation),
        "cleanup_input": cleanup_input,
        "mask_box": mask_box,
        "caption_placement": caption_placement,
        "downloaded_video_id": downloaded_video_id,
    }
    if auto_repair is not None:
        request_snapshot["auto_repair"] = bool(auto_repair)
    logger.info(
        "TASK START tool:translate | job=%s target=%s mode=%s profile=%s aspect=%s",
        job_id[:8], target_language, mode, profile_id or "default", target_aspect or "default",
    )
    # Consume a recovery-claim marker only for a genuinely new/reopened
    # attempt. Bounded auto-repair recursively re-enters this runner while the
    # same Job is already running; emitting 0% there makes UI progress jump
    # backwards even though completed upstream work is being preserved.
    announce_start = True
    try:
        from backend.database import AsyncSessionLocal
        from backend.models.job import Job
        async with AsyncSessionLocal() as db:
            row = await db.get(Job, job_id)
            if row is not None and row.status == "running":
                announce_start = False
    except Exception:
        pass
    if announce_start:
        await _tool_progress(job_id, 0, "Starting localization…", user_id)

    original_input = Path(in_path)
    cleanup: list[Path] = []
    if cleanup_input is True:
        cleanup.append(original_input)
    elif cleanup_input is None:
        # Safe by default: only treat as ephemeral scratch if inside tools/in or tools/staging
        try:
            from backend.api.tools import tools_in_dir, tools_staging_dir
            res_orig = original_input.resolve()
            if res_orig.is_relative_to(tools_in_dir().resolve()) or res_orig.is_relative_to(tools_staging_dir().resolve()):
                cleanup.append(original_input)
        except Exception:
            pass
    current_stage = "separation"
    run = None

    # Snapshot the last delivered artifact BEFORE this attempt writes anything.
    # restart_job() deliberately keeps output_json, so a failed rerun can retain
    # the previous successful version. Also backfill the Publish bridge for
    # pre-upgrade successful localization jobs before a new attempt starts.
    previous_output: dict = {}
    previous_output_path: Path | None = None
    previous_generated_video_id: str | None = None
    try:
        from backend.services.localization_publish_service import (
            promote_localization_output,
        )
        await promote_localization_output(job_id)
    except Exception:
        # A brand-new job has no publishable output, and a promotion failure must
        # never prevent localization itself from running.
        logger.debug(
            "No previous localization output to promote for job %s",
            job_id[:8],
            exc_info=True,
        )
    try:
        from backend.database import AsyncSessionLocal
        from backend.models.job import Job
        async with AsyncSessionLocal() as db:
            previous_job = await db.get(Job, job_id)
            if previous_job and previous_job.output_json:
                try:
                    previous_output = _json.loads(previous_job.output_json)
                except (TypeError, ValueError):
                    previous_output = {}
        if isinstance(previous_output, dict):
            previous_generated_video_id = (
                str(previous_output.get("generated_video_id"))
                if previous_output.get("generated_video_id")
                else None
            )
            previous_file = previous_output.get("file")
            if previous_file:
                candidate_previous = Path(previous_file)
                if candidate_previous.is_file():
                    previous_output_path = candidate_previous
    except Exception:
        previous_output = {}
        previous_output_path = None
        previous_generated_video_id = None

    try:
        # Existing reruns use their effective profile snapshot. An explicit
        # profile_override is the C11 expert path. Loaded BEFORE the A/V
        # preflight so a resumed job can record why it stopped.
        try:
            run = LocalizationRun.load(job_id)
            stored_profile = run.effective_profile
        except ValueError:
            run = None
            stored_profile = {}

        # ── Source A/V preflight (before any provider or GPU work) ───────────
        # A video-only source cannot be dubbed, separated, or transcribed.
        # Without this gate the failure surfaced as a raw ffmpeg traceback
        # ("Stream map '0:a:0' matches no streams") from deep inside Demucs
        # extraction, AFTER the htdemucs_ft model had already been loaded onto
        # the GPU — a long, expensive, completely wasted unit of work whose
        # error told the user nothing actionable.
        #
        # Runs before profile materialization and the GPU gate, so a
        # video-only source is rejected while the fix is still a one-click
        # re-download rather than after credentials and models are involved.
        #
        # Scoped to full_dub deliberately: a subtitle-only run over a silent
        # clip is legitimate, so this asserts nothing about other modes. The
        # probe is one ffprobe call on a file we are about to copy anyway.
        if is_dub:
            from backend.core.exceptions import MissingSourceAudioError
            from backend.services.video_utils import probe_stream_inventory
            _preflight_inventory = await asyncio.to_thread(
                probe_stream_inventory, original_input
            )
            if (
                _preflight_inventory.readable
                and _preflight_inventory.has_video
                and not _preflight_inventory.has_audio
            ):
                _error = MissingSourceAudioError(original_input)
                # On a RESUME the manifest already exists, and it is what a
                # human reads to find out why a job stopped. Record the reason
                # there before raising, or the resumed job fails with a stage
                # record that still says "pending" and points nowhere.
                if run is not None:
                    try:
                        run.fail("separation", _error)
                    except Exception:
                        logger.debug(
                            "Could not record missing-audio on the resumed manifest",
                            exc_info=True,
                        )
                raise _error

        user_settings = await _load_user_settings(user_id)

        if profile_override:
            profile = LocalizationProfile.model_validate(profile_override)
        elif stored_profile:
            profile = LocalizationProfile.model_validate(stored_profile)
        else:
            profile = resolve_localization_profile(user_settings, profile_id)

        if run is None:
            # Preserve whether the SOURCE profile intentionally pinned a TTS
            # provider or inherited Settings. materialize_localization_profile()
            # resolves both to a concrete provider, so without this provenance
            # a later Settings change cannot distinguish "stale inherited
            # binding" from "intentional FAST=Edge while HQ=ElevenLabs".
            request_snapshot["tts_binding_mode"] = (
                "pinned"
                if (profile.tts_connection_id or profile.tts_provider)
                else "inherit"
            )

        # Manual tool controls are job-level overrides on the resolved profile.
        updates = {}
        if voice_id:
            updates["tts_voice"] = voice_id
        if caption_style:
            updates["caption_style"] = caption_style
        if target_aspect:
            updates["target_aspect"] = target_aspect
        if updates:
            profile = LocalizationProfile.model_validate({
                **profile.model_dump(),
                **updates,
            })

        if run is None:
            profile = materialize_localization_profile(
                user_settings,
                profile,
                target_language=target_language,
                for_dub=is_dub,
            )
            if is_dub:
                await validate_localization_tts_preflight(
                    user_settings, profile, target_language,
                )
            run = LocalizationRun(
                job_id,
                source_path=str(original_input),
                effective_profile=profile.model_dump(),
                request=request_snapshot,
            )
            source_path = run.ensure_source_copy(original_input)
        else:
            # Preserve the original review choice and downloaded source on resume/rerun unless the
            # caller explicitly supplied one.
            if review_translation is None:
                request_snapshot["review_translation"] = bool(
                    run.request.get("review_translation")
                )
            if auto_repair is None:
                request_snapshot.pop("auto_repair", None)
            if not downloaded_video_id and run.request.get("downloaded_video_id"):
                downloaded_video_id = run.request.get("downloaded_video_id")
                request_snapshot["downloaded_video_id"] = downloaded_video_id
            source_path = run.source_path
            if not source_path.exists():
                raise RuntimeError(
                    "Persisted source media is missing; existing localization "
                    "artifacts cannot be safely resumed. Rerun from separation."
                )
            if profile_override:
                run.update_effective_profile(profile.model_dump())
            if request_snapshot:
                run.update_request({**run.request, **request_snapshot})

        # Source content, not its pathname, is the resume boundary. A file may
        # be overwritten in-place while stale C5/C6 artifacts still exist.
        run.verify_source_identity()
        await _cancel_checkpoint("media processing")

        # Never clean the persisted source snapshot.
        cleanup = [
            path for path in cleanup
            if path.resolve() != source_path.resolve()
        ]

        # ── C8 separation & C5 ASR (GPU gate protected) ───────────────────
        needs_separation = (
            is_dub
            and profile.audio_mode == "hq_clean"
            and not run.is_complete("separation", ("vocals", "background"))
        )
        needs_asr = not run.is_complete("asr", ("transcript",))

        if needs_separation or needs_asr:
            await asyncio.to_thread(
                _localization_source_media_preflight_sync,
                source_path,
            )

        background_file = None
        vocals_file = None
        asr_input = source_path
        transcript = None

        async def _run_gpu_audio_pipeline():
            nonlocal background_file, vocals_file, asr_input, transcript, current_stage
            current_stage = "separation"
            if is_dub and profile.audio_mode == "hq_clean":
                if run.is_complete("separation", ("vocals", "background")):
                    vocals_file = run.artifact("separation", "vocals")
                    background_file = run.artifact("separation", "background")
                else:
                    await _tool_progress(
                        job_id, 6,
                        "Separating vocals/background for HQ clean dub...",
                        user_id,
                    )
                    run.start("separation")
                    try:
                        from backend.services.audio_separation_service import (
                            separate_vocals_background,
                        )
                        # Live Demucs progress across 6→15%. Without this the
                        # whole stage read as frozen at 6% until the model
                        # finished, which looked like a hung job.
                        _sep_forward = _separation_progress_forwarder(
                            job_id, user_id, [SEPARATION_PROGRESS_START],
                        )
                        vocals_file, background_file = await separate_vocals_background(
                            source_path,
                            run.stage_dir("separation"),
                            on_progress=_sep_forward,
                        )
                        run.complete(
                            "separation",
                            artifacts={
                                "vocals": vocals_file,
                                "background": background_file,
                            },
                            metadata={"model": "htdemucs_ft"},
                        )
                    except Exception as exc:
                        run.fail("separation", exc)
                        raise
                asr_input = vocals_file
            elif not run.is_complete("separation"):
                run.mark_complete_without_artifacts(
                    "separation",
                    metadata={"skipped": True, "reason": "clean_separation_not_required"},
                )

            # ── C5 ASR + WhisperX alignment/diarization ─────────────────────
            current_stage = "asr"
            if run.is_complete("asr", ("transcript",)):
                transcript = LocalizationTranscript.model_validate(
                    run.load_json("asr", "transcript")
                )
            else:
                await _tool_progress(
                    job_id, 16,
                    f"Transcribing/alignment with {profile.asr_model or profile.asr_quality}...",
                    user_id,
                )
                run.start("asr")
                try:
                    transcript = await transcribe_for_localization(
                        asr_input,
                        profile,
                        user_settings=user_settings,
                    )
                    path = run.save_json("asr", "transcript.json", transcript.model_dump())
                    run.complete(
                        "asr",
                        artifacts={"transcript": path},
                        metadata=transcript.metadata,
                    )
                except Exception as exc:
                    run.fail("asr", exc)
                    raise

        if needs_separation or needs_asr:
            async with localization_gpu_gate():
                try:
                    await _run_gpu_audio_pipeline()
                finally:
                    # Release unused Demucs/WhisperX allocator memory even when
                    # separation/ASR fails or the task is cancelled. Without a
                    # finally, exactly the failure path that stops a job can
                    # leave multi-GB resident memory pinned in the backend.
                    await asyncio.to_thread(_release_localization_torch_cache_sync)
        else:
            await _run_gpu_audio_pipeline()

        await _cancel_checkpoint("semantic segmentation")
        source_segment_count = len(transcript.segments)

        # ── C6 semantic segmentation ────────────────────────────────────
        current_stage = "semantic"
        if run.is_complete("semantic", ("transcript",)):
            transcript = LocalizationTranscript.model_validate(
                run.load_json("semantic", "transcript")
            )
        else:
            await _tool_progress(job_id, 26, "Đang chia câu/ngữ nghĩa...", user_id)
            run.start("semantic")
            semantic_runtime = {
                "attempts": 0,
                "errors": 0,
                "fallback_provider_used": False,
                "deterministic_fallback": False,
            }

            async def _semantic_progress(event: dict):
                name = event.get("event")
                window = int(event.get("window") or 1)
                total = max(1, int(event.get("total") or 1))
                pct = min(34.0, 26.0 + (window - 1) / total * 8.0)
                role = str(event.get("role") or "primary")
                provider = str(event.get("provider") or "")
                model = str(event.get("model") or "")
                if name == "attempt":
                    semantic_runtime["attempts"] += 1
                    if role.startswith("fallback"):
                        semantic_runtime["fallback_provider_used"] = True
                    await _tool_progress(
                        job_id,
                        pct,
                        (
                            f"Phân đoạn ngữ nghĩa {window}/{total} · "
                            f"{'fallback' if role.startswith('fallback') else 'AI chính'} "
                            f"{provider}/{model} · thử {event.get('attempt')}/{event.get('max_attempts')}"
                        ),
                        user_id,
                    )
                elif name == "error":
                    semantic_runtime["errors"] += 1
                    await _tool_progress(
                        job_id,
                        pct,
                        (
                            f"AI phân đoạn lỗi ở {window}/{total} · "
                            f"{'fallback' if role == 'fallback' else 'AI chính'} · "
                            f"đang xử lý dự phòng..."
                        ),
                        user_id,
                    )
                elif name in {"budget_exhausted", "deterministic_fallback"}:
                    semantic_runtime["deterministic_fallback"] = True
                    await _tool_progress(
                        job_id,
                        pct,
                        "AI phân đoạn không phản hồi · chuyển sang phân đoạn nhanh không AI...",
                        user_id,
                    )

            try:
                transcript = await prepare_semantic_transcript(
                    transcript,
                    profile=profile,
                    user_settings=user_settings,
                    progress_cb=_semantic_progress,
                )
                path = run.save_json(
                    "semantic", "transcript.json", transcript.model_dump()
                )
                run.complete(
                    "semantic",
                    artifacts={"transcript": path},
                    metadata={
                        "semantic_cue_count": len(transcript.segments),
                        "ai_enabled": bool(profile.semantic_segmentation),
                        **semantic_runtime,
                    },
                )
            except Exception as exc:
                run.fail("semantic", exc)
                raise

        await _cancel_checkpoint("translation")

        # ── C6 contextual translation ───────────────────────────────────
        current_stage = "translation"
        if run.is_complete("translation", ("transcript",)):
            localized = LocalizationTranscript.model_validate(
                run.load_json("translation", "transcript")
            )
        else:
            await _tool_progress(
                job_id, 36,
                f"Đang dịch {len(transcript.segments)} câu ngữ nghĩa...",
                user_id,
            )
            run.start("translation")
            translation_runtime = {
                "attempts": 0,
                "errors": 0,
                "fallback_provider_used": False,
            }
            translation_batch = {"current": 1, "total": 1}

            async def _translation_progress(event: dict):
                name = event.get("event")
                if name == "batch":
                    translation_batch["current"] = int(event.get("batch") or 1)
                    translation_batch["total"] = max(1, int(event.get("total_batches") or 1))
                current = translation_batch["current"]
                total = translation_batch["total"]
                pct = min(50.0, 36.0 + (current - 1) / total * 14.0)
                role = str(event.get("role") or "primary")
                provider = str(event.get("provider") or "")
                model = str(event.get("model") or "")
                if name == "attempt":
                    translation_runtime["attempts"] += 1
                    if role.startswith("fallback"):
                        translation_runtime["fallback_provider_used"] = True
                    await _tool_progress(
                        job_id,
                        pct,
                        (
                            f"Dịch {current}/{total} · "
                            f"{'fallback' if role.startswith('fallback') else 'AI chính'} "
                            f"{provider}/{model} · thử {event.get('attempt')}/{event.get('max_attempts')}"
                        ),
                        user_id,
                    )
                elif name == "error":
                    translation_runtime["errors"] += 1
                    await _tool_progress(
                        job_id,
                        pct,
                        (
                            f"AI dịch lỗi · {provider}/{model} · "
                            f"thử {event.get('attempt')}/{event.get('max_attempts')}..."
                        ),
                        user_id,
                    )
                elif name == "retry_wait":
                    await _tool_progress(
                        job_id,
                        pct,
                        f"AI tạm lỗi · chờ {event.get('backoff_s')}s rồi thử lại...",
                        user_id,
                    )
                elif name == "switch_fallback":
                    translation_runtime["fallback_provider_used"] = True
                    await _tool_progress(
                        job_id,
                        pct,
                        f"AI chính không dùng được · chuyển fallback {provider}/{model}...",
                        user_id,
                    )

            try:
                localized = await translate_transcript(
                    transcript,
                    target_language=target_language,
                    profile=profile,
                    user_settings=user_settings,
                    for_dub=is_dub,
                    checkpoint_dir=run.stage_dir("translation"),
                    progress_cb=_translation_progress,
                )
                path = run.save_json(
                    "translation", "transcript.json", localized.model_dump()
                )
                run.complete(
                    "translation",
                    artifacts={"transcript": path},
                    metadata={
                        "translated_count": sum(
                            1 for s in localized.segments
                            if s.translation_status == "success"
                        ),
                        "translation_provider": profile.translation_provider,
                        "translation_model": profile.translation_model,
                        "translation_fallback_provider": profile.translation_fallback_provider,
                        "translation_fallback_model": profile.translation_fallback_model,
                        "translation_preset": profile.translation_preset,
                        "translation_routes": [
                            route.model_dump() for route in profile.translation_routes
                        ],
                        "translation_ai": dict(
                            localized.metadata.get("translation_ai") or {}
                        ),
                        **translation_runtime,
                    },
                )
            except Exception as exc:
                run.record("translation")["metadata"] = {
                    **dict(run.record("translation").get("metadata") or {}),
                    **translation_runtime,
                    "translation_provider": profile.translation_provider,
                    "translation_model": profile.translation_model,
                    "translation_fallback_provider": profile.translation_fallback_provider,
                    "translation_fallback_model": profile.translation_fallback_model,
                    "translation_routes": [
                        route.model_dump() for route in profile.translation_routes
                    ],
                    "ai_failure_chain": list(
                        getattr(exc, "ai_failure_chain", None) or []
                    ),
                    "ai_selected_connection": getattr(
                        exc, "ai_selected_connection", None
                    ),
                    "translation_ai": {
                        "selected_connection_id": getattr(
                            exc, "ai_selected_connection", None
                        ),
                        "route_chain": [
                            route.model_dump() for route in profile.translation_routes
                        ],
                        "failure_chain": list(
                            getattr(exc, "ai_failure_chain", None) or []
                        ),
                        "sticky_route_index": getattr(
                            exc, "ai_sticky_route_index", None
                        ),
                        "attempted": int(
                            getattr(exc, "ai_attempt_count", 0) or 0
                        ) > 0,
                        "attempt_count": int(
                            getattr(exc, "ai_attempt_count", 0) or 0
                        ),
                        "success_count": int(
                            getattr(exc, "ai_success_count", 0) or 0
                        ),
                    },
                }
                run.fail("translation", exc)
                raise

        await _cancel_checkpoint("translation review / dubbing")

        # Optional human quality gate: stop after C6 and expose source/target
        # pairs before spending on TTS/render. The same manifest/job resumes at
        # C7 after approval; no second pipeline or duplicate ASR is created.
        if (
            is_dub
            and bool(run.request.get("review_translation"))
            and not bool(run.request.get("translation_review_approved"))
        ):
            # This is a HUMAN GATE, not a completed tool result. Previously this
            # wrote a review .json through _tool_success(), which marked the job
            # 100% success, emitted job_complete and made Library index the JSON
            # as a finished "Dịch" asset. The workflow then looked dead even
            # though the same persisted run was waiting to resume at C7.
            from backend.agents.job_helper import update_job_status
            from backend.core.ws_manager import ws_manager

            source_id = str(run.request.get("downloaded_video_id") or "")
            run.update_request({
                **run.request,
                "translation_review_pending": True,
                "translation_review_approved": False,
            })
            await update_job_status(
                job_id,
                "awaiting_review",
                progress_pct=50,
                current_step="Chờ duyệt bản dịch",
                output_data={
                    "translation_review_pending": True,
                    "translation_model": profile.translation_model,
                    "translation_preset": profile.translation_preset,
                    "semantic_cue_count": len(localized.segments),
                    "downloaded_video_id": source_id or None,
                },
            )
            action_url = (
                f"/studio/localize?source={source_id}&job={job_id}&step=translation"
                if source_id
                else f"/studio/localize?job={job_id}&step=translation"
            )
            await ws_manager.send({
                "type": "job_attention",
                "job_id": job_id,
                "job_type": "tool:translate",
                "reason": "translation_review",
                "message": "Bản dịch đã xong — cần duyệt trước khi lồng tiếng.",
                "source_id": source_id or None,
                "action_url": action_url,
            }, user_id)
            # The immutable source snapshot lives under localization_jobs/<id>,
            # so ephemeral upload/download scratch can be released at the gate.
            _cleanup_paths(*cleanup)
            logger.info(
                "TASK PAUSED tool:translate | job=%s awaiting translation review",
                job_id[:8],
            )
            return

        timing_issues: list[dict] = []
        video_slowdown = 1.0
        dub_chunk_count = 0
        max_dubbing_speed = 1.0
        max_fitted_dubbing_speed = 1.0
        dubbing_rewrite_count = 0
        dub_track = None

        # ── C7 smart dubbing ────────────────────────────────────────────
        current_stage = "dubbing"
        if is_dub:
            if run.is_complete("dubbing", ("transcript", "dub_track", "plan", "chunks")):
                localized = LocalizationTranscript.model_validate(
                    run.load_json("dubbing", "transcript")
                )
                dub_track = run.artifact("dubbing", "dub_track")
                meta = run.record("dubbing").get("metadata") or {}
                timing_issues = list(meta.get("issues") or [])
                video_slowdown = float(meta.get("video_slowdown") or 1.0)
                dub_chunk_count = int(meta.get("chunk_count") or 0)
                max_dubbing_speed = float(meta.get("max_raw_required_speed") or meta.get("max_speed_factor") or 1.0)
                max_fitted_dubbing_speed = float(meta.get("max_fitted_speed_factor") or 1.0)
                dubbing_rewrite_count = int(meta.get("rewrite_count") or 0)
            else:
                await _tool_progress(
                    job_id, 52,
                    "Planning chunks, synthesizing and fitting HQ dubbing...",
                    user_id,
                )
                run.start("dubbing")
                try:
                    async def _rewrite_progress(event: dict):
                        name = event.get("event")
                        provider = str(event.get("provider") or "")
                        model = str(event.get("model") or "")
                        role = str(event.get("role") or "primary")
                        if name == "attempt":
                            await _tool_progress(
                                job_id,
                                54,
                                (
                                    f"C7 viết lại · {'fallback' if role.startswith('fallback') else 'AI chính'} "
                                    f"{provider}/{model} · thử {event.get('attempt')}/{event.get('max_attempts')}"
                                ),
                                user_id,
                            )
                        elif name == "error":
                            await _tool_progress(
                                job_id,
                                54,
                                (
                                    f"C7 AI lỗi · {provider}/{model} · "
                                    f"thử {event.get('attempt')}/{event.get('max_attempts')}..."
                                ),
                                user_id,
                            )
                        elif name == "retry_wait":
                            await _tool_progress(
                                job_id,
                                54,
                                f"C7 AI tạm lỗi · chờ {event.get('backoff_s')}s rồi thử lại...",
                                user_id,
                            )
                        elif name == "switch_fallback":
                            await _tool_progress(
                                job_id,
                                54,
                                f"C7 chuyển fallback {provider}/{model}...",
                                user_id,
                            )

                    regenerate_chunk_ids = {
                        int(x) for x in (run.request.get("regenerate_chunk_ids") or [])
                    }
                    dub = await synthesize_timed_dub(
                        localized,
                        source_media=source_path,
                        target_language=target_language,
                        profile=profile,
                        user_settings=user_settings,
                        work_dir=run.stage_dir("dubbing"),
                        fail_on_overflow=True,
                        regenerate_chunk_ids=regenerate_chunk_ids,
                        # Safe on normal resume too: C7 validates a synthesis
                        # identity sidecar before reusing any raw chunk.
                        reuse_existing_chunks=True,
                        reference_media=asr_input,
                        progress_cb=_rewrite_progress,
                    )
                    localized = dub.transcript
                    dub_track = dub.dub_track
                    timing_issues = dub.issues
                    video_slowdown = dub.video_slowdown
                    dub_chunk_count = dub.chunk_count
                    max_dubbing_speed = dub.max_speed_factor
                    max_fitted_dubbing_speed = dub.max_fitted_speed_factor
                    dubbing_rewrite_count = dub.rewrite_count
                    transcript_path = run.save_json(
                        "dubbing", "transcript.json", localized.model_dump()
                    )
                    plan_path = run.save_json(
                        "dubbing", "plan.json",
                        [asdict(x) for x in (dub.plan or [])],
                    )
                    chunks_path = run.save_json(
                        "dubbing", "chunks.json",
                        [asdict(x) for x in (dub.chunks or [])],
                    )
                    run.complete(
                        "dubbing",
                        artifacts={
                            "transcript": transcript_path,
                            "dub_track": dub_track,
                            "plan": plan_path,
                            "chunks": chunks_path,
                        },
                        metadata={
                            "issues": timing_issues,
                            "video_slowdown": video_slowdown,
                            "chunk_count": dub_chunk_count,
                            "max_raw_required_speed": max_dubbing_speed,
                            "max_fitted_speed_factor": max_fitted_dubbing_speed,
                            "rewrite_count": dubbing_rewrite_count,
                            "rewrite_provider": profile.rewrite_provider,
                            "rewrite_model": profile.rewrite_model,
                            "rewrite_fallback_provider": profile.rewrite_fallback_provider,
                            "rewrite_fallback_model": profile.rewrite_fallback_model,
                            "rewrite_routes": [
                                route.model_dump() for route in profile.rewrite_routes
                            ],
                            "rewrite_ai": dict(
                                localized.metadata.get("dubbing", {}).get("rewrite_ai") or {}
                            ),
                        },
                    )
                    if dubbing_rewrite_count:
                        translation_base = LocalizationTranscript.model_validate(
                            run.load_json("translation", "transcript")
                        )
                        durable_translation = persist_dubbing_rewrites(
                            translation_base,
                            localized,
                        )
                        translation_path = run.save_json(
                            "translation",
                            "transcript.json",
                            durable_translation.model_dump(),
                        )
                        translation_meta = dict(
                            run.record("translation").get("metadata") or {}
                        )
                        translation_meta["duration_rewrite_count"] = (
                            int(translation_meta.get("duration_rewrite_count") or 0)
                            + dubbing_rewrite_count
                        )
                        run.complete(
                            "translation",
                            artifacts={"transcript": translation_path},
                            metadata=translation_meta,
                        )
                    if regenerate_chunk_ids:
                        run.update_request({
                            **run.request,
                            "regenerate_chunk_ids": [],
                        })
                except Exception as exc:
                    # Timing failures happen after C7 may already have attempted
                    # one or more rewrites. Persist that audit evidence before
                    # marking the stage failed so the UI can prove whether C7
                    # actually ran instead of exposing only the final overflow.
                    if hasattr(exc, "rewrite_count"):
                        failure_issues = list(
                            getattr(exc, "all_issues", None)
                            or getattr(exc, "issues", None)
                            or []
                        )
                        unresolved = list(getattr(exc, "issues", None) or [])
                        run.record("dubbing")["metadata"] = {
                            "issues": failure_issues,
                            "rewrite_count": int(getattr(exc, "rewrite_count", 0) or 0),
                            "rewrite_provider": profile.rewrite_provider,
                            "rewrite_model": profile.rewrite_model,
                            "rewrite_fallback_provider": profile.rewrite_fallback_provider,
                            "rewrite_fallback_model": profile.rewrite_fallback_model,
                            "rewrite_routes": [
                                route.model_dump() for route in profile.rewrite_routes
                            ],
                            "rewrite_ai": dict(
                                getattr(exc, "rewrite_ai", None) or {}
                            ),
                            "max_raw_required_speed": max(
                                [1.0, *[
                                    float(item.get("required_speed") or 1.0)
                                    for item in unresolved
                                    if isinstance(item, dict)
                                ]],
                            ),
                            "failed_before_fit": True,
                        }
                    run.fail("dubbing", exc)
                    raise
        elif not run.is_complete("dubbing"):
            run.mark_complete_without_artifacts(
                "dubbing", metadata={"skipped": True, "mode": mode}
            )

        await _cancel_checkpoint("audio mix")

        # ── C8 clean audio mix; still no video encode ───────────────────
        current_stage = "audio_mix"
        mixed_audio = None
        if is_dub:
            if run.is_complete("audio_mix", ("mixed_audio",)):
                mixed_audio = run.artifact("audio_mix", "mixed_audio")
            else:
                await _tool_progress(job_id, 70, "Mixing clean localized audio...", user_id)
                run.start("audio_mix")
                try:
                    mixed_audio = run.artifact_path("audio_mix", "mixed_audio.wav")
                    await mix_dub_audio(
                        source_path,
                        dub_track,
                        mixed_audio,
                        profile,
                        background_file=background_file,
                        video_slowdown=video_slowdown,
                    )
                    run.complete(
                        "audio_mix",
                        artifacts={"mixed_audio": mixed_audio},
                        metadata={
                            "audio_mode": profile.audio_mode,
                            "video_slowdown": video_slowdown,
                        },
                    )
                except Exception as exc:
                    run.fail("audio_mix", exc)
                    raise
        elif not run.is_complete("audio_mix"):
            run.mark_complete_without_artifacts(
                "audio_mix", metadata={"skipped": True, "mode": mode}
            )

        await _cancel_checkpoint("final render")

        # ── C8 one and only lossy visual encode ─────────────────────────
        current_stage = "final_render"
        from backend.services.caption_service import generate_captions_ass, burn_captions

        render_segments = caption_segments_from_localization(localized)
        if not render_segments:
            raise RuntimeError("Localization produced no captionable target segments.")

        effective_aspect = profile.target_aspect
        if run.is_complete("final_render", ("final_video",)):
            # A completed stage may point either at a legacy tools/out delivery
            # or at the new workspace candidate. Do not copy it over the last
            # successful delivery yet; QC and terminal validation still have to
            # succeed for THIS attempt.
            out_path = run.artifact("final_render", "final_video")
        else:
            async with localization_gpu_gate():
                await _tool_progress(job_id, 84, "Rendering final localized video...", user_id)
                run.start("final_render")
                try:
                    caption_aspect = (
                        effective_aspect
                        if effective_aspect and effective_aspect != "source"
                        else await _probe_aspect_ratio(source_path)
                    )
                    ass_path = run.artifact_path("final_render", "captions.ass")
                    await generate_captions_ass(
                        render_segments,
                        style=profile.caption_style,
                        aspect_ratio=caption_aspect,
                        emoji_style=emoji_style,
                        output_path=ass_path,
                        mask_box=mask_box,
                        caption_placement=caption_placement,
                    )
                    # The render candidate remains isolated inside the durable
                    # workspace until QC AND the generic output validator pass.
                    # This is the key last-known-good boundary.
                    candidate_path = run.artifact_path("final_render", "candidate.mp4")
                    rendered = await burn_captions(
                        source_path,
                        ass_path,
                        candidate_path,
                        render_profile=profile.render_profile,
                        target_aspect=effective_aspect,
                        reframe_method="auto",
                        external_audio=mixed_audio,
                        video_slowdown=video_slowdown if is_dub else 1.0,
                        mask_box=mask_box,
                    )
                    if rendered != candidate_path or not candidate_path.exists():
                        raise RuntimeError("Final localization render failed.")
                    out_path = candidate_path
                    run.complete(
                        "final_render",
                        artifacts={
                            "final_video": candidate_path,
                            "captions": ass_path,
                        },
                        metadata={
                            "video_slowdown": video_slowdown if is_dub else 1.0,
                            "render_profile": profile.render_profile,
                        },
                    )
                except Exception as exc:
                    run.fail("final_render", exc)
                    raise

        await _cancel_checkpoint("quality checks")

        # ── C10 automatic QC + exception gate ───────────────────────────
        current_stage = "qc"
        if run.is_complete("qc", ("report",)):
            qc = run.load_json("qc", "report")
        else:
            await _tool_progress(job_id, 94, "Running localization quality checks...", user_id)
            run.start("qc")
            try:
                qc_report = await asyncio.to_thread(
                    evaluate_localization_qc,
                    source_video=source_path,
                    final_video=out_path,
                    transcript=localized,
                    profile=profile,
                    video_slowdown=video_slowdown if is_dub else 1.0,
                    timing_issues=timing_issues,
                    check_tts_quality=is_dub,
                )
                qc = qc_report.as_dict()
                report_path = run.save_json("qc", "report.json", qc)
                run.complete(
                    "qc",
                    artifacts={"report": report_path},
                    metadata={"status": qc["status"]},
                )
            except Exception as exc:
                run.fail("qc", exc)
                raise

        await _cancel_checkpoint("completion")

        from backend.services.video_utils import probe_duration
        src_dur = await asyncio.to_thread(probe_duration, source_path)
        final_dur = await asyncio.to_thread(probe_duration, out_path)

        # Deliver each newly rendered version to an immutable path. The Job row
        # keeps pointing at the PREVIOUS successful file until _tool_success()
        # accepts this exact version, so a QC/probe/validation/cancellation
        # failure cannot erase or overwrite the last-known-good artifact.
        from uuid import uuid4
        delivery_path = out_path
        delivery_is_new = False
        same_as_previous = bool(
            previous_output_path is not None
            and out_path.resolve() == previous_output_path.resolve()
        )
        if not same_as_previous:
            delivery_path = tool_out_path(
                f"{job_id}_{uuid4().hex[:10]}",
                ".mp4",
            )
            staged_delivery = delivery_path.with_suffix(
                delivery_path.suffix + ".next"
            )
            staged_delivery.unlink(missing_ok=True)
            shutil.copy2(out_path, staged_delivery)
            staged_delivery.replace(delivery_path)
            delivery_is_new = True

        output_meta = {
            "target_language": target_language,
            "mode": mode,
            "localization_profile": profile.id,
            "localization_manifest": str(run.manifest_path),
            "asr_quality": profile.asr_quality,
            "asr_model": profile.asr_model,
            "alignment_mode": profile.alignment_mode,
            "diarization_mode": profile.diarization_mode,
            "render_profile": profile.render_profile,
            "audio_mode": profile.audio_mode if is_dub else None,
            "source_asr_segment_count": source_segment_count,
            "semantic_cue_count": len(localized.segments),
            "dubbing_chunk_count": dub_chunk_count,
            "max_dubbing_speed_factor": max_dubbing_speed,
            "max_fitted_dubbing_speed_factor": max_fitted_dubbing_speed,
            "dubbing_rewrite_count": dubbing_rewrite_count,
            "translation_provider": profile.translation_provider,
            "translation_model": profile.translation_model,
            "translation_preset": profile.translation_preset,
            "rewrite_provider": profile.rewrite_provider if is_dub else None,
            "rewrite_model": profile.rewrite_model if is_dub else None,
            "video_slowdown": video_slowdown,
            "timing_issue_count": len(timing_issues),
            "qc_status": qc.get("status"),
            "qc_failures": qc.get("failures", []),
            "qc_warnings": qc.get("warnings", []),
            "qc_human_approved": bool(run.request.get("qc_human_approved")),
            "source_duration": src_dur,
            "final_duration": final_dur,
            "duration_seconds": final_dur,
            "target_aspect": effective_aspect,
            "downloaded_video_id": downloaded_video_id,
            "duration_ratio": round(final_dur / src_dur, 4) if src_dur > 0 else 1.0,
            "localization_delivery_version": delivery_path.stem,
        }
        if previous_generated_video_id:
            output_meta["supersedes_generated_video_id"] = (
                previous_generated_video_id
            )

        delivered = await _tool_success(
            job_id,
            delivery_path,
            cleanup,
            user_id,
            extra_output=output_meta,
        )
        if not delivered:
            if delivery_is_new:
                delivery_path.unlink(missing_ok=True)
            return

        # The manifest may have pointed at a workspace candidate. Once the
        # terminal delivery is accepted, bind final_render to the immutable
        # delivered file so later QC-only retries do not need a duplicate copy.
        final_record = run.record("final_render")
        final_meta = dict(final_record.get("metadata") or {})
        final_artifacts = {"final_video": delivery_path}
        captions_path = run.artifact("final_render", "captions")
        if captions_path is not None:
            final_artifacts["captions"] = captions_path
        run.complete(
            "final_render",
            artifacts=final_artifacts,
            metadata=final_meta,
        )
        if (
            out_path.resolve() != delivery_path.resolve()
            and out_path.resolve().is_relative_to(run.root.resolve())
        ):
            out_path.unlink(missing_ok=True)

        # P1 handoff: only QC-ready (or explicitly human-acknowledged warning)
        # outputs become GeneratedVideo rows consumed by Publish. FAILED and
        # unreviewed NEEDS_REVIEW outputs remain reviewable Library job assets.
        try:
            from backend.services.localization_publish_service import (
                promote_localization_output,
            )
            promotion = await promote_localization_output(
                job_id,
                existing_generated_video_id=previous_generated_video_id,
                previous_output_path=previous_output_path,
                expected_output_path=delivery_path,
            )
        except Exception as promotion_error:
            logger.error(
                "Localization output is valid but Publish registration failed "
                "for job %s: %s",
                job_id[:8],
                promotion_error,
                exc_info=True,
            )
            from backend.database import AsyncSessionLocal
            from backend.models.job import Job
            async with AsyncSessionLocal() as db:
                job_row = await db.get(Job, job_id)
                if job_row is not None:
                    try:
                        persisted = _json.loads(job_row.output_json or "{}")
                    except (TypeError, ValueError):
                        persisted = {}
                    if not isinstance(persisted, dict):
                        persisted = {}
                    persisted["publish_registration_error"] = str(
                        promotion_error
                    )[:1000]
                    job_row.output_json = _json.dumps(persisted)
                    await db.commit()
            promotion = {"promoted": False}

        logger.info(
            "TASK DONE tool:translate | job=%s qc=%s publish=%s",
            job_id[:8],
            qc.get("status"),
            "ready" if promotion.get("publish_ready") else "blocked",
        )
    except Exception as e:
        # DELETE /jobs/{id} only flips persisted state; long media calls cannot
        # always be interrupted mid-frame. Once control returns to the runner,
        # cancellation wins: no auto-repair, no job_failed event, no late
        # success, and no extra expensive downstream stage.
        if isinstance(e, JobCancelledError) or await job_cancelled(job_id):
            _cleanup_paths(*cleanup)
            logger.info(
                "TASK CANCELLED tool:translate | job=%s stage=%s",
                job_id[:8], current_stage,
            )
            return

        if run is not None:
            try:
                run.fail(current_stage, e)
            except Exception:
                logger.debug("Could not record localization stage failure", exc_info=True)

        # Optional bounded self-repair. This never changes provider/model/voice
        # or any other user configuration. It only retries the exact failed
        # stage once, preserving every completed upstream artifact. Dubbing
        # timing failures are narrower still: only the overflowing chunks are
        # marked for regeneration and raw non-target chunk files are retained.
        request = run.request if run is not None else {}
        repair_enabled = bool(request.get("auto_repair"))
        attempts = dict(request.get("auto_repair_attempts") or {})
        stage = current_stage if current_stage in {
            "separation", "asr", "semantic", "translation",
            "dubbing", "audio_mix", "final_render", "qc",
        } else (run.first_incomplete_stage() if run is not None else None)
        message = str(e or "").lower()
        error_name = type(e).__name__.lower()
        deterministic_markers = (
            "api key", "lacks permission", "permission denied",
            "configure that tts provider", "unsupported language",
            "requires reference audio", "requires a base url",
            "matches no streams", "no audio", "source media has changed",
            "source identity", "missing or inaccessible",
        )
        cancelled = "cancel" in error_name
        deterministic = any(marker in message for marker in deterministic_markers)
        attempt_count = int(attempts.get(stage, 0) or 0) if stage else 0

        if (
            run is not None
            and repair_enabled
            and stage
            and not cancelled
            and not deterministic
            and attempt_count < 1
        ):
            attempts[stage] = attempt_count + 1
            repair_request = {
                **run.request,
                "auto_repair_attempts": attempts,
                "last_auto_repair": {
                    "stage": stage,
                    "attempt": attempts[stage],
                    "error": str(e)[:500],
                },
            }
            delete_artifacts = True
            preserve_files: set[str] = set()
            if stage == "translation":
                preserve_files = {"checkpoint.json"}
            if stage == "dubbing" and hasattr(e, "issues"):
                chunk_ids = sorted({
                    int(item.get("chunk_id"))
                    for item in (getattr(e, "issues", None) or [])
                    if isinstance(item, dict) and item.get("chunk_id") is not None
                })
                if chunk_ids:
                    repair_request["regenerate_chunk_ids"] = chunk_ids
                    delete_artifacts = False

            run.update_request(repair_request)
            run.invalidate_from(
                stage,
                delete_artifacts=delete_artifacts,
                preserve_files=preserve_files,
            )
            await _tool_progress(
                job_id,
                max(1.0, float({
                    "separation": 5, "asr": 15, "semantic": 25, "translation": 35,
                    "dubbing": 51, "audio_mix": 69, "final_render": 83, "qc": 93,
                }.get(stage, 1))),
                f"Tự động sửa: thử lại đúng bước {stage} (1/1)...",
                user_id,
            )
            await run_tool_translate(
                job_id,
                run.source_path,
                str(run.request.get("target_language") or target_language),
                str(run.request.get("mode") or mode),
                str(run.request.get("voice_id") or voice_id or ""),
                str(run.effective_profile.get("caption_style") or caption_style or "viral"),
                str(run.request.get("emoji_style") or emoji_style or "moderate"),
                user_id=user_id,
                target_aspect=run.effective_profile.get("target_aspect") or run.request.get("target_aspect"),
                profile_id=run.request.get("profile_id"),
                profile_override=run.effective_profile,
                review_translation=bool(run.request.get("review_translation")),
                cleanup_input=False,
                mask_box=run.request.get("mask_box"),
                caption_placement=run.request.get("caption_placement"),
                downloaded_video_id=run.request.get("downloaded_video_id"),
                auto_repair=None,
            )
            return

        await _tool_fail(job_id, e, cleanup, user_id, "translate")


# ── Hook analysis (Whisper first 10s → AI score) ───────────────────────────

_HOOK_PROMPT = """You are a short-form video hook expert. Analyze the opening
of this video based on its first ~10 seconds of spoken transcript.

TRANSCRIPT (first 10s):
{transcript}

Return STRICT JSON only — no markdown fences:
{{
  "score": <integer 1-10>,
  "verdict": "<one short sentence on why it works or doesn't>",
  "issues": ["<problem 1>", "<problem 2>"],
  "suggested_hooks": ["<rewritten opening 1>", "<rewritten opening 2>", "<rewritten opening 3>"]
}}"""


async def run_tool_hook_analysis(job_id: str, in_path: Path, user_id: str = "local"):
    """Analyze the opening 10s of a video and return a structured hook score +
    improvement suggestions in the job's output_data (no downloadable file)."""
    import json as _json
    from backend.agents.job_helper import update_job_status
    from backend.core.ws_manager import ws_manager

    logger.info("TASK START tool:hook_analysis | job=%s", job_id[:8])
    cleanup = [in_path]
    clip_audio = None
    try:
        from backend.core.ai_provider import get_ai_client

        user_settings = await _load_user_settings(user_id)

        await _tool_progress(job_id, 25, "Extracting opening audio...", user_id)
        clip_audio = in_path.with_suffix(".hook.mp3")
        cleanup.append(clip_audio)

        def _extract():
            cmd = [
                "ffmpeg", "-y", "-i", str(in_path),
                "-t", "10", "-vn", "-c:a", "libmp3lame", "-q:a", "4",
                str(clip_audio),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode != 0:
                raise RuntimeError(f"Opening-audio extract failed: {res.stderr[:400]}")
        await asyncio.to_thread(_extract)

        await _tool_progress(job_id, 45, "Transcribing opening audio...", user_id)
        from backend.services.whisper_service import whisper_service
        tx = await whisper_service.transcribe(str(clip_audio))
        transcript = (tx.get("text") or "").strip()
        if not transcript:
            raise RuntimeError("No speech detected in the first 10 seconds.")

        await _tool_progress(job_id, 70, "Asking AI to score the hook...", user_id)
        ai = get_ai_client(user_settings)
        raw = await ai.chat(
            messages=[{"role": "user", "content": _HOOK_PROMPT.format(transcript=transcript[:2000])}],
            max_tokens=700,
        )
        text = _strip_json_fence(raw)
        try:
            result = _json.loads(text)
        except _json.JSONDecodeError:
            raise RuntimeError(f"AI returned malformed JSON: {text[:200]!r}")

        await update_job_status(
            job_id, "success", progress_pct=100, current_step="Done",
            output_data=result,
        )
        await ws_manager.send({"type": "job_complete", "job_id": job_id, "result": result}, user_id)
        _cleanup_paths(*cleanup)
        logger.info("TASK DONE  tool:hook_analysis | job=%s score=%s", job_id[:8], result.get("score"))
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "hook_analysis")


# ── Voiceover (Edge TTS, free) ─────────────────────────────────────────────

async def run_tool_voiceover(
    job_id: str, text: str, voice_id: str,
    video_path: Path | None = None, user_id: str = "local",
    provider: str = "edge_tts",
):
    """Narrate a script, optionally over an uploaded video.

    `provider` is honoured rather than ignored. The page has always offered
    both providers, and this runner used to call Edge TTS unconditionally with
    whatever `voice_id` arrived — so picking OpenAI handed Edge an id it does
    not know ("alloy"), which edge_tts rejects with a bare ValueError and the
    whole job failed. A paid provider with no key degrades to Edge and SAYS
    so, the same rule the Smart Video pipeline follows: a render that speaks
    in a voice the user did not pick must never be silent about it.
    """
    from backend.api.tools import tool_out_path
    from backend.config import settings
    from backend.services.tts_service import (
        DEFAULT_VOICES, PROVIDER_INFO, TTSProvider, generate_tts)

    logger.info("TASK START tool:voiceover | job=%s chars=%d", job_id[:8], len(text))
    cleanup: list[Path] = []
    if video_path:
        cleanup.append(video_path)
    try:
        try:
            tts_provider = TTSProvider(provider or "edge_tts")
        except ValueError:
            tts_provider = TTSProvider.EDGE_TTS

        api_key = settings.OPENAI_API_KEY if tts_provider == TTSProvider.OPENAI_TTS else ""
        if PROVIDER_INFO[tts_provider]["needs_key"] and not api_key:
            from backend.core.ws_manager import ws_manager
            await ws_manager.send_constraint_warning(
                constraint="tts_fallback",
                severity="warning",
                message=(
                    f"{PROVIDER_INFO[tts_provider]['label']} needs an API key — "
                    f"add one in Settings to use it. Narrated with the free "
                    f"Edge voice instead."
                ),
            )
            tts_provider = TTSProvider.EDGE_TTS
            voice_id = ""   # an OpenAI voice id is not an Edge voice id

        label = PROVIDER_INFO[tts_provider]["label"]
        await _tool_progress(job_id, 30, f"Generating voice ({label})...", user_id)

        mp3_path = tool_out_path(job_id, ".mp3")
        await generate_tts(
            text,
            provider=tts_provider,
            voice_id=voice_id or DEFAULT_VOICES[tts_provider],
            api_key=api_key,
            output_path=mp3_path,
        )

        out_path = mp3_path
        if video_path and video_path.exists():
            await _tool_progress(job_id, 75, "Overlaying voice on video...", user_id)
            from backend.services.ffmpeg_service import add_audio_to_video
            out_path = tool_out_path(job_id, ".mp4")
            await add_audio_to_video(video_path, mp3_path, out_path)
            cleanup.append(mp3_path)

        await _tool_success(job_id, out_path, cleanup, user_id)
        logger.info("TASK DONE  tool:voiceover | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "voiceover")


# ── Transform (flip / rotate / loop / volume) ──────────────────────────────

async def run_tool_transform(
    job_id: str, in_path: Path, operation: str, amount: str = "", user_id: str = "local",
):
    """Quick local FFmpeg transforms: flip / rotate / loop / volume / mute."""
    from backend.api.tools import tool_out_path

    logger.info("TASK START tool:transform | job=%s op=%s", job_id[:8], operation)
    cleanup = [in_path]
    try:
        out_path = tool_out_path(job_id, ".mp4")

        # "Remove audio" on a video that never had any is a no-op that looks
        # like a broken tool — you get a file back and nothing changed. Say so
        # instead. Still produce the output: the user's goal (a video with no
        # sound) is already true, so failing would be wrong.
        if operation == "mute":
            from backend.core.ws_manager import ws_manager
            from backend.services.ffmpeg_service import has_audio_stream
            if not await has_audio_stream(in_path):
                await ws_manager.send_constraint_warning(
                    constraint="mute_no_audio", severity="info",
                    message=(
                        "This video had no audio track to begin with — the "
                        "result is the same video, unchanged."
                    ),
                    user_id=user_id,
                )

        def _run():
            base = ["ffmpeg", "-y"]
            if operation == "mute":
                # Strip every audio track outright. Measured against the
                # `volume=0` it replaces in the UI (ffmpeg 7.1): volume=0
                # leaves a SILENT AAC stream in the file — it re-encodes the
                # audio to silence rather than removing it, so the bytes and
                # the stream are both still there. `-an` drops it, and
                # skipping the AAC pass makes this near-instant on a long
                # clip. Video is stream-copied either way.
                cmd = base + ["-i", str(in_path), "-c:v", "copy", "-an",
                              "-movflags", "+faststart", str(out_path)]
            elif operation == "loop":
                try:
                    n = max(2, min(20, int(float(amount or "2"))))
                except (ValueError, TypeError):
                    n = 2
                cmd = base + ["-stream_loop", str(n - 1), "-i", str(in_path),
                              "-c", "copy", str(out_path)]
            elif operation == "volume":
                try:
                    vol = max(0.0, min(8.0, float(amount)))
                except (ValueError, TypeError):
                    vol = 1.0
                cmd = base + ["-i", str(in_path), "-af", f"volume={vol}",
                              "-c:v", "copy", "-c:a", "aac", str(out_path)]
            else:
                vf = {
                    "flip_h": "hflip",
                    "flip_v": "vflip",
                    "rotate_cw": "transpose=1",
                    "rotate_ccw": "transpose=2",
                    "rotate_180": "transpose=2,transpose=2",
                }.get(operation)
                if not vf:
                    raise RuntimeError(f"Unknown transform operation: {operation}")
                cmd = base + ["-i", str(in_path), "-vf", vf,
                              "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                              "-pix_fmt", "yuv420p", "-c:a", "copy", str(out_path)]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if res.returncode != 0:
                raise RuntimeError(f"Transform failed: {res.stderr[:400]}")

        await _tool_progress(job_id, 40, "Processing...", user_id)
        await asyncio.to_thread(_run)
        await _tool_success(job_id, out_path, cleanup, user_id)
        logger.info("TASK DONE  tool:transform | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "transform")


# ── Music visualizer ───────────────────────────────────────────────────────

_VIZ_PALETTES = {
    "sunset": {"bg": "0x1A0E2E", "c": "0xFFD93D"},
    "ocean":  {"bg": "0x081428", "c": "0x4FC3F7"},
    "neon":   {"bg": "0x0A0A1E", "c": "0xFF2ED1"},
    "mono":   {"bg": "0x111111", "c": "0xFFFFFF"},
}
_VIZ_DIMS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}


async def run_tool_music_visualizer(
    job_id: str, audio_path: Path,
    style: str = "waves", palette: str = "sunset", aspect: str = "9:16",
    user_id: str = "local",
):
    """Turn an audio file into an animated visualizer video synced to the
    audio. Pure local FFmpeg."""
    from backend.api.tools import tool_out_path
    from backend.services.video_utils import probe_duration

    logger.info("TASK START tool:music_visualizer | job=%s style=%s", job_id[:8], style)
    cleanup: list[Path] = [audio_path]
    try:
        pal = _VIZ_PALETTES.get(palette, _VIZ_PALETTES["sunset"])
        W, H = _VIZ_DIMS.get(aspect, _VIZ_DIMS["9:16"])
        dur = probe_duration(audio_path, default=0.0)
        if dur <= 0:
            raise RuntimeError("Could not read the audio file")
        out_path = tool_out_path(job_id, ".mp4")

        await _tool_progress(job_id, 40, "Rendering visualizer...", user_id)

        def _render():
            if style == "bars":
                fc = f"[0:a]showcqt=s={W}x{H}:axis=0,format=yuv420p[v]"
                inputs = ["-i", str(audio_path)]
                amap = "0:a"
            elif style == "spectrum":
                fc = (f"[0:a]showspectrum=s={W}x{H}:mode=combined:color=intensity:"
                      f"scale=cbrt:slide=scroll,format=yuv420p[v]")
                inputs = ["-i", str(audio_path)]
                amap = "0:a"
            else:  # waves
                wave_h = max(160, H // 4)
                fc = (
                    f"[1:a]showwaves=s={W}x{wave_h}:mode=cline:colors={pal['c']}|{pal['c']},"
                    f"format=rgba,colorkey=0x000000:0.30:0.10[w];"
                    f"[0:v][w]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
                )
                inputs = ["-f", "lavfi", "-i", f"color=c={pal['bg']}:s={W}x{H}:r=30",
                          "-i", str(audio_path)]
                amap = "1:a"
            cmd = (
                ["ffmpeg", "-y"] + inputs +
                ["-filter_complex", fc, "-map", "[v]", "-map", amap,
                 "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out_path)]
            )
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if res.returncode != 0:
                raise RuntimeError(f"Visualizer render failed: {res.stderr[:400]}")
        await asyncio.to_thread(_render)

        await _tool_success(job_id, out_path, cleanup, user_id)
        logger.info("TASK DONE  tool:music_visualizer | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "music_visualizer")


# ── GIF (two-pass palette) ─────────────────────────────────────────────────

async def run_tool_gif(
    job_id: str, in_path: Path,
    fps: int = 15, width: int = 540,
    start_seconds: float = 0.0, duration_seconds: float = 0.0,
    user_id: str = "local",
):
    """Convert video → animated GIF with two-pass palette generation."""
    logger.info("TASK START tool:gif | job=%s", job_id[:8])
    cleanup = [in_path]
    palette_path = None
    try:
        from backend.api.tools import tool_out_path

        await _tool_progress(job_id, 15, "Building color palette...", user_id)

        trim_args: list[str] = []
        if start_seconds > 0:
            trim_args.extend(["-ss", f"{start_seconds:.2f}"])
        if duration_seconds > 0:
            trim_args.extend(["-t", f"{duration_seconds:.2f}"])

        scale_fps = f"fps={fps},scale={width}:-1:flags=lanczos"
        palette_path = tool_out_path(job_id, "_palette.png")
        cleanup.append(palette_path)

        def _palette():
            cmd = (
                ["ffmpeg", "-y"] + trim_args + ["-i", str(in_path),
                 "-vf", f"{scale_fps},palettegen=stats_mode=diff",
                 str(palette_path)]
            )
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode != 0:
                raise RuntimeError(f"GIF palette failed: {res.stderr[:400]}")
        await asyncio.to_thread(_palette)

        await _tool_progress(job_id, 60, "Encoding GIF...", user_id)
        out_path = tool_out_path(job_id, ".gif")

        def _encode():
            cmd = (
                ["ffmpeg", "-y"] + trim_args + ["-i", str(in_path),
                 "-i", str(palette_path),
                 "-lavfi", f"{scale_fps} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5",
                 str(out_path)]
            )
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if res.returncode != 0:
                raise RuntimeError(f"GIF encode failed: {res.stderr[:400]}")
        await asyncio.to_thread(_encode)

        await _tool_success(job_id, out_path, cleanup, user_id)
        logger.info("TASK DONE  tool:gif | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "gif")


# ── Speed ──────────────────────────────────────────────────────────────────

def _build_speed_filters(speed: float, keep_pitch: bool) -> tuple[str, str]:
    """Build (video_filter, audio_filter) for the ffmpeg speed-change. Pure
    function so it's unit-testable without invoking ffmpeg."""
    v_filter = f"setpts={1.0 / speed:.6f}*PTS"
    if not keep_pitch:
        a_filter = f"asetrate=44100*{speed:.6f},aresample=44100"
        return v_filter, a_filter
    remaining = speed
    parts: list[str] = []
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return v_filter, ",".join(parts)


async def run_tool_speed(
    job_id: str, in_path: Path,
    speed: float = 1.5, keep_pitch: bool = True, user_id: str = "local",
):
    """Apply a video/audio speed change via FFmpeg setpts + atempo."""
    logger.info("TASK START tool:speed | job=%s speed=%s pitch=%s", job_id[:8], speed, keep_pitch)
    cleanup = [in_path]
    try:
        from backend.api.tools import tool_out_path

        await _tool_progress(job_id, 20, f"Re-encoding at {speed}x speed...", user_id)
        v_filter, a_filter = _build_speed_filters(speed, keep_pitch)
        out_path = tool_out_path(job_id, ".mp4")

        def _encode():
            cmd = [
                "ffmpeg", "-y", "-i", str(in_path),
                "-filter:v", v_filter,
                "-filter:a", a_filter,
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "128k",
                str(out_path),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if res.returncode != 0:
                raise RuntimeError(f"Speed re-encode failed: {res.stderr[:400]}")
        await asyncio.to_thread(_encode)

        await _tool_success(job_id, out_path, cleanup, user_id)
        logger.info("TASK DONE  tool:speed | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "speed")


# ── Metadata (AI: title / description / tags / caption) ────────────────────

_METADATA_PROMPT = """Generate SEO-optimized metadata for a short-form video.

{input_block}

Return STRICT JSON only — no markdown fences:
{{
  "youtube_title": "<= 70 chars, click-worthy",
  "youtube_description": "<2-3 sentence description with a call to action>",
  "tags": ["<tag1>", "<tag2>", "... up to 12 relevant tags"],
  "tiktok_caption": "<short punchy caption with 3-5 hashtags>"
}}"""


async def run_tool_metadata(
    job_id: str, text: str = "", topic: str = "",
    in_path: Path | None = None, user_id: str = "local",
):
    """Generate YT + TikTok metadata from a transcript / video / topic."""
    import json as _json
    logger.info("TASK START tool:metadata | job=%s mode=%s",
                job_id[:8], "file" if in_path else ("text" if text else "topic"))
    cleanup: list[Path] = []
    if in_path:
        cleanup.append(in_path)

    try:
        from backend.api.tools import tool_out_path
        from backend.core.ai_provider import get_ai_client

        if in_path:
            await _tool_progress(job_id, 20, "Transcribing audio (~30-60s on CPU)...", user_id)
            from backend.services.whisper_service import whisper_service
            tx = await whisper_service.transcribe(str(in_path))
            transcript = (tx.get("text") or "").strip()
            if not transcript:
                raise RuntimeError(
                    "No speech detected — try a video with clear audio or "
                    "switch to the text/topic input mode."
                )
            input_block = f"VIDEO TRANSCRIPT:\n{transcript[:8000]}"
        elif text:
            input_block = f"SCRIPT / TRANSCRIPT:\n{text[:8000]}"
        else:
            input_block = f"TOPIC:\n{topic}"

        await _tool_progress(job_id, 60, "Generating metadata...", user_id)
        prompt = _METADATA_PROMPT.format(input_block=input_block)

        user_settings = await _load_user_settings(user_id)
        ai = get_ai_client(user_settings)
        raw = await ai.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=900,
        )

        out_text = _strip_json_fence(raw)
        try:
            parsed = _json.loads(out_text)
        except _json.JSONDecodeError:
            raise RuntimeError(f"AI returned malformed JSON: {out_text[:200]!r}")

        out_path = tool_out_path(job_id, ".json")
        out_path.write_text(_json.dumps(parsed, indent=2), encoding="utf-8")

        await _tool_success(job_id, out_path, cleanup, user_id, extra_output={"metadata": parsed})
        logger.info("TASK DONE  tool:metadata | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "metadata")


# ── Auto chapters (Whisper segments → AI clusters) ─────────────────────────

_CHAPTERS_PROMPT = """Generate YouTube chapter markers from this transcript.

Transcript segments (each line is `[MM:SS] text`):
{segments_text}

Total video duration: {duration_label}

TASK
Group the segments into {target_label} chapters. Each chapter must:
  - Start at a natural topic boundary (not mid-sentence)
  - Have a 2-6 word title that's specific to that section's content
  - First chapter MUST start at 00:00 (YouTube requirement)
  - Chapters must be ordered by start time, ascending
  - Each chapter must be at least 30 seconds long

Return STRICT JSON only — no markdown fences:
{{
  "chapters": [
    {{"start_seconds": 0, "title": "Intro"}},
    {{"start_seconds": 47, "title": "The Hook"}}
  ]
}}"""


def _format_chapters_text(chapters: list[dict]) -> str:
    """Render a chapter list into the `MM:SS Title` plain text format YouTube
    accepts when pasted into a video description."""
    lines = []
    for ch in chapters:
        secs = max(0, int(ch.get("start_seconds", 0)))
        mm, ss = divmod(secs, 60)
        hh = 0
        if mm >= 60:
            hh, mm = divmod(mm, 60)
        stamp = f"{hh:01d}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"
        title = str(ch.get("title", "")).strip() or "Chapter"
        lines.append(f"{stamp} {title}")
    return "\n".join(lines) + ("\n" if lines else "")


async def run_tool_auto_chapters(
    job_id: str, in_path: Path, target_count: int = 0, user_id: str = "local",
):
    """Whisper-transcribe → AI clusters into chapters → write a `MM:SS Title`
    text file ready to paste into a YT description."""
    import json as _json
    logger.info("TASK START tool:auto_chapters | job=%s target=%d", job_id[:8], target_count)
    cleanup = [in_path]

    try:
        from backend.api.tools import tool_out_path
        from backend.core.ai_provider import get_ai_client
        from backend.services.whisper_service import whisper_service

        await _tool_progress(job_id, 20, "Transcribing audio (~30-60s on CPU)...", user_id)
        tx = await whisper_service.transcribe(str(in_path))
        segments = tx.get("segments") or []
        if not segments:
            raise RuntimeError(
                "No speech detected — auto-chapters needs a transcript. "
                "Make sure the audio is intelligible and try again."
            )

        duration = max((s.get("end") or 0) for s in segments) or sum(
            (s.get("end", 0) - s.get("start", 0)) for s in segments
        )
        duration = max(int(duration), 0)
        mm_total, ss_total = divmod(duration, 60)
        duration_label = f"{mm_total}m {ss_total}s"

        if target_count <= 0:
            target_count = max(3, min(15, duration // 150))
        target_label = f"exactly {target_count}"

        lines: list[str] = []
        for s in segments[:600]:
            t = (s.get("text") or "").strip()
            if not t:
                continue
            start = int(s.get("start", 0))
            mm, ss = divmod(start, 60)
            lines.append(f"[{mm:02d}:{ss:02d}] {t}")
        segments_text = "\n".join(lines)

        await _tool_progress(job_id, 70, "Clustering chapters...", user_id)
        prompt = _CHAPTERS_PROMPT.format(
            segments_text=segments_text,
            duration_label=duration_label,
            target_label=target_label,
        )

        user_settings = await _load_user_settings(user_id)
        ai = get_ai_client(user_settings)
        raw = await ai.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
        )

        text = _strip_json_fence(raw)
        try:
            parsed = _json.loads(text)
            chapters = parsed.get("chapters", []) if isinstance(parsed, dict) else []
        except _json.JSONDecodeError:
            raise RuntimeError(f"AI returned malformed JSON: {text[:200]!r}")

        valid: list[dict] = []
        for ch in chapters:
            try:
                start = int(ch.get("start_seconds", 0))
            except (TypeError, ValueError):
                continue
            if start < 0 or start > duration:
                continue
            valid.append({"start_seconds": start, "title": str(ch.get("title", "")).strip()})
        valid.sort(key=lambda c: c["start_seconds"])
        if not valid:
            raise RuntimeError("AI produced no usable chapters")
        if valid[0]["start_seconds"] != 0:
            valid.insert(0, {"start_seconds": 0, "title": valid[0]["title"] or "Intro"})

        plain_text = _format_chapters_text(valid)
        out_path = tool_out_path(job_id, ".txt")
        out_path.write_text(plain_text, encoding="utf-8")

        await _tool_success(
            job_id, out_path, cleanup, user_id,
            extra_output={"chapters": valid, "count": len(valid)},
        )
        logger.info("TASK DONE  tool:auto_chapters | job=%s chapters=%d", job_id[:8], len(valid))
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "auto_chapters")


# ── Trim ───────────────────────────────────────────────────────────────────

async def run_tool_trim(
    job_id: str, in_path: Path,
    start_seconds: float, end_seconds: float, user_id: str = "local",
):
    """Keep the [start, end] segment of a video, dropping the rest. Pure
    FFmpeg via the shared `extract_clip` helper (vertical=False preserves the
    source aspect)."""
    logger.info("TASK START tool:trim | job=%s %.2f-%.2f", job_id[:8], start_seconds, end_seconds)
    cleanup = [in_path]
    try:
        from backend.api.tools import tool_out_path
        from backend.services.ffmpeg_service import extract_clip
        from backend.services.video_utils import probe_duration

        await _tool_progress(job_id, 20, "Trimming...", user_id)
        duration = await asyncio.to_thread(probe_duration, in_path, 0.0)
        end = end_seconds
        if duration > 0:
            end = min(end_seconds, duration)
        if end - start_seconds < 0.5:
            raise ValueError(
                f"Trim range too short ({end - start_seconds:.2f}s) — need at least 0.5s."
            )

        out_path = tool_out_path(job_id, ".mp4")
        await extract_clip(in_path, float(start_seconds), float(end), out_path, vertical=False)
        if not out_path.exists():
            raise RuntimeError("Trim produced no output")

        await _tool_success(
            job_id, out_path, cleanup, user_id,
            extra_output={"start_seconds": float(start_seconds), "end_seconds": float(end)},
        )
        logger.info("TASK DONE  tool:trim | job=%s", job_id[:8])
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "trim")


# ── Subtitles (Whisper → .srt / .vtt / .txt) ───────────────────────────────

def _format_ts(seconds: float, vtt: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    hh = int(seconds // 3600)
    mm = int((seconds % 3600) // 60)
    ss = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    sep = "." if vtt else ","
    return f"{hh:02d}:{mm:02d}:{ss:02d}{sep}{ms:03d}"


def _build_subtitle_file(segments: list[dict], fmt: str, out_path: Path):
    """Write Whisper segments out as SRT / VTT / TXT."""
    if fmt == "txt":
        text = " ".join((s.get("text") or "").strip() for s in segments).strip()
        out_path.write_text(text + "\n", encoding="utf-8")
        return out_path

    vtt = fmt == "vtt"
    lines: list[str] = []
    if vtt:
        lines.append("WEBVTT\n")
    for i, s in enumerate(segments, 1):
        start = _format_ts(s.get("start", 0), vtt=vtt)
        end = _format_ts(s.get("end", 0), vtt=vtt)
        body = (s.get("text") or "").strip()
        if not vtt:
            lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(body)
        lines.append("")
    out_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return out_path


async def run_tool_subtitles(
    job_id: str, in_path: Path, fmt: str = "srt", user_id: str = "local",
):
    """Transcribe a video with Whisper and emit a downloadable subtitle file
    (SRT / VTT) or a plain transcript (TXT). No burn-in."""
    logger.info("TASK START tool:subtitles | job=%s fmt=%s", job_id[:8], fmt)
    cleanup = [in_path]
    try:
        from backend.api.tools import tool_out_path
        from backend.services.whisper_service import whisper_service

        await _tool_progress(job_id, 20, "Transcribing audio (~30-60s on CPU)...", user_id)
        tx = await whisper_service.transcribe(str(in_path))
        segments = tx.get("segments") or []
        if not segments:
            raise ValueError("No speech detected — nothing to transcribe.")

        await _tool_progress(job_id, 80, f"Writing .{fmt} file...", user_id)
        out_path = tool_out_path(job_id, f".{fmt}")
        await asyncio.to_thread(_build_subtitle_file, segments, fmt, out_path)

        await _tool_success(
            job_id, out_path, cleanup, user_id,
            extra_output={"format": fmt, "segment_count": len(segments)},
        )
        logger.info("TASK DONE  tool:subtitles | job=%s fmt=%s segs=%d", job_id[:8], fmt, len(segments))
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "subtitles")


# ── Auto-zoom (Whisper words → zoom pulses) ────────────────────────────────

async def run_tool_auto_zoom(
    job_id: str, in_path: Path,
    zoom_factor: float = 1.15, words_per_group: int = 3, user_id: str = "local",
):
    """Add subtle zoom "punch-in" pulses on spoken words for energy. Whisper
    word timings drive the pulses; the shared `apply_auto_zoom` helper does the
    FFmpeg zoompan work."""
    from backend.core.ws_manager import ws_manager
    import shutil

    logger.info(
        "TASK START tool:auto_zoom | job=%s zoom=%.2f wpg=%d",
        job_id[:8], zoom_factor, words_per_group,
    )
    cleanup = [in_path]
    noop = False
    try:
        from backend.api.tools import tool_out_path
        from backend.services.whisper_service import whisper_service
        from backend.services.ffmpeg_service import apply_auto_zoom

        await _tool_progress(job_id, 20, "Transcribing audio (~30-60s on CPU)...", user_id)
        tx = await whisper_service.transcribe(str(in_path))
        segments = tx.get("segments") or []
        words: list[dict] = []
        for seg in segments:
            for w in seg.get("words") or []:
                token = w.get("word") or w.get("text")
                if token and w.get("start") is not None:
                    words.append({
                        "text": token,
                        "start": float(w.get("start", 0)),
                        "end": float(w.get("end", 0)),
                    })

        out_path = tool_out_path(job_id, ".mp4")
        if not words:
            noop = True
            await asyncio.to_thread(shutil.copy2, str(in_path), str(out_path))
            await ws_manager.send_constraint_warning(
                constraint="auto_zoom_noop",
                message="No clear speech detected — returned unchanged (zoom pulses need word timings).",
                severity="info",
                user_id=user_id,
            )
        else:
            await _tool_progress(job_id, 55, f"Applying zoom pulses to {len(words)} words...", user_id)
            result_path = await apply_auto_zoom(
                in_path, words, output_path=out_path,
                zoom_factor=float(zoom_factor), words_per_group=int(words_per_group),
            )
            if result_path != out_path and not out_path.exists():
                await asyncio.to_thread(shutil.copy2, str(in_path), str(out_path))

        await _tool_success(job_id, out_path, cleanup, user_id)
        logger.info("TASK DONE  tool:auto_zoom | job=%s words=%d noop=%s", job_id[:8], len(words), noop)
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "auto_zoom")


# ── Video compressor (free, local ffmpeg) ──────────────────────────────────


# level → (CRF, audio bitrate). CRF is the quality knob libx264 actually
# respects; higher = smaller + softer. The five steps are spaced ~3-4 CRF
# apart because a smaller gap is not visible and a bigger one skips a useful
# trade. Audio rides along: a 32-CRF video next to a 192k track is wasted
# bytes, and a 18-CRF video next to a 64k track sounds broken.
_COMPRESS_LEVELS: dict[str, tuple[int, str]] = {
    "maximum": (32, "64k"),
    "high":    (28, "96k"),
    "medium":  (24, "128k"),
    "low":     (21, "160k"),
    "minimal": (18, "192k"),
}

# label → target height. Width is derived from the source aspect so we never
# distort; "original" keeps the source resolution and only re-encodes.
_COMPRESS_HEIGHTS: dict[str, int] = {
    "original": 0,
    "1280x720": 720,
    "854x480":  480,
    "640x360":  360,
    "426x240":  240,
}


def compress_settings(level: str) -> tuple[int, str]:
    """(crf, audio_bitrate) for a compression level. Unknown → the default.

    Pure so the mapping is testable without invoking ffmpeg — same reason
    `_build_speed_filters` is pure.
    """
    return _COMPRESS_LEVELS.get((level or "").lower(), _COMPRESS_LEVELS["high"])


def compress_scale_filter(
    src_w: int, src_h: int, resolution: str,
) -> tuple[str, bool]:
    """Build the scale filter for a compress run.

    Returns ``(vf, downscaled)``. `vf` is "" when no scaling should happen —
    either the caller asked for "original", the source dimensions are unknown,
    or the requested height is TALLER than the source.

    That last case is the one worth naming: picking "HD 720p" for a 480p clip
    must not re-encode it up to 720p. Upscaling invents pixels, makes the file
    BIGGER, and the user asked to compress. We keep the source size and report
    `downscaled=False` so the runner can say so.

    Height is the axis because these presets are named by height and the
    sources are a mix of portrait and landscape; width comes from `-2`, which
    both preserves aspect and rounds to an even number (libx264 rejects odd
    dimensions).
    """
    target_h = _COMPRESS_HEIGHTS.get(resolution or "original", 0)
    if not target_h or src_h <= 0:
        return "", False
    if target_h >= src_h:
        return "", False
    return f"scale=-2:{target_h}:flags=lanczos", True


async def run_tool_compress(
    job_id: str,
    in_path: Path,
    resolution: str = "original",
    level: str = "high",
    user_id: str = "local",
):
    """Re-encode a video smaller — for email, chat apps and upload limits.

    Single-pass CRF rather than two-pass bitrate targeting: CRF holds a
    consistent *quality* across the whole clip, which is what "make this
    smaller without it looking bad" actually means. Two-pass only wins when
    you must hit an exact byte budget, and we don't promise one — quoting a
    predicted file size we can't honour on a high-motion clip would be worse
    than quoting nothing.
    """
    from backend.api.tools import tool_out_path
    from backend.core.ws_manager import ws_manager
    from backend.services.video_utils import probe_media

    logger.info(
        "TASK START tool:compress | job=%s res=%s level=%s",
        job_id[:8], resolution, level,
    )
    cleanup = [in_path]
    try:
        in_bytes = in_path.stat().st_size if in_path.exists() else 0
        src_w, src_h, _dur = await asyncio.to_thread(probe_media, in_path)
        crf, audio_bitrate = compress_settings(level)
        vf, downscaled = compress_scale_filter(src_w, src_h, resolution)

        if resolution != "original" and not downscaled and src_h > 0:
            await ws_manager.send_constraint_warning(
                constraint="compress_upscale", severity="info",
                message=(
                    f"This video is already {src_w}×{src_h} — kept its size "
                    f"rather than scaling up to {resolution}, which would have "
                    "made the file bigger."
                ),
                user_id=user_id,
            )

        out_path = tool_out_path(job_id, ".mp4")

        def _encode():
            cmd = ["ffmpeg", "-y", "-i", str(in_path)]
            if vf:
                cmd += ["-vf", vf]
            cmd += [
                "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", audio_bitrate,
                # Puts the moov atom up front so the result streams/scrubs
                # immediately instead of needing a full download first.
                "-movflags", "+faststart",
                str(out_path),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if res.returncode != 0:
                raise RuntimeError(f"Compression failed: {res.stderr[-400:]}")

        await _tool_progress(job_id, 25, "Compressing...", user_id)
        await asyncio.to_thread(_encode)

        out_bytes = out_path.stat().st_size if out_path.exists() else 0
        # An already-compressed source can come out BIGGER. That's a real
        # outcome, not a failure — report both numbers and let the user judge
        # rather than silently handing back a worse file or erroring on work
        # that did exactly what was asked.
        if out_bytes and in_bytes and out_bytes >= in_bytes:
            await ws_manager.send_constraint_warning(
                constraint="compress_no_gain", severity="warning",
                message=(
                    "This video was already well compressed — the result "
                    f"({out_bytes // 1024} KB) isn't smaller than the original "
                    f"({in_bytes // 1024} KB). Try a lower resolution or a "
                    "stronger level."
                ),
                user_id=user_id,
            )

        saved_pct = (
            round((1 - out_bytes / in_bytes) * 100, 1)
            if in_bytes and out_bytes else 0.0
        )
        await _tool_success(
            job_id, out_path, cleanup, user_id,
            extra_output={
                "input_bytes": in_bytes,
                "output_bytes": out_bytes,
                "saved_pct": saved_pct,
                "resolution": resolution,
                "scaled": downscaled,
                "level": level,
            },
        )
        logger.info(
            "TASK DONE  tool:compress | job=%s %d→%d bytes (%.1f%%)",
            job_id[:8], in_bytes, out_bytes, saved_pct,
        )
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "compress")


# ── Video cropper (free, local ffmpeg) ─────────────────────────────────────


# Below this many pixels on either axis there's no video left to speak of, and
# libx264 starts refusing sizes outright. Rejecting here gives a sentence the
# user can act on instead of an ffmpeg stderr dump.
CROP_MIN_PIXELS = 16


def crop_rect(
    src_w: int, src_h: int, x: float, y: float, w: float, h: float,
) -> tuple[int, int, int, int]:
    """Normalized crop box (0-1, origin top-left) → ffmpeg's (w, h, x, y) px.

    Pure, so the arithmetic that decides what the user actually gets is
    testable without ffmpeg.

    Three things happen here that all matter:

    * **Clamp into the frame.** A normalized box is user input arriving over
      HTTP. An out-of-range crop is an ffmpeg error, not a message anyone can
      read, so the box is pinned inside the source rather than trusted.
    * **Round every value to even.** libx264 rejects odd dimensions outright,
      and with yuv420p chroma an odd OFFSET shifts colour against luma — so
      offsets are rounded too, not just sizes.
    * **Re-clamp after rounding.** Rounding a size up can push `x + w` one
      pixel past the edge; ffmpeg fails on that, and it only shows up on
      certain source sizes, which is the worst kind of bug to ship.

    Raises ValueError when the box is too small to be a video.
    """
    if src_w <= 0 or src_h <= 0:
        raise ValueError("Couldn't read this video's dimensions to crop it.")

    x = min(max(float(x), 0.0), 1.0)
    y = min(max(float(y), 0.0), 1.0)
    w = min(max(float(w), 0.0), 1.0 - x)
    h = min(max(float(h), 0.0), 1.0 - y)

    cx = int(round(src_w * x)) & ~1
    cy = int(round(src_h * y)) & ~1
    cw = int(round(src_w * w)) & ~1
    ch = int(round(src_h * h)) & ~1

    # Rounding can overshoot the edge — pull back inside, then re-even.
    cw = min(cw, src_w - cx) & ~1
    ch = min(ch, src_h - cy) & ~1

    if cw < CROP_MIN_PIXELS or ch < CROP_MIN_PIXELS:
        raise ValueError(
            "That crop area is too small — drag a larger box over the frame."
        )
    return cw, ch, cx, cy


async def run_tool_crop(
    job_id: str,
    in_path: Path,
    x: float, y: float, w: float, h: float,
    user_id: str = "local",
):
    """Crop a video to a rectangle. Local FFmpeg only.

    The manual counterpart to Reframe: Reframe decides the framing for you;
    this one does exactly what you drew and nothing else. Audio is
    stream-copied — cropping is a video-only operation and re-encoding the
    track would cost quality for no reason.
    """
    from backend.api.tools import tool_out_path
    from backend.services.video_utils import probe_media

    logger.info("TASK START tool:crop | job=%s box=%.3f,%.3f,%.3f,%.3f",
                job_id[:8], x, y, w, h)
    cleanup = [in_path]
    try:
        src_w, src_h, _dur = await asyncio.to_thread(probe_media, in_path)
        cw, ch, cx, cy = crop_rect(src_w, src_h, x, y, w, h)

        out_path = tool_out_path(job_id, ".mp4")

        def _encode():
            cmd = [
                "ffmpeg", "-y", "-i", str(in_path),
                "-vf", f"crop={cw}:{ch}:{cx}:{cy}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p",
                # Copy, don't re-encode: a crop doesn't touch the audio.
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(out_path),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if res.returncode != 0:
                raise RuntimeError(f"Crop failed: {res.stderr[-400:]}")

        await _tool_progress(job_id, 30, "Cropping...", user_id)
        await asyncio.to_thread(_encode)

        await _tool_success(
            job_id, out_path, cleanup, user_id,
            extra_output={
                "source_width": src_w, "source_height": src_h,
                "width": cw, "height": ch, "x": cx, "y": cy,
            },
        )
        logger.info("TASK DONE  tool:crop | job=%s %dx%d → %dx%d at %d,%d",
                    job_id[:8], src_w, src_h, cw, ch, cx, cy)
    except Exception as e:
        await _tool_fail(job_id, e, cleanup, user_id, "crop")
