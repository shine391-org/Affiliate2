# ARCHITECTURE DECISION — Phase 1 Validation

Tài liệu quyết định kiến trúc phân bổ từng năng lực (Capability) của hệ thống **AI Content Distribution Engine**, được hình thành từ kết quả kiểm chứng thực tế trong Phase 1.

**Quy ước quyết định**:
- `REUSE`: Sử dụng nguyên bản thành phần từ mã nguồn mở (OSS).
- `REUSE WITH ADAPTER`: Tận dụng mã nguồn mở kèm một lớp bọc (adapter/wrapper) để khắc phục hạn chế nhỏ hoặc chuẩn hóa I/O.
- `BUILD`: Tự xây dựng module riêng do mã nguồn mở không hỗ trợ hoặc chi phí tinh chỉnh quá lớn.
- `DROP`: Loại bỏ khỏi phạm vi hiện tại do rủi ro kỹ thuật hoặc chi phí không tương xứng.

---

## 1. Bảng phân loại quyết định kiến trúc (Architecture Decision Table)

| STT | Khả năng (Capability) | Quyết định (Decision) | Thành phần phụ trách (Component) | Bằng chứng thực nghiệm (Evidence) & Cơ sở kỹ thuật |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **Bilibili Search & Discovery** | **REUSE WITH ADAPTER** | MediaCrawler (`BilibiliClient`, `BilibiliSign`) | Thu thập thành công 45 records đầy đủ 100% metadata. Adapter bọc client để vượt qua câu lệnh `sys.exit(1)` chặn người dùng chưa đăng nhập trong upstream `core.py`. |
| 2 | **Douyin Search & Discovery** | **REUSE WITH ADAPTER** | MediaCrawler (`CDPBrowserManager` / Custom Session) | Trực tiếp gọi API bị chặn bởi WAF Argus (`code 2483`). Bọc adapter tái sử dụng trình duyệt người dùng qua cổng CDP 9222 hoặc nạp cookie session thật. |
| 3 | **Kuaishou Search** | **REUSE WITH ADAPTER** | MediaCrawler (`KuaishouCrawler`) | Lấy thành công 21 video records từ khóa `AI`. Adapter tắt cờ lấy bình luận (`--get_comment false`) để tránh kích hoạt captcha trượt. |
| 4 | **Xiaohongshu Search** | **DROP** | — | Bị khóa hoàn toàn ở mức tài khoản (HTTP 403 / unauthenticated). Chi phí duy trì tài khoản cao, không khả thi cho MVP ban đầu. |
| 5 | **Candidate Ranking & Outlier Scoring** | **BUILD** | Project Module (`src/ranking/`) | Cả 2 bộ OSS không cung cấp thuật toán xếp hạng video đột biến. Tự xây dựng công thức dựa trên metadata crawl được (`play`, `like`, `favorites`, `pubdate`). |
| 6 | **Media Acquisition (Tải video)** | **REUSE** | ViralMint (`ytdlp_service.py` / `yt-dlp`) | Tải tốc độ cao (2-6s cho video 1080p), hỗ trợ cắt đoạn theo mốc thời gian (`--download-sections`) giúp tiết kiệm băng thông và dung lượng đĩa. |
| 7 | **Chinese ASR (Bóc băng)** | **REUSE** | ViralMint (`whisper_service.py` / `faster-whisper`) | Xử lý 5/5 video tiếng Trung chính xác tuyệt đối, giữ trọn vẹn thuật ngữ kỹ thuật, độ trễ chỉ 2.5s/video trên CPU, RAM chỉ ~150MB (model base). |
| 8 | **Content Analysis (Topic, Hook, Format)** | **REUSE WITH ADAPTER** | ViralMint (`viral_formula_service.py`) + LLM Adapter | Cấu trúc phân tích hook và cấu trúc video của ViralMint rất tối ưu. Cần adapter chuẩn hóa API key sang BYOK (OpenAI/Claude/OpenRouter) theo chuẩn JSON schema. |
| 9 | **Vietnamese Adaptation (Việt hóa kịch bản)** | **REUSE WITH ADAPTER** | ViralMint (`clip_extractor.py` + Adaptation Prompt) | Dùng prompt chuyển thể phong cách video ngắn nói chuyện (talking-head), loại bỏ triệt để từ Hán-Việt thô cứng, đảm bảo 100% văn phong tự nhiên. |
| 10 | **Vietnamese TTS (Lồng tiếng)** | **REUSE WITH ADAPTER** | ViralMint (`edge_tts_service.py`) + Text Normalizer | Giọng đọc `vi-VN-HoaiMyNeural` và `vi-VN-NamMinhNeural` trong trẻo, miễn phí 100%. Cần adapter chuẩn hóa tỷ lệ (như `9:16` -> `chín mười sáu`) để tránh lỗi XML/SSML. |
| 11 | **Subtitle Generation & Burn-in** | **REUSE** | ViralMint (`caption_service.py`, `ffmpeg_service.py`) | Tạo phụ đề ASS tự động theo mốc thời gian của Whisper, nhúng trực tiếp vào video qua bộ lọc FFmpeg. |
| 12 | **Video Rendering 9:16** | **REUSE** | ViralMint (`ffmpeg_service.py` / `video_utils.py`) | Bộ lọc `scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920` chuyển đổi chuẩn xác 100% sang định dạng dọc Shorts/Reels/TikTok. |
| 13 | **Background Audio Mixing (Ducking)** | **REUSE** | ViralMint (`music_service.py` `mix_audio`) | Tự động hạ âm lượng nhạc nền xuống -14dB, fade in/out 1s-2s, giữ giọng đọc tiếng Việt rõ ràng, cân bằng âm lượng qua `alimiter`. |
| 14 | **Batch Processing** | **REUSE** | ViralMint (`task_runner.py` / `asyncio.gather`) | Xử lý song song nhiều video với cơ chế cô lập lỗi, một tác vụ thất bại không làm sập toàn bộ hàng đợi. |
| 15 | **Programmatic Integration** | **REUSE** | ViralMint FastAPI Backend (`/api/downloaded`, `/api/jobs`) | API REST chuẩn hóa đầy đủ: Nạp tác vụ (submit) → Kiểm tra tiến độ (inspect) → Lấy kết quả (retrieve). |
| 16 | **Local Database Storage** | **REUSE** | SQLite (via `aiosqlite` & `SQLAlchemy`) | Lưu trữ toàn bộ metadata, kịch bản và job state cục bộ. Tuân thủ triệt để nguyên tắc không microservices, không Redis/PostgreSQL. |

