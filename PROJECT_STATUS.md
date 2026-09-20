# PROJECT_STATUS.md

## Trạng thái tổng thể: IN PROGRESS (Phase 1 — OSS Validation)

| Hạng mục kiểm thử | Trạng thái | Ghi chú / Bằng chứng |
| :--- | :--- | :--- |
| **1. Project Bootstrap** | IN PROGRESS | Đang khởi tạo cấu trúc thư mục và clone repositories |
| **2. MediaCrawler - Douyin** | PENDING | Cần kiểm tra 2 keyword search, >=20 records, creator/feed |
| **2. MediaCrawler - Bilibili** | PENDING | Cần kiểm tra 2 keyword search, >=20 records, creator/feed |
| **2. MediaCrawler - Xiaohongshu** | PENDING | Optional - Ghi nhận log & root cause |
| **2. MediaCrawler - Kuaishou** | PENDING | Optional - Ghi nhận log & root cause |
| **3. ViralMint - Input Validation** | PENDING | Kiểm tra 3 Douyin + 2 Bilibili + 1 local (Target: >=5/6) |
| **4. Chinese ASR** | PENDING | Kiểm tra transcript trên >=5 video (Target: >=4/5) |
| **5. Content Analysis** | PENDING | Trích xuất topic, hook, format, structure (Target: >=4/5) |
| **6. Vietnamese Adaptation** | PENDING | Chuyển ngữ tự nhiên >=5 scripts (Target: >=4/5) |
| **7. Vietnamese TTS** | PENDING | Sinh >=5 voice samples (Target: 5/5 no error, >=4/5 clear) |
| **8. Render Video 9:16** | PENDING | Render >=3 complete vertical videos (Target: 3/3 playable) |
| **9. Background Audio** | PENDING | Test >=2 video có BGM (bảo toàn BGM + rõ voice) |
| **10. Batch Processing** | PENDING | Test batch 3 videos (Target: >=2/3 success, isolated fail) |
| **11. Programmatic Integration** | PENDING | Kiểm tra REST hoặc MCP endpoint |
| **12. Local Benchmark** | PENDING | Đo RAM, time, disk, GPU trên 1 video và batch 3 videos |
