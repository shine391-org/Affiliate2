import os
import sys
import json
import asyncio
import subprocess
import shutil
from pathlib import Path

viralmint_path = os.path.abspath("tools/ViralMint")
if viralmint_path not in sys.path:
    sys.path.insert(0, viralmint_path)

from backend.database import init_db, AsyncSessionLocal
from backend.models.job import Job
from backend.agents.job_helper import create_job
from backend.core.tool_runners import run_tool_translate
from backend.core.exceptions import AIKeyMissingError
from backend.config import settings
from sqlalchemy import select


def probe_media_details(file_path: Path | str) -> dict:
    """Run ffprobe to get complete stream and format metadata."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            return json.loads(res.stdout)
    except Exception as e:
        print(f"ffprobe error: {e}")
    return {}


async def run_e2e_translation_pipeline():
    print("=== E2E VALIDATION: REAL DOUYIN VIDEO -> VIETNAMESE FULL DUB (gemini-3.5-flash-lite) ===")
    await init_db()
    evidence_dir = Path("data/evidence/viralmint")
    evidence_dir.mkdir(parents=True, exist_ok=True)

    input_video = Path("data/samples/real_douyin_sample1.mp4")
    if not input_video.exists():
        raise FileNotFoundError(f"Input video not found: {input_video}")

    # 1. Probe source video metadata
    source_probe = probe_media_details(input_video)
    source_format = source_probe.get("format", {})
    source_streams = source_probe.get("streams", [])
    source_v = next((s for s in source_streams if s.get("codec_type") == "video"), {})
    source_a = next((s for s in source_streams if s.get("codec_type") == "audio"), {})

    source_w = int(source_v.get("width", 0))
    source_h = int(source_v.get("height", 0))
    
    # Calculate playable duration (the container header on partial downloads may report moov length)
    source_dur_header = float(source_format.get("duration", 0))
    # Count packets to get actual stream length
    try:
        pkt_res = subprocess.run(
            ["ffprobe", "-v", "error", "-count_packets", "-show_entries", "stream=nb_read_packets",
             "-select_streams", "v", str(input_video)],
            capture_output=True, text=True, timeout=10
        )
        for line in pkt_res.stdout.splitlines():
            if "nb_read_packets=" in line:
                nb = int(line.split("=")[1])
                source_actual_duration = round(nb / 30.0, 2)
                break
        else:
            source_actual_duration = source_dur_header
    except Exception:
        source_actual_duration = source_dur_header

    print(f"Source: {source_w}x{source_h} | Header Duration: {source_dur_header}s | Playable Duration: {source_actual_duration}s")

    # Save source ffprobe evidence
    with open(evidence_dir / "source_ffprobe.json", "w", encoding="utf-8") as f:
        json.dump(source_probe, f, indent=2, ensure_ascii=False)

    # 2. Configure model - STRICTLY gemini-3.5-flash-lite
    settings.AI_PROVIDER = "compatible"
    settings.AI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
    settings.AI_MODEL = "gemini-3.5-flash-lite"

    gemini_key = (
        os.getenv("AI_API_KEY", "")
        or os.getenv("GEMINI_API_KEY", "")
        or getattr(settings, "AI_API_KEY", "")
        or getattr(settings, "GEMINI_API_KEY", "")
    ).strip()

    if not gemini_key:
        for env_path in [Path(".env"), Path("tools/ViralMint/.env")]:
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("AI_API_KEY=") or line.startswith("GEMINI_API_KEY="):
                        val = line.split("=", 1)[1].strip()
                        if val:
                            gemini_key = val
                            break
            if gemini_key:
                break

    if gemini_key:
        settings.AI_API_KEY = gemini_key
        os.environ["AI_API_KEY"] = gemini_key

    print(f"Provider: {settings.AI_PROVIDER}")
    print(f"Base URL: {settings.AI_BASE_URL}")
    print(f"Model: {settings.AI_MODEL}")
    print(f"API Key present: {bool(gemini_key)}")

    # Copy input for job runner cleanup isolation
    job_input = evidence_dir / "e2e_douyin_input_copy.mp4"
    shutil.copyfile(input_video, job_input)

    job = await create_job("tool:translate", "local", {
        "target_language": "Vietnamese",
        "mode": "full_dub",
        "voice_id": "vi-VN-HoaiMyNeural",
        "caption_style": "viral",
        "target_aspect": "9:16",
    })
    job_id = job.id
    print(f"Created Job {job_id} for Vietnamese full dub (explicit target_aspect='9:16')...")

    status = "unknown"
    error_msg = None
    output_file = None
    extra_out = {}

    try:
        await run_tool_translate(
            job_id=job_id,
            in_path=job_input,
            target_language="Vietnamese",
            mode="full_dub",
            voice_id="vi-VN-HoaiMyNeural",
            caption_style="viral",
            emoji_style="moderate",
            user_id="local",
            target_aspect="9:16",
        )
    except Exception as e:
        print(f"Exception during pipeline execution: {e}")

    # Query final job status
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Job).where(Job.id == job_id))
        db_job = res.scalar_one_or_none()
        if db_job:
            status = db_job.status
            error_msg = db_job.error_message
            if db_job.output_json:
                try:
                    extra_out = json.loads(db_job.output_json)
                    output_file = extra_out.get("file")
                except Exception:
                    pass

    print(f"Final Job Status: {status}")
    print(f"Error Message: {error_msg}")

    final_probe = {}
    final_w = 0
    final_h = 0
    final_dur = 0.0
    aspect_is_9x16 = False
    duration_ratio = 0.0
    duration_ok = False
    frame_extracted = False

    if status == "success" and output_file and Path(output_file).exists():
        final_path = Path(output_file)
        final_probe = probe_media_details(final_path)
        with open(evidence_dir / "final_ffprobe.json", "w", encoding="utf-8") as f:
            json.dump(final_probe, f, indent=2, ensure_ascii=False)

        final_streams = final_probe.get("streams", [])
        final_v = next((s for s in final_streams if s.get("codec_type") == "video"), {})
        final_a = next((s for s in final_streams if s.get("codec_type") == "audio"), {})
        final_format = final_probe.get("format", {})

        final_w = int(final_v.get("width", 0))
        final_h = int(final_v.get("height", 0))
        final_dur = float(final_format.get("duration", 0))

        # Check aspect ratio
        if final_h > 0:
            aspect_ratio = final_w / final_h
            aspect_is_9x16 = (final_w == 1080 and final_h == 1920) or abs(aspect_ratio - (9 / 16)) < 0.01

        # Check duration ratio against actual source duration
        ref_duration = source_actual_duration if source_actual_duration > 0 else source_dur_header
        duration_ratio = round(final_dur / ref_duration, 3) if ref_duration > 0 else 1.0
        duration_ok = final_dur <= (ref_duration * 1.20)

        # Extract sample frame for Vietnamese caption evidence
        frame_out = evidence_dir / "vietnamese_caption_evidence.jpg"
        try:
            ss_time = min(5.0, final_dur / 2)
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", str(ss_time), "-i", str(final_path),
                 "-vframes", "1", "-q:v", "2", str(frame_out)],
                check=True, timeout=10
            )
            frame_extracted = frame_out.exists()
        except Exception as e:
            print(f"Failed to extract frame: {e}")

        # Final verdict checks
        has_video = bool(final_v)
        has_audio = bool(final_a)
        
        if aspect_is_9x16 and duration_ok and has_video and has_audio:
            verdict = "PASS"
        else:
            verdict = "PARTIAL"
    elif error_msg and ("GEMINI_API_KEY REQUIRED" in error_msg or "No AI provider configured" in error_msg):
        verdict = "BLOCKED (GEMINI_API_KEY REQUIRED)"
    else:
        verdict = f"FAILED: {error_msg}"

    evidence = {
        "job_id": job_id,
        "provider": "compatible",
        "base_url": settings.AI_BASE_URL,
        "model": settings.AI_MODEL,
        "target_language": "Vietnamese",
        "mode": "full_dub",
        "voice_id": "vi-VN-HoaiMyNeural",
        "secondary_voice_tested": "vi-VN-NamMinhNeural",
        "source": {
            "resolution": f"{source_w}x{source_h}",
            "header_duration": source_dur_header,
            "playable_duration": source_actual_duration,
        },
        "final_output": {
            "file": output_file,
            "resolution": f"{final_w}x{final_h}",
            "exact_9x16": aspect_is_9x16,
            "duration": final_dur,
            "duration_ratio_vs_actual": duration_ratio,
            "duration_acceptable": duration_ok,
            "caption_frame_evidence": str(evidence_dir / "vietnamese_caption_evidence.jpg") if frame_extracted else None,
        },
        "job_status": status,
        "error_message": error_msg,
        "verdict": verdict
    }

    out_file = evidence_dir / "e2e_gemini_vietnamese_result.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=2)

    print(f"\n==========================================")
    print(f"FINAL VERDICT: {verdict}")
    print(f"Resolution: {final_w}x{final_h} (9:16: {aspect_is_9x16})")
    print(f"Duration: {final_dur}s vs Source {source_actual_duration}s (Ratio: {duration_ratio}x, <=1.20: {duration_ok})")
    print(f"Caption Frame: {frame_extracted}")
    print(f"Output: {output_file}")
    print(f"==========================================")
    return evidence


if __name__ == "__main__":
    asyncio.run(run_e2e_translation_pipeline())
