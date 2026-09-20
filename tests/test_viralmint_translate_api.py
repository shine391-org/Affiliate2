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
from sqlalchemy import select

async def test_translate_vietnamese():
    print("=== TESTING TRANSLATE API WITH TARGET_LANGUAGE=Vietnamese ===")
    await init_db()
    evidence_dir = Path("data/evidence/viralmint")
    evidence_dir.mkdir(parents=True, exist_ok=True)

    input_video = Path("data/samples/real_douyin_sample1.mp4")
    assert input_video.exists(), f"Input video not found: {input_video}"

    job = await create_job("tool:translate", "local", {
        "target_language": "Vietnamese",
        "mode": "captions_only",
        "caption_style": "viral"
    })
    job_id = job.id
    print(f"Created Job {job_id} for tool:translate...")

    # Run the translation pipeline directly
    await run_tool_translate(
        job_id=job_id,
        in_path=input_video,
        target_language="Vietnamese",
        mode="captions_only",
        voice_id=None,
        caption_style="viral",
        emoji_style="moderate",
        user_id="local"
    )

    # Check resulting job status in SQLite
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Job).where(Job.id == job_id))
        db_job = res.scalar_one_or_none()
        status = db_job.status if db_job else "unknown"
        error_msg = db_job.error_message if db_job else "unknown"

    print(f"\nResulting Job Status: {status}")
    print(f"Error Message: {error_msg}")

    result_data = {
        "job_id": job_id,
        "input_video": str(input_video),
        "target_language": "Vietnamese",
        "mode": "captions_only",
        "job_status": status,
        "error_message": error_msg,
        "exact_verdict": "AI PROVIDER REQUIRED" if "No AI provider configured" in (error_msg or "") else status
    }

    out_file = evidence_dir / "translate_vietnamese_test_result.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    print(f"\nFinal Verdict: {result_data['exact_verdict']}")
    print(f"Saved evidence to {out_file}")
    return result_data

if __name__ == "__main__":
    asyncio.run(test_translate_vietnamese())
