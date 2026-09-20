# PROJECT_PLAN.md

## Lộ trình phát triển tổng quan (Roadmap)

### Giai đoạn 1: OSS Validation (Hiện tại)
- Mục tiêu: Kiểm chứng thực tế năng lực của `MediaCrawler` và `ViralMint`.
- Đầu ra:
  - `research/OSS_CAPABILITY_MATRIX.md`
  - `research/LOCAL_BENCHMARK.md`
  - `research/KNOWN_GAPS.md`
  - `research/ARCHITECTURE_DECISION.md`
- Tiêu chí hoàn thành (Exit Gate): Toàn bộ 12 chỉ tiêu kiểm thử đạt ngưỡng yêu cầu hoặc phân loại gap khả thi.

### Giai đoạn 2: Source Radar MVP (Dự kiến sau Phase 1)
- Xây dựng module tự động tìm kiếm, lọc và chấm điểm nội dung tiềm năng (candidate ranking).
- Tích hợp SQLite lưu trữ metadata bài viết và tác giả.
- Giao diện dòng lệnh (CLI) phục vụ vận hành.

### Giai đoạn 3: Adaptation & Production Pipeline (Dự kiến)
- Chuẩn hóa quy trình ASR -> Phân tích -> Việt hóa kịch bản -> TTS -> Render.
- Xây dựng giao diện Human Review cục bộ.

### Giai đoạn 4: Distribution & Measurement (Dự kiến)
- Hỗ trợ công cụ hỗ trợ đăng tải nội dung lên TikTok, Facebook Reels, YouTube Shorts.
- Theo dõi chỉ số hiệu quả và phản hồi vòng lặp sáng tạo.