---

## 2. Luồng dữ liệu kiến trúc đề xuất (Target Architecture Flow)

```
[ Bilibili / Douyin (CDP) / Kuaishou ]
                │
                ▼ (MediaCrawler Adapter)
     [ Source Metadata (SQLite) ]
                │
                ▼ (BUILD: Candidate Ranking & Outlier Engine)
      [ Top Growth Candidates ]
                │
                ▼ (ViralMint yt-dlp Service)
         [ Raw Local Media ]
                │
                ▼ (ViralMint WhisperService)
        [ Chinese Transcripts ]
                │
                ▼ (LLM Content Analyzer & Adapter)
      [ Vietnamese Adapted Scripts ]
                │
                ▼ (Edge-TTS Adapter)
       [ Vietnamese Audio Tracks ]
                │
                ▼ (ViralMint FFmpeg Service)
 [ Rendered 9:16 Videos with Subtitles & Mixed Audio ]
                │
                ▼
         [ Human Review ]
```

---

## 3. Kết luận kiến trúc

Stack mã nguồn mở **hoàn toàn đủ năng lực** để đưa dự án bước sang giai đoạn tiếp theo (**Phase 2: Source Radar MVP**) mà không cần tự phát triển lại các thành phần nặng như crawler base, ASR, TTS, audio mixer hay render pipeline.

Khối lượng code tự xây dựng (BUILD) trong Phase 2 chỉ tập trung vào:
1. **Candidate Ranking Engine**: Thuật toán tính điểm Freshness, Engagement, Outlier Score trên SQLite.
2. **CLI Wrapper & Local Storage Schema**: Giao diện dòng lệnh vận hành toàn bộ luồng tự động từ Discovery đến Render.
