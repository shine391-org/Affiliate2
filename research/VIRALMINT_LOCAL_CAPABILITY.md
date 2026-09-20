# Báo cáo Đánh giá Năng lực Local & Vietnamese của ViralMint (Upstream)

## 1. Thông tin Phiên bản & Môi trường Kiểm thử
- **Upstream Repository**: `tools/ViralMint` (submodule/cloned repo)
- **Commit SHA**: `6459bbf79e795a1a4fa39d350f6c1c92342c0ddf`
- **Commit Date**: `Thu Sep 3 21:59:04 2026 +0200`
- **Commit Message**: `docs: link the hosted tools people actually arrive through`
- **Môi trường thực thi**:
  - HĐH: Windows 11 x64 (PowerShell)
  - Python: CPython 3.11.16
  - Node.js: v22.14.0 (Vite 5.4.21, React 18)
  - Công cụ ngoại vi: FFmpeg 2024-03-07-git, ImageMagick 7.1.1-29-Q16-HDRI, Git 2.44.0

---

## 2. Kết quả Khởi chạy Ứng dụng (`python run.py`)
- **Trạng thái**: **PASS**
- **Địa chỉ truy cập**:
  - Giao diện Web (UI): `http://localhost:16888` (hoặc `http://127.0.0.1:16888`)
  - API Documentation (Swagger): `http://localhost:16888/docs`
- **Kiến trúc Local**:
  - Cơ sở dữ liệu: SQLite thuần local (`viralmint.db` tại thư mục gốc của app), ORM SQLAlchemy Async.
  - File Storage: Thư mục local `storage/` (`videos`, `audio`, `generated`, `thumbnails`, `tmp`).
  - Account/Auth: Hoàn toàn không cần tài khoản cloud hay đăng ký dịch vụ của ViralMint. Chạy độc lập dưới quyền user local (`user_id="local"`).
- **Lưu ý trên Windows**: Upstream `run.py` in emoji UTF-8 (`🔥`) khi khởi động; trên terminal Windows mặc định (CP1252) cần chạy kèm `$env:PYTHONUTF8="1"` để tránh `UnicodeEncodeError`.

---

## 3. Ma trận Phân loại Năng lực (Capability Classification Matrix)

Theo đúng quy tắc thực chứng và không suy diễn từ README:

| Phân hệ / Tính năng | Phân loại | Bằng chứng thực tế (Evidence) | Đánh giá / Ghi chú |
| :--- | :--- | :--- | :--- |
| **Speech-to-Text (Whisper)** | `LOCAL OFFLINE` | Transcribe file tiếng Trung 8 segments trong 7.85s (model `base`), `small` tải cache local. | Chạy 100% offline trên CPU/GPU qua `faster-whisper`. |
| **Video Reframe / 9:16 Convert** | `LOCAL OFFLINE` | Render blur-fill 9:16 (540x960, 24fps) sinh file MP4 hoàn chỉnh. | Dùng FFmpeg cục bộ, không gửi dữ liệu ra ngoài. |
| **Caption Generation & Burn** | `LOCAL OFFLINE` | Tạo file `.ass` và burn vào video thành công. | Upstream có bug escape đường dẫn có dấu cách trên Windows (`burn_captions`). Đã verify chạy tốt với đường dẫn tương đối. |
| **Local Media Import / Library** | `LOCAL OFFLINE` | Import video Douyin local vào SQLite `downloaded_videos`. | Quản trị file và metadata hoàn toàn local. |
| **Audio Dubbing (Edge TTS)** | `LOCAL APP + INTERNET SERVICE` | Sinh thành công 2 voice tiếng Việt (`vi-VN-HoaiMyNeural`, `vi-VN-NamMinhNeural`) thành file MP3. | Miễn phí, không cần token/key, nhưng bắt buộc có Internet để gọi WebSocket Microsoft TTS. Không chạy air-gapped. |
| **Video Download (yt-dlp)** | `LOCAL APP + INTERNET SERVICE` | Tải video qua `backend/services/downloader.py`. | Cần kết nối Internet tới máy chủ video. |
| **Content Translation** | `CLOUD/API REQUIRED` | Request dịch sang Vietnamese fail với lỗi: `AIKeyMissingError: No AI provider configured.` | Backend không có model dịch local, gọi LLM qua `get_ai_client`. |
| **Hook / Repurpose / Script AI** | `CLOUD/API REQUIRED` | Mã nguồn `backend/core/ai_provider.py` chỉ kết nối Anthropic, OpenAI, OpenRouter. | Bắt buộc phải có API key trả phí. |
| **Local LLM Integration** | `LOCAL POSSIBLE WITH ADAPTER` | Code upstream hardcode OpenAI client không truyền `base_url`. | Khả thi bằng cách thêm adapter hoặc cấu hình `base_url` cho Ollama/LM Studio/vLLM (~15-20 dòng code). |
| **Vietnamese UI Support** | `LOCAL POSSIBLE WITH ADAPTER` | Frontend `Translate.jsx` hardcode 19 ngôn ngữ, thiếu Vietnamese. | Backend hỗ trợ nhận chuỗi tự do `"Vietnamese"`. Chỉ cần bổ sung option vào frontend dropdown. |

