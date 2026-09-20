# PROJECT_STATUS.md

## Trạng thái tổng thể: **PHASE 1 COMPLETE** ✅

Tất cả hạng mục Phase 1 OSS Validation đã hoàn thành. Sẵn sàng chuyển Phase 2 khi được phê duyệt.

| Hạng mục kiểm thử | Trạng thái | Ghi chú / Bằng chứng thực nghiệm |
| :--- | :--- | :--- |
| **1. Project Bootstrap** | **COMPLETE** | Khởi tạo repo sạch, tài liệu chuẩn, clone MediaCrawler & ViralMint, virtualenv Python 3.11 (`research/OSS_INVENTORY.md`). |
| **2. MediaCrawler - Bilibili** | **PASS** | Thu thập thành công **45 records** qua 2 từ khóa (`AI`, `Python`), đầy đủ 100% metadata (`data/evidence/mediacrawler/bilibili/`). Hoạt động ổn định không cần đăng nhập. |
| **2. MediaCrawler - Douyin Discovery** | **PASS** | CDP Mode với Edge + login session thật: **28 unique videos** qua 2 keyword (`减肥食谱`, `健身教程`). Fields: `aweme_id`, `aweme_url`, `nickname`, `create_time`, `liked_count`, `comment_count`, `share_count`, `collected_count`. Session persistence PASS (đóng/mở lại browser, không cần login lại). Evidence: `data/evidence/mediacrawler/douyin/douyin_discovery_evidence.json`. NOTE: `video_play_count` (views) không có trong search API response. |
| **2. MediaCrawler - Xiaohongshu** | **FAIL** | Optional - Bị chặn HTTP 403 (yêu cầu session đăng nhập). Quyết định: DROP trong Phase 1. |
| **2. MediaCrawler - Kuaishou** | **PARTIAL** | Optional - Tìm kiếm lấy được 21 records (`data/evidence/mediacrawler/kuaishou/`), comment API bị chặn bởi slider captcha. |
| **3. Real Douyin Media Inputs** | **PASS** | Tải thành công **2 video thực tế** trực tiếp từ ByteDance CDN Douyin (`real_douyin_sample1.mp4`, `real_douyin_sample2.mp4`), kiểm tra qua `ffprobe` (`data/evidence/viralmint/real_douyin_pipeline_summary.json`). |
| **4. Chinese ASR (Real Douyin)** | **PASS** | 2/2 video Douyin thật được bóc băng chính xác 100% ngôn ngữ `zh` với timestamps (`data/evidence/viralmint/transcripts/asr_real_douyin_sample*.json`). |
| **5. ViralMint Content Analysis** | **PARTIAL** | Upstream có prompt template `INSIGHTS_PROMPT` và schema dữ liệu trong `AnalyzerAgent`, nhưng **phụ thuộc 100% vào Cloud BYOK Key** (OpenAI/Claude/OpenRouter), không có local inference. Quyết định: **REUSE WITH ADAPTER**. (`data/evidence/viralmint/viralmint_real_capability_audit.json`). |
| **6. ViralMint Vietnamese Translation & Dub** | **PASS — INTEGRATION COMPLETE** | Hoàn thành pipeline E2E đầy đủ: Whisper ASR tiếng Trung -> Gemini 3.5 Flash-Lite dịch tiếng Việt súc tích -> Edge TTS (HoaiMy/NamMinh) -> Reframe dọc chuẩn 1080x1920 (9:16) blur-fill -> Burn phụ đề tiếng Việt karaoke -> Render MP4 đồng bộ âm thanh/hình ảnh. **Cleanup 2026-09-21**: `target_aspect` opt-in (không ép 9:16 khi không cần), `captions_only` khôi phục upstream semantics, Edge TTS 3-retry resilience. Regression 12/12 PASS. ViralMint `71253e2`, root `9703d7a`. Quyết định: **REUSE ViralMint + compatible provider**. |
| **7. Vietnamese TTS** | **PASS** | 5/5 file audio giọng Việt chuẩn (`vi-VN-HoaiMyNeural`, `vi-VN-NamMinhNeural`) qua Edge-TTS (`data/evidence/viralmint/tts_summary.json`). |
| **8. Render Video 9:16** | **PASS** | Render chuẩn dọc 1080x1920 (9:16) cho cả 2 video Douyin thật và video Bilibili, khớp audio, không lỗi corrupt (`data/evidence/viralmint/renders/`). |
| **9. Background Audio Mixing** | **PASS** | 2/2 mẫu thử nghiệm phối trộn BGM với giọng đọc (ducking -14dB, fade in/out) (`data/evidence/viralmint/audio_mix_summary.json`). |
| **10. Batch Processing** | **PASS** | Xử lý song song 3 video thành công trong 5.69s với cơ chế cô lập lỗi độc lập (`data/evidence/viralmint/batch_summary.json`). |
| **11. Programmatic Integration** | **PASS** | Luồng FastAPI hoàn chỉnh: Submit (`/api/downloaded/import`) -> Inspect (`/api/jobs/{id}`) -> Retrieve (`/api/downloaded/{id}`). |
| **12. Local Benchmark** | **PASS** | Đo đạc tài nguyên thực tế: E2E 1 video = 11.07s (Peak RAM: 314MB), Batch 3 = 5.69s (`research/LOCAL_BENCHMARK.md`). |

---

## Architecture Decisions

```text
Media processing:
REUSE ViralMint

Chinese ASR:
REUSE ViralMint

Vietnamese translation:
REUSE ViralMint + compatible LLM provider

Vietnamese dub:
REUSE ViralMint + UI/config adapter

Creative localization:
REUSE WITH PROMPT/LOGIC ADAPTER

Generic AI provider:
ADAPTER
```

---

## Exit Gate Decision

Theo quy tắc nghiêm ngặt của dự án:
- Nguồn Bilibili Discovery: `PASS`
- Nguồn Douyin Discovery: `PASS` (CDP + Edge + login session thật — 28 records, 2 keywords, session persistence OK)
- Real Douyin Media Pipeline: `PASS` (Input → ASR → Render)
- ViralMint AI Provider: `PASS (ADAPTER)` (Đã thêm generic OpenAI-compatible provider cho Gemini Free Tier / Local LLM)
- Vietnamese Adaptation / Dubbing: `PASS` (ASR → Gemini Translation → Vietnamese Subtitles → Edge TTS Dubbing → FFmpeg Render)

**TRẠNG THÁI HIỆN TẠI: PHASE 1 COMPLETE ✅**  
Tất cả blocker đã được giải quyết. ViralMint integration COMPLETE. Douyin Discovery PASS.  
Sẵn sàng chuyển Phase 2 khi được phê duyệt.
