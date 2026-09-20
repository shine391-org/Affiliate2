# ARCHITECTURE DECISION — Phase 1 Validation

Tài liệu quyết định kiến trúc phân bổ từng năng lực (Capability) của hệ thống **AI Content Distribution Engine**, được hình thành từ kết quả kiểm chứng thực nghiệm trên mã nguồn và hệ thống thực tế.

**Quy ước quyết định**:
- `REUSE`: Sử dụng nguyên bản thành phần từ mã nguồn mở (OSS).
- `REUSE WITH ADAPTER`: Tận dụng mã nguồn mở kèm một lớp bọc (adapter/wrapper) để khắc phục hạn chế nhỏ hoặc chuẩn hóa I/O.
- `BUILD`: Tự xây dựng module riêng do mã nguồn mở không hỗ trợ hoặc chi phí tinh chỉnh quá lớn.
- `DROP`: Loại bỏ khỏi phạm vi hiện tại do rủi ro kỹ thuật hoặc chi phí không tương xứng.

---

## 1. Bảng phân loại quyết định kiến trúc (Architecture Decision Table)

| STT | Khả năng (Capability) | Quyết định (Decision) | Thành phần phụ trách (Component) | Bằng chứng thực nghiệm (Evidence) & Cơ sở kỹ thuật |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **Bilibili Search & Discovery** | **REUSE WITH ADAPTER** | MediaCrawler (`BilibiliClient`, `BilibiliSign`) | Thu thập thành công **45 records** đầy đủ 100% metadata. Adapter bọc client để vượt qua câu lệnh `sys.exit(1)` chặn người dùng chưa đăng nhập trong upstream `core.py`. Hoạt động ổn định không cần đăng nhập. |
| 2 | **Douyin Search & Discovery** | **UNRESOLVED / BLOCKED** | Chờ quyết định phương án Nguồn | **Kiểm thử CDP thực tế THẤT BẠI** (`0/20 records`). Gặp rào cản kép: Argus WAF + Modal bắt buộc đăng nhập ("登录后免费看") chặn DOM video cards. Không có tài khoản đăng nhập và Chrome `v20` App-Bound Encryption ngăn chia sẻ cookie. **Không coi CDP Douyin là verified**. |
| 3 | **Kuaishou Search** | **REUSE WITH ADAPTER** | MediaCrawler (`KuaishouCrawler`) | Lấy thành công **21 records** từ khóa `AI`. Adapter tắt cờ lấy bình luận (`--get_comment false`) để tránh kích hoạt captcha trượt. |
| 4 | **Xiaohongshu Search** | **DROP** | — | Bị khóa hoàn toàn ở mức tài khoản (HTTP 403 / unauthenticated). Chi phí duy trì tài khoản cao, loại bỏ khỏi phạm vi Phase 1. |
| 5 | **Candidate Ranking & Outlier Scoring** | **BUILD** | Project Module (`src/ranking/`) | Cả MediaCrawler và ViralMint đều không cung cấp thuật toán chấm điểm video tiềm năng. Tự xây dựng công thức dựa trên metadata (`play`, `like`, `favorites`, `pubdate`) trên nền SQLite. |
| 6 | **Media Acquisition (Tải video)** | **REUSE** | ViralMint (`ytdlp_service.py` / `yt-dlp` / Playwright CDN fetch) | Tải thành công video từ Bilibili và ByteDance CDN Douyin (`real_douyin_sample1.mp4`, `real_douyin_sample2.mp4`). |
| 7 | **Chinese ASR (Bóc băng)** | **REUSE** | ViralMint (`whisper_service.py` / `faster-whisper`) | Xử lý 7/7 video tiếng Trung (bao gồm cả 2 video Douyin thật) chính xác tuyệt đối, giữ trọn vẹn thuật ngữ kỹ thuật, độ trễ 2-5s/video trên CPU, RAM ~300MB (model base). |
| 8 | **Content Analysis (Topic, Hook, Format)** | **REUSE WITH ADAPTER** | ViralMint (`AnalyzerAgent`) + LLM Adapter | Cấu trúc phân tích hook (`INSIGHTS_PROMPT`) và retention curve của ViralMint rất chi tiết. Tuy nhiên ViralMint không có local model, cần adapter kết nối với API LLM được quản lý tập trung. |
| 9 | **Vietnamese Adaptation (Việt hóa kịch bản)** | **BUILD** | Project Module (`src/adaptation/`) | **ViralMint hoàn toàn không có capability chuyển thể tiếng Việt** (chỉ có prompt tạo script tiếng Anh `SCRIPT_PROMPT`). Phải tự xây dựng module adaptation riêng với prompt tối ưu văn phong short-form Việt Nam và từ điển thuật ngữ. |
| 10 | **Vietnamese TTS (Lồng tiếng)** | **REUSE WITH ADAPTER** | ViralMint (`edge_tts_service.py`) + Text Normalizer | Giọng đọc `vi-VN-HoaiMyNeural` và `vi-VN-NamMinhNeural` trong trẻo, miễn phí 100%. Cần adapter chuẩn hóa tỷ lệ (như `9:16` -> `chín mười sáu`) để tránh lỗi XML/SSML WebSocket. |
| 11 | **Video Rendering 9:16** | **REUSE** | ViralMint (`ffmpeg_service.py`) | Render chuẩn xác 100% video dọc 1080x1920 (9:16) bằng FFmpeg, scale & pad tự động, re-mux audio tiếng Việt hoàn hảo. |
| 12 | **Background Audio Mixing (Ducking)** | **REUSE** | ViralMint (`music_service.py` `mix_audio`) | Tự động hạ âm lượng nhạc nền xuống -14dB, fade in/out 1s-2s, giữ giọng đọc tiếng Việt rõ ràng, cân bằng âm lượng qua `alimiter`. |
| 13 | **Batch Processing** | **REUSE** | ViralMint (`task_runner.py` / `asyncio.gather`) | Xử lý song song nhiều video với cơ chế cô lập lỗi, một tác vụ thất bại không làm sập toàn bộ hàng đợi. |
| 14 | **Programmatic Integration** | **REUSE** | ViralMint FastAPI Backend (`/api/downloaded`, `/api/jobs`) | API REST chuẩn hóa đầy đủ: Nạp tác vụ (submit) → Kiểm tra tiến độ (inspect) → Lấy kết quả (retrieve). |
| 15 | **Local Database Storage** | **REUSE** | SQLite (via `aiosqlite` & `SQLAlchemy`) | Lưu trữ toàn bộ metadata, kịch bản và job state cục bộ. Không dùng Redis, Postgres, Vector DB. |

---

## 2. Nguyên tắc Phạm vi & Ranh giới (Scope Boundaries)

- **Không xây dựng cơ chế Bypass/Anti-Detection phức tạp**: Dự án không cố gắng dịch ngược hoặc can thiệp sâu để né tránh WAF của Douyin khi không có phiên người dùng hợp lệ.
- **Không áp dụng các thủ thuật Fingerprint Evasion**: Dự án loại bỏ hoàn toàn các đề xuất biến đổi video mang tính đối phó thuật toán (flip, dynamic zoom, picture-in-picture, blur góc) nhằm né bản quyền hay thuật toán Unoriginal Content. Dự án tập trung vào phân phối nội dung có giá trị biên tập thật sự.
- **Không tự ý chuyển sang Phase 2**: Khi chưa giải quyết xong yêu cầu về Douyin Discovery, toàn bộ dự án dừng lại ở Phase 1 và thông báo trạng thái nghẽn (`PHASE 1 BLOCKED`).