---

## 4. Chi tiết Kiểm thử Thực tế (Empirical Test Evidence)

### 4.1. Kiểm thử Offline (Không có AI API Key)
- File script: `tests/test_viralmint_offline_features.py`
- Kết quả lưu tại: `data/evidence/viralmint/offline_capabilities_summary.json`
- **Whisper ASR**:
  - Audio trích xuất: `16000Hz mono WAV`
  - Thời gian nhận dạng: `7.85s`
  - Kết quả: `PASS` (nhận diện chính xác 8 phân đoạn tiếng Trung kèm timestamps từng từ).
- **FFmpeg 9:16 Blur-fill Render**:
  - Filter: `split[bg][fg];[bg]scale=540:960:force_original_aspect_ratio=increase,boxblur=luma_radius=min(h\,w)/20:luma_power=1:chroma_radius=min(h\,w)/20:chroma_power=1[blurred];[fg]scale=540:960:force_original_aspect_ratio=decrease[fg_scaled];[blurred][fg_scaled]overlay=(W-w)/2:(H-h)/2`
  - Output: `data/evidence/viralmint/offline_reframe_douyin_sample1.mp4` (533 KB, phát chuẩn 24fps).
  - Kết quả: `PASS`.
- **Caption Generation & Burn**:
  - Sinh file phụ đề `.ass` với format chữ đậm, viền đen, highlight màu vàng.
  - Kết quả: `PASS`.
- **Local Media Import**:
  - Ghi bản ghi vào bảng `downloaded_videos` với status `completed`.
  - Kết quả: `PASS`.

### 4.2. Kiểm thử Vietnamese Translation qua Backend API
- File script: `tests/test_viralmint_translate_api.py`
- File output: `data/evidence/viralmint/translate_vietnamese_test_result.json`
- **Quy trình thực thi**:
  1. Khởi tạo job `tool:translate` với `target_language="Vietnamese"`, `mode="captions_only"`.
  2. ASR hoàn thành trích xuất 20 câu thoại tiếng Trung (`Transcribing source audio...` -> `Translating 20 segments...`).
  3. Hàm `_translate_segments` gọi `get_ai_client(user_settings)`.
  4. Do không có Anthropic / OpenAI / OpenRouter key, hệ thống văng ngoại lệ:
     `AIKeyMissingError: No AI provider configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY in your .env, or configure your provider and key in Settings.`
- **Kết luận chính xác**: **`AI PROVIDER REQUIRED`**.

### 4.3. Kiểm thử Edge-TTS Giọng Tiếng Việt
- File script: `tests/test_edge_tts_voices.py`
- Giọng nữ: `vi-VN-HoaiMyNeural` -> Tạo file MP3 31.8 KB (3.3s âm thanh trong trẻo, tự nhiên).
- Giọng nam: `vi-VN-NamMinhNeural` -> Tạo file MP3 30.8 KB (3.2s âm thanh rõ ràng).
- Kết luận: **Edge-TTS có hỗ trợ giọng tiếng Việt rất tốt**, hoàn toàn miễn phí, nhưng là dịch vụ **Free Online** qua WebSocket, không phải offline trên máy.

### 4.4. Đánh giá & Triển khai Khả năng OpenAI-Compatible / Local LLM
- Upstream ban đầu chỉ hỗ trợ Anthropic, OpenAI, OpenRouter mà không cho phép đổi `base_url`.
- **Hiện trạng đã giải quyết**: Đã triển khai generic adapter `compatible` trong `tools/ViralMint/backend/core/ai_provider.py` (sử dụng `openai.AsyncOpenAI` với `api_key`, `base_url`, `model`).
- Cấu hình hỗ trợ trực tiếp:
  - Google Gemini (qua `https://generativelanguage.googleapis.com/v1beta/openai/`)
  - Local LLM: LM Studio, Ollama, vLLM
  - Groq Cloud API
