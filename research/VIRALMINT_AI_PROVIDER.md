# Báo cáo Triển khai Generic OpenAI-Compatible AI Provider cho ViralMint

## 1. Mục tiêu và Kiến trúc

Mục tiêu: Bổ sung khả năng kết nối tới các AI Provider theo chuẩn OpenAI API (`compatible`) vào ViralMint, cho phép sử dụng:
- Google Gemini API (thông qua OpenAI-compatible endpoint tại `https://generativelanguage.googleapis.com/v1beta/openai/`)
- Groq Cloud API (`https://api.groq.com/openai/v1`)
- Local LLMs: LM Studio (`http://127.0.0.1:1234/v1`), Ollama (`http://localhost:11434/v1`), vLLM (`http://localhost:8000/v1`)
- Bất kỳ API gateway nào tuân theo chuẩn OpenAI Chat Completions mà không cần sửa đổi pipeline cốt lõi.

### Kiến trúc giải pháp: `ADAPTER`
- Sử dụng trực tiếp client `openai.AsyncOpenAI` với các tham số: `api_key`, `base_url`, và `model`.
- Không phụ thuộc vào OpenRouter hay bất kỳ trung gian thanh toán nào.
- Giữ nguyên vẹn 100% hoạt động của các provider cũ: **OpenAI**, **Anthropic**, **OpenRouter**.

---

## 2. Cấu hình Chuẩn (Minimum Configuration)

Thêm vào file `.env` hoặc biến môi trường hệ thống:

```env
AI_PROVIDER=compatible
AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
AI_API_KEY=<USER_KEY>
AI_MODEL=gemini-2.5-flash
```

### Các kịch bản mở rộng trong tương lai:
1. **Local LM Studio**:
   ```env
   AI_PROVIDER=compatible
   AI_BASE_URL=http://127.0.0.1:1234/v1
   AI_API_KEY=lm-studio
   AI_MODEL=qwen2.5-7b-instruct
   ```
2. **Local Ollama**:
   ```env
   AI_PROVIDER=compatible
   AI_BASE_URL=http://localhost:11434/v1
   AI_API_KEY=ollama
   AI_MODEL=llama3.1:8b
   ```
3. **Groq Cloud**:
   ```env
   AI_PROVIDER=compatible
   AI_BASE_URL=https://api.groq.com/openai/v1
   AI_API_KEY=gsk_...
   AI_MODEL=llama-3.3-70b-versatile
   ```

---

## 3. Chi tiết Cập nhật Mã nguồn

### 3.1. Backend (`tools/ViralMint/backend/core/ai_provider.py`)
- Bổ sung enum: `AIProvider.COMPATIBLE = "compatible"`.
- Bổ sung `PROVIDER_DEFAULTS[AIProvider.COMPATIBLE] = "gemini-2.5-flash"`.
- Cập nhật `AIClient.__init__` nhận thêm tham số `base_url: Optional[str] = None`.
- Cập nhật luồng xử lý `_openai_stream` truyền `base_url` vào `openai.AsyncOpenAI(api_key=..., base_url=..., timeout=55.0)`.
- Cập nhật hàm factory `get_ai_client()`:
  - Nhận diện `AI_PROVIDER=compatible`.
  - Kiểm tra `AI_API_KEY` (hoặc `GEMINI_API_KEY`). Nếu thiếu, báo lỗi rõ ràng: `AIKeyMissingError: GEMINI_API_KEY REQUIRED: No AI_API_KEY or GEMINI_API_KEY configured for compatible provider.`.
  - Kiểm tra `AI_BASE_URL`. Nếu thiếu, báo lỗi: `ValueError: AI_BASE_URL is required when AI_PROVIDER=compatible.`.
  - Giữ nguyên thứ tự ưu tiên cấu hình người dùng và fallback an toàn sang Anthropic, OpenAI, OpenRouter.

### 3.2. Settings & Database
- `tools/ViralMint/backend/config.py`: Khai báo các trường cấu hình `AI_PROVIDER`, `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL`, `GEMINI_API_KEY`.
- `tools/ViralMint/backend/models/user_settings.py`: Thêm cột `ai_base_url` (VARCHAR 500).
- `tools/ViralMint/backend/database.py`: Thêm migration tự động `_add_column_if_missing(conn, "user_settings", "ai_base_url", "VARCHAR(500)")`.

### 3.3. Sửa lỗi Subtitle Path Quoting (`tools/ViralMint/backend/services/caption_service.py`)
- Sửa lỗi FFmpeg filter parsing khi đường dẫn chứa khoảng trắng (ví dụ `E:\Affiliate ver 1\...`). Bọc đường dẫn trong single quotes: `f"ass='{escaped_ass}'"`.

### 3.4. Cập nhật Giao diện Vietnamese UI (`tools/ViralMint/frontend/src/pages/tools/Translate.jsx`)
- Thêm ngôn ngữ tiếng Việt vào dropdown:
  ```javascript
  { code: "vi", name: "Vietnamese" }
  ```
- Thêm hai giọng Edge-TTS tiếng Việt chất lượng cao vào dropdown lồng tiếng (Full Dub):
  ```javascript
  { id: "vi-VN-HoaiMyNeural", label: "Hoài My (Female, Vietnamese)" },
  { id: "vi-VN-NamMinhNeural", label: "Nam Minh (Male, Vietnamese)" },
  ```
- Đã rebuild gói production frontend (`npm run build` thành công, lưu tại `frontend/dist`).

---

## 4. Kết quả Kiểm thử

### 4.1. Unit Tests (`tests/test_compatible_provider.py`)
Đã thực thi 7 test cases độc lập:
1. `test_compatible_provider_config`: **PASS** (khởi tạo đúng `provider="compatible"`, `base_url`, `api_key`, `model`).
2. `test_base_url_passed_correctly`: **PASS** (truyền đúng URL vào `AsyncOpenAI`).
3. `test_missing_key_fails_clearly`: **PASS** (ném lỗi rõ ràng `GEMINI_API_KEY REQUIRED`).
4. `test_missing_base_url_fails_clearly`: **PASS** (ném `ValueError` khi thiếu URL).
5. `test_existing_providers_no_regression`: **PASS** (OpenAI, Anthropic, OpenRouter tiếp tục hoạt động chính xác).
6. `test_vietnamese_language_in_ui`: **PASS** (code `vi`, name `Vietnamese` tồn tại).
7. `test_vietnamese_tts_voices_in_ui`: **PASS** (`vi-VN-HoaiMyNeural` & `vi-VN-NamMinhNeural` tồn tại).

### 4.2. E2E Pipeline Live Test (`tests/test_e2e_translate_flow.py`)
- Video đầu vào: `data/samples/real_douyin_sample1.mp4` (video Douyin tiếng Trung thực tế).
- Tiến trình thực tế:
  1. ASR trích xuất 20 phân đoạn thoại tiếng Trung qua Whisper local.
  2. Kích hoạt bước dịch qua provider `compatible` hướng tới Gemini endpoint.
  3. Do trong môi trường hệ thống chưa cấu hình giá trị `GEMINI_API_KEY` (chuỗi rỗng), pipeline dừng chính xác tại điểm kiểm tra key:
     `TASK FAIL tool:translate: GEMINI_API_KEY REQUIRED: No AI_API_KEY or GEMINI_API_KEY configured for compatible provider.`
- Bằng chứng lưu tại: `data/evidence/viralmint/e2e_gemini_vietnamese_result.json`.

---

## 5. Phân định Trách nhiệm Kiến trúc (Architecture Decisions)

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
