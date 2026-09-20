import asyncio
import edge_tts
from pathlib import Path

async def test_voices():
    out_dir = Path("data/evidence/viralmint/tts")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    text = "Xin chào, đây là bài kiểm tra giọng đọc tiếng Việt của Edge TTS trên hệ thống."
    voices = ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]
    results = {}

    for voice in voices:
        out_file = out_dir / f"test_{voice}.mp3"
        print(f"Testing Edge-TTS voice: {voice}...")
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(out_file))
        size = out_file.stat().st_size
        results[voice] = {
            "status": "PASS",
            "type": "FREE ONLINE",
            "file": str(out_file),
            "size_bytes": size,
            "text": text
        }
        print(f"  -> {voice}: Generated {size} bytes ({out_file})")

    print("\nEdge TTS Test Complete: All voices working via FREE ONLINE WebSocket.")
    return results

if __name__ == "__main__":
    asyncio.run(test_voices())
