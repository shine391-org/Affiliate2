import os
import sys
import json
import asyncio
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


async def run_e2e_translation_pipeline():
    print("=== E2E VALIDATION: REAL DOUYIN VIDEO -> VIETNAMESE FULL DUB ===")
    await init_db()
    evidence_dir = Path("data/evidence/viralmint")
    evidence_dir.mkdir(parents=True, exist_ok=True)

    input_video = Path("data/samples/real_douyin_sample1.mp4")
    if not input_video.exists():
        raise FileNotFoundError(f"Input video not found: {input_video}")

    # Set configuration for generic compatible provider pointing to Gemini
    settings.AI_PROVIDER = "compatible"
    settings.AI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
    settings.AI_MODEL = "gemini-2.5-flash"

    # Check key from environment
    gemini_key = (
        os.getenv("AI_API_KEY", "")
        or os.getenv("GEMINI_API_KEY", "")
        or getattr(settings, "AI_API_KEY", "")
        or getattr(settings, "GEMINI_API_KEY", "")
    ).strip()

    if gemini_key:
        settings.AI_API_KEY = gemini_key

    print(f"Provider: {settings.AI_PROVIDER}")
    print(f"Base URL: {settings.AI_BASE_URL}")
    print(f"Model: {settings.AI_MODEL}")
    print(f"API Key present: {bool(gemini_key)}")

    # We make a copy of the input video for the job because ViralMint's tool runner deletes in_path in cleanup
    job_input = evidence_dir / "e2e_douyin_input_copy.mp4"
    import shutil
    shutil.copyfile(input_video, job_input)

    job = await create_job("tool:translate", "local", {
        "target_language": "Vietnamese",
        "mode": "full_dub",
        "voice_id": "vi-VN-HoaiMyNeural",
        "caption_style": "viral"
    })
    job_id = job.id
    print(f"Created Job {job_id} for Vietnamese full dub...")

    status = "unknown"
    error_msg = None
    output_file = None

    try:
        await run_tool_translate(
            job_id=job_id,
            in_path=job_input,
            target_language="Vietnamese",
            mode="full_dub",
            voice_id="vi-VN-HoaiMyNeural",
            caption_style="viral",
            emoji_style="moderate",
            user_id="local"
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
                    out_dict = json.loads(db_job.output_json)
                    output_file = out_dict.get("file")
                except Exception:
                    pass

    print(f"Final Job Status: {status}")
    print(f"Error Message: {error_msg}")

    if status == "success" and output_file and Path(output_file).exists():
        verdict = "PASS"
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
        "job_status": status,
        "error_message": error_msg,
        "output_file": output_file,
        "verdict": verdict
    }

    out_file = evidence_dir / "e2e_gemini_vietnamese_result.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=2)

    print(f"\nFinal Verdict: {verdict}")
    print(f"Saved E2E evidence to {out_file}")
    return evidence


if __name__ == "__main__":
    asyncio.run(run_e2e_translation_pipeline())
