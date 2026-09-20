# PROJECT_STATUS.md

## Trạng thái tổng thể: PHASE 1 COMPLETE (OSS Validation Passed)

| Hạng mục kiểm thử | Trạng thái | Ghi chú / Bằng chứng |
| :--- | :--- | :--- |
| **1. Project Bootstrap** | **COMPLETE** | Khởi tạo repo sạch, tài liệu chuẩn, clone MediaCrawler & ViralMint, tạo virtualenv Python 3.11 (`research/OSS_INVENTORY.md`). |
| **2. MediaCrawler - Bilibili** | **PASS** | Thu thập thành công 45 records qua 2 từ khóa (`AI`, `Python`), đầy đủ 100% metadata (`data/evidence/mediacrawler/bilibili/`). |
| **2. MediaCrawler - Douyin** | **PARTIAL** | API web bị chặn bởi Argus WAF (`code 2483`), cần adapter dùng browser CDP người dùng hoặc session cookie thật. |
| **2. MediaCrawler - Xiaohongshu** | **FAIL** | Optional - Bị chặn HTTP 403 (yêu cầu session đăng nhập tài khoản thật). Quyết định: DROP trong Phase 1. |
| **2. MediaCrawler - Kuaishou** | **PARTIAL** | Optional - Tìm kiếm lấy được 21 records (`data/evidence/mediacrawler/kuaishou/`), comment API bị chặn bởi slider captcha. |
| **3. ViralMint - Input Validation** | **PASS** | 6/6 file input được nạp và phân tích thành công (3 Douyin-derived, 2 Bilibili, 1 Local) (`data/evidence/viralmint/input_validation.json`). |
| **4. Chinese ASR** | **PASS** | 5/5 video tiếng Trung được bóc băng chính xác 100% qua `faster-whisper`, đủ timestamps (`data/evidence/viralmint/transcripts/`). |
| **5. Content Analysis** | **PASS** | 5/5 video trích xuất đầy đủ Topic, Hook, Format, Structure, Attention Mechanism (`data/evidence/viralmint/analysis/`). |
| **6. Vietnamese Adaptation** | **PASS** | 5/5 kịch bản chuyển thể tiếng Việt tự nhiên, 0 từ tiếng Trung sót lại, sẵn sàng lồng tiếng (`data/evidence/viralmint/scripts/`). |
| **7. Vietnamese TTS** | **PASS** | 5/5 file audio giọng Việt chuẩn (`vi-VN-HoaiMyNeural`, `vi-VN-NamMinhNeural`) qua Edge-TTS (`data/evidence/viralmint/tts/`). |
| **8. Render Video 9:16** | **PASS** | 3/3 video render chuẩn dọc 1080x1920 (9:16), khớp giọng lồng tiếng, không lỗi corrupt (`data/evidence/viralmint/renders/`). |
| **9. Background Audio** | **PASS** | 2/2 mẫu thử nghiệm phối trộn BGM với giọng đọc (ducking -14dB, fade in/out) (`data/evidence/viralmint/audio_mix/`). |
| **10. Batch Processing** | **PASS** | Xử lý song song 3 video thành công trong 5.69s với cơ chế cô lập lỗi độc lập (`data/evidence/viralmint/batch_summary.json`). |
| **11. Programmatic Integration** | **PASS** | Luồng FastAPI hoàn chỉnh: Submit (`/api/downloaded/import`) -> Inspect (`/api/jobs/{id}`) -> Retrieve (`/api/downloaded/{id}`). |
| **12. Local Benchmark** | **PASS** | Đo đạc tài nguyên thực tế: E2E 1 video = 11.07s (Peak RAM: 314MB), Batch 3 = 5.69s (`research/LOCAL_BENCHMARK.md`). |
