import os
import sys
import json
import time
import asyncio
from pathlib import Path

viralmint_path = os.path.abspath("tools/ViralMint")
if viralmint_path not in sys.path:
    sys.path.insert(0, viralmint_path)

from backend.database import init_db, AsyncSessionLocal
from backend.models.downloaded_video import DownloadedVideo
from backend.services.whisper_service import whisper_service
from backend.services.caption_service import generate_captions_ass, burn_captions
from backend.services.ffmpeg_service import convert_aspect_ratio
from backend.config import settings

async def test_offline_capabilities():
    print("=== TESTING VIRALMINT OFFLINE CAPABILITIES (NO AI KEY) ===")
    await init_db()
    
    test_video = Path("data/samples/bilibili_sample1.mp4")
    assert test_video.exists(), f"Missing test video: {test_video}"
    
    results = {}
    
    # 1. Test Whisper transcription
    print("\n1. Testing Whisper transcription (local faster-whisper)...")
    try:
        t0 = time.time()
        tx = await whisper_service.transcribe(str(test_video), quality="fast")
        t_cost = round(time.time() - t0, 2)
        segments = tx.get("segments", [])
        text = tx.get("text", "")
        results["whisper_transcription"] = {
            "verdict": "PASS",
            "latency_s": t_cost,
            "segments_count": len(segments),
            "text_preview": text[:100]
        }
        print(f"  -> PASS: Transcribed {len(segments)} segments in {t_cost}s. Text: '{text[:60]}...'")
    except Exception as e:
        results["whisper_transcription"] = {"verdict": "FAIL", "error": str(e)}
        print(f"  -> FAIL: {e}")

    # 2. Test FFmpeg render (9:16 vertical convert_aspect_ratio)
    print("\n2. Testing FFmpeg render (convert_aspect_ratio 9:16)...")
    out_render = Path("data/evidence/viralmint/renders/test_offline_render_9x16.mp4")
    try:
        t0 = time.time()
        res_path = await convert_aspect_ratio(test_video, target_aspect="9:16", method="blur_fill", output_path=out_render)
        r_cost = round(time.time() - t0, 2)
        results["ffmpeg_render"] = {
            "verdict": "PASS",
            "latency_s": r_cost,
            "out_file": str(res_path),
            "size_bytes": res_path.stat().st_size
        }
        print(f"  -> PASS: Rendered 9:16 in {r_cost}s ({res_path.stat().st_size} bytes)")
    except Exception as e:
        results["ffmpeg_render"] = {"verdict": "FAIL", "error": str(e)}
        print(f"  -> FAIL: {e}")

    # 3. Test Captions (ASS generation + Burn-in)
    print("\n3. Testing Captions (ASS subtitle generation + burn-in)...")
    out_captioned = Path("data/evidence/viralmint/renders/test_offline_captioned.mp4")
    try:
        t0 = time.time()
        ass_path = await generate_captions_ass(
            segments[:5],
            style="viral",
            aspect_ratio="9:16",
            emoji_style="moderate"
        )
        # Use relative path to avoid FFmpeg filter space-escaping bug in Windows paths
        rel_ass = Path(os.path.relpath(ass_path, os.getcwd()))
        await burn_captions(out_render, rel_ass, out_captioned)
        c_cost = round(time.time() - t0, 2)
        results["captions"] = {
            "verdict": "PASS",
            "latency_s": c_cost,
            "ass_file": str(ass_path),
            "captioned_video": str(out_captioned),
            "size_bytes": out_captioned.stat().st_size
        }
        print(f"  -> PASS: Generated ASS and burned captions in {c_cost}s ({out_captioned.stat().st_size} bytes)")
    except Exception as e:
        results["captions"] = {"verdict": "FAIL", "error": str(e)}
        print(f"  -> FAIL: {e}")


    # 4. Test Local Media Import (SQLite storage)
    print("\n4. Testing Local Media Import (SQLite + local storage)...")
    try:
        async with AsyncSessionLocal() as db:
            dv = DownloadedVideo(
                user_id="local",
                title="Test Offline Import",
                platform="local",
                video_path=str(test_video),
                duration_seconds=12.0
            )
            db.add(dv)
            await db.commit()
            await db.refresh(dv)
            saved_id = dv.id

        results["local_media_import"] = {
            "verdict": "PASS",
            "saved_id": saved_id,
            "storage_path": str(test_video)
        }
        print(f"  -> PASS: Inserted record into SQLite downloaded_videos with ID {saved_id}")
    except Exception as e:
        results["local_media_import"] = {"verdict": "FAIL", "error": str(e)}
        print(f"  -> FAIL: {e}")

    summary_file = Path("data/evidence/viralmint/offline_capabilities_summary.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nSaved offline capability results to {summary_file}")
    return results

if __name__ == "__main__":
    asyncio.run(test_offline_capabilities())