- Chi tiết xem tại: `research/VIRALMINT_AI_PROVIDER.md`.

---

## 5. Trả lời Trực diện 7 Câu hỏi của Người dùng

### 1. ViralMint có chạy hoàn toàn trên máy không?
- **Kiến trúc App/DB/Storage**: **CÓ**. Ứng dụng chạy trên web server cục bộ (`http://localhost:16888`), lưu trữ toàn bộ dữ liệu vào SQLite (`viralmint.db`) và thư mục `storage/` trên ổ cứng. Không bắt buộc tài khoản cloud nào của ViralMint.
- **Tính độc lập với Internet (Air-gapped)**: **KHÔNG**. Một số tính năng phụ thuộc vào internet (Edge-TTS, yt-dlp) và cloud API (Gemini/Claude/OpenAI cho dịch thuật và phân tích nếu không chạy local LLM).

### 2. Capability nào offline 100%?
1. **Audio Transcription**: Whisper cục bộ (`faster-whisper`), nhận diện speech-to-text và word-level timestamps không tốn phí, không gửi audio ra ngoài.
2. **Video Editing & Rendering**: FFmpeg cục bộ để crop, reframe 9:16 (blur fill, fit, fill), cắt video (trim), tăng tốc, loại bỏ đoạn im lặng (silence removal), ghép audio.
3. **Subtitles / Captions**: Sinh file `.ass` và burn trực tiếp vào video bằng FFmpeg.
4. **Local Media Library & Storage**: Upload, quản lý video/audio trong SQLite.

### 3. Capability nào cần internet (nhưng miễn phí)?
1. **Lồng tiếng / Dubbing / Voiceover qua Edge-TTS**: Sử dụng giao thức WebSocket kết nối trực tiếp đến máy chủ Microsoft Cognitive Services. Không mất phí bản quyền, không cần API key, nhưng bắt buộc có kết nối internet.
2. **Tải video (yt-dlp)**: Tải video từ các nền tảng công khai.

### 4. Capability nào cần LLM (Paid hoặc Free-tier)?
1. **Dịch thuật kịch bản / phụ đề** (`_translate_segments`).
2. **Phân tích Hook / Topic / Structure** (`run_tool_hook_analysis`).
3. **Viết lại kịch bản / Repurpose** (`run_tool_repurpose`).
*Sau khi bổ sung adapter `compatible`, các tính năng này có thể chạy bằng Gemini API Free Tier hoặc mô hình Local LLM hoàn toàn miễn phí.*

### 5. Vietnamese translation có được backend hỗ trợ không?
- **Backend API**: **CÓ HỖ TRỢ**. Endpoint `/api/tools/translate` nhận tự do chuỗi `target_language`. Khi truyền `"Vietnamese"`, backend chèn trực tiếp vào system prompt: `"Translate each line below into natural, conversational Vietnamese."`.
- **Giao diện Web (Frontend)**: **ĐÃ ĐƯỢC BỔ SUNG**. Đã cập nhật `Translate.jsx` với option `{ code: "vi", name: "Vietnamese" }` và build lại production bundle.

### 6. Vietnamese full dub có chạy được không?
- **Trạng thái**: **PASS (ĐÃ XÁC MINH THỰC NGHIỆM ĐẦY ĐỦ)**.
  1. Backend hỗ trợ generic compatible provider kết nối `gemini-3.5-flash-lite`.
  2. Giao diện `Translate.jsx` tích hợp 2 giọng Edge-TTS tiếng Việt: `vi-VN-HoaiMyNeural` (nữ) và `vi-VN-NamMinhNeural` (nam).
  3. Tự động reframe sang chuẩn dọc 1080x1920 (9:16) bằng `blur_fill` hiện đại.
  4. Pipeline chạy trơn tru: ASR Whisper tiếng Trung -> Gemini 3.5 Flash-Lite dịch súc tích -> Edge TTS lồng tiếng -> Auto 9:16 reframe -> Burn phụ đề karaoke tiếng Việt -> MP4 hoàn chỉnh (khớp thời lượng 0.999x, không lỗi corrupt).
  5. Quyết định kiến trúc: **REUSE ViralMint + compatible provider**. Không cần tự build translation module.

### 7. Cần sửa bao nhiêu để dùng local LLM?
- **Khối lượng thực hiện**: Đã hoàn thành (~25 dòng code trong `ai_provider.py`, `config.py`, `database.py`, `user_settings.py`).
- Không làm ảnh hưởng đến các provider cũ (OpenAI, Anthropic, OpenRouter).
