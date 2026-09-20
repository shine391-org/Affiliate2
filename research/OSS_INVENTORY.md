# OSS INVENTORY & BOOTSTRAP METADATA

Tài liệu ghi nhận chi tiết về các bộ mã nguồn mở được tích hợp vào dự án trong Phase 1.

---

## 1. MediaCrawler

- **Repository URL**: `https://github.com/NanmiCoder/MediaCrawler.git`
- **Commit Hash**: `380b426000aac3d612837ed72c99808347dc94c9`
- **Branch**: `main`
- **License**: `NON-COMMERCIAL LEARNING LICENSE 1.1` (Copyright 2024 relakkes@gmail.com)
- **Primary Role in Project**:
  - Content discovery theo từ khóa (keywords search)
  - Crawl metadata và media (Douyin, Bilibili, Xiaohongshu, Kuaishou)
  - Creator feed profiling

### Environment & Ports
- **Default Ports**:
  - CDP Remote Debugging Port: `9222` (hoặc auto-increment nếu bận)
  - WebUI (nếu bật): `5000` / `8000`
- **Supported Storage**: SQLite, JSONL, JSON, CSV, MySQL, PostgreSQL, MongoDB. (Dự án chọn `sqlite` / `jsonl` tuân thủ nguyên tắc local-first).
- **Environment Variables** (`.env`):
  - Hỗ trợ cấu hình DB, Redis, Proxy (Kuaidaili, Wandou, Jisu). Dự án chạy local SQLite nên không yêu cầu DB ngoại vi.

### Installation & Dependencies
- Quản lý qua Python venv (khuyến nghị Python 3.11/3.12).
- Cốt lõi: `playwright`, `httpx`, `typer`, `aiosqlite`, `pandas`, `pydantic`, `opencv-python`.
- Trình duyệt Playwright: `playwright install chromium`.

---

## 2. ViralMint

- **Repository URL**: `https://github.com/openclaw-easy/ViralMint.git`
- **Commit Hash**: `6459bbf79e795a1a4fa39d350f6c1c92342c0ddf`
- **Branch**: `main`
- **License**: `GNU AGPL-3.0` (GNU Affero General Public License Version 3)
- **Primary Role in Project**:
  - Media ingestion & download (yt-dlp)
  - Chinese transcription (Whisper / faster-whisper)
  - Content analysis & script adaptation (LLM API: Anthropic / OpenAI / OpenRouter)
  - Vietnamese TTS (EdgeTTS: vi-VN-HoaiMyNeural, vi-VN-NamMinhNeural)
  - Video composition, subtitling & FFmpeg 9:16 rendering
  - Batch job queue & REST/MCP programmatic interfaces

### Environment & Ports
- **Default Host/Port**: `127.0.0.1:16888`
- **Database**: SQLite qua `aiosqlite` & `SQLAlchemy[asyncio]`, lưu trữ local tại thư mục data của ứng dụng.
- **Environment Variables** (`.env`):
  - `HOST`: `127.0.0.1`
  - `PORT`: `16888`
  - `SECRET_KEY`, `ENCRYPTION_KEY`: Tự sinh nếu để trống.
  - `DEBUG`: `true`
  - `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`: Khóa API mô hình ngôn ngữ lớn (BYOK).
  - `TIKHUB_API_KEY`, `YOUTUBE_API_KEY`: Tùy chọn scout.

### Installation & Dependencies
- Quản lý qua Python venv (Python 3.11/3.12).
- Cốt lõi: `fastapi`, `uvicorn`, `faster-whisper`, `edge-tts`, `yt-dlp`, `imageio-ffmpeg`, `aiosqlite`, `sqlalchemy`.
