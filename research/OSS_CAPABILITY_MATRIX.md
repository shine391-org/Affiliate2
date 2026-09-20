# OSS CAPABILITY MATRIX — Phase 1 Validation

Tài liệu ma trận đánh giá năng lực thực tế của hai bộ mã nguồn mở: **MediaCrawler** (Discovery & Metadata) và **ViralMint** (Media Pipeline & Processing).

Mọi kết luận đều dựa trên dữ liệu thực thi, invocation thật vào mã nguồn OSS và nhật ký lỗi thực tế lưu tại thư mục `data/evidence/`.

**Quy ước Verdict**:
- `PASS`: Đáp ứng đầy đủ tiêu chí kiểm thử thực tế bằng invocation thật, có dữ liệu output xác thực.
- `PARTIAL`: Hoạt động một phần, thiếu khâu tự chủ cục bộ hoặc phụ thuộc dịch vụ ngoài.
- `FAIL`: Bị chặn hoàn toàn, không cung cấp tính năng hoặc kiểm thử thực tế không vượt qua.
- `NOT TESTED`: Chưa kiểm thử.

---

## 1. MediaCrawler Capability Matrix

| STT | Khả năng (Capability) | Nền tảng | Verdict | Bằng chứng thực nghiệm (Evidence) & Chẩn đoán kỹ thuật |
| :--- | :--- | :--- | :--- | :--- |
| 1.1 | Keyword Search & Discovery | Bilibili | **PASS** | Chạy 2 keyword search (`AI`, `Python`) lấy thành công **45 records** độc lập. Dữ liệu lưu tại `data/evidence/mediacrawler/bilibili/search_AI_*.json` và `search_Python_*.json`. |
| 1.2 | Metadata Completeness | Bilibili | **PASS** | Đủ 100% trường: `bvid`, `arcurl`, `author`, `mid`, `pubdate`, `title`, `description`, `play`, `like`, `review`, `favorites`, `duration`, `pic`. Đủ để tính Freshness, Engagement, Baseline & Outlier Score. |
| 1.3 | Creator Feed / Profile | Bilibili | **PARTIAL** | API `get_creator_videos` và detail gọi trực tiếp không kèm WBI sign bị HTTP 412 (WAF policy). Cần adapter gán WBI sign tương tự search API. |
| 1.4 | Headless / Unauthenticated | Bilibili | **PASS** | Không yêu cầu đăng nhập tài khoản để tìm kiếm và lấy metadata. |
| 1.5 | Keyword Search & Discovery | Douyin | **FAIL** | - **Direct API**: Trả về HTTP 200 nhưng `status_code: 2483` (Argus WAF / WAF risk control).<br>- **CDP Mode**: Kết nối CDP tới trình duyệt Chrome thật, nhưng search URL `/search/{keyword}?type=video` bị chặn hoàn toàn bởi Modal bắt buộc đăng nhập ("登录后免费看" / "扫码登录"). DOM không render thẻ video (0 cards), không phát sinh request dữ liệu search. Trả về **0/20 records**.<br>- **App-Bound Encryption**: Cookie Chrome trên Windows (Chrome 127+) mã hóa bằng `v20` (`elevation_service.exe`), không thể export cookie sang profile độc lập.<br>- Minh chứng: `data/evidence/mediacrawler/douyin/cdp_validation_result.json`, `cdp_search_AI.png`, `cdp_search_Python.png`. |
| 1.6 | Video Detail & Auth | Douyin | **FAIL** | API detail yêu cầu cookie phiên đăng nhập cá nhân hợp lệ (`sessionid`, `sessionid_ss`, `UIFID`, `s_v_web_id`). Thiếu session dẫn đến `account blocked` / `Signature Not Found`. |
| 1.7 | Keyword Search & Discovery | Xiaohongshu | **FAIL** | Optional test. Trả về `DataFetchError: 您当前登录的账号没有权限访问` (HTTP 403). Bắt buộc phải có cookie phiên đăng nhập hợp lệ. |
| 1.8 | Keyword Search & Discovery | Kuaishou | **PARTIAL** | Tìm kiếm metadata cấp 1 trả về **21 records** (`data/evidence/mediacrawler/kuaishou/`), nhưng API lấy bình luận bị chặn bởi Slider Captcha (`ANTICRAWL_COMMON`). |

---

## 2. ViralMint Capability Matrix

| STT | Khả năng (Capability) | Nền tảng / Engine | Verdict | Bằng chứng thực nghiệm (Evidence) & Chẩn đoán kỹ thuật |
| :--- | :--- | :--- | :--- | :--- |
| 2.1 | Media Ingestion / Input | MP4, MKV, WebM, Local | **PASS** | 8/8 file input được nạp và phân tích cấu trúc thành công qua `ffprobe` (`data/evidence/viralmint/input_validation.json` & `real_douyin_pipeline_summary.json`): bao gồm 2 video Douyin thực tế tải từ ByteDance CDN, 2 video Bilibili, và 1 local benchmark. |
| 2.2 | Chinese ASR (Bóc băng tiếng Trung) | faster-whisper (base/fast) | **PASS** | 7/7 video tiếng Trung (bao gồm `real_douyin_sample1` và `real_douyin_sample2`) được bóc băng chính xác 100% ngôn ngữ `zh`, có đầy đủ timestamps theo câu/từ (`data/evidence/viralmint/transcripts/`). Giữ nguyên các thuật ngữ kỹ thuật (LLM, Token, Context, Agent Skill). |
| 2.3 | Content Analysis | ViralMint `AnalyzerAgent` | **PARTIAL** | ViralMint cung cấp các prompt mẫu có cấu trúc (`INSIGHTS_PROMPT`, `SEGMENT_ANALYSIS_PROMPT`, `IMPROVEMENT_PROMPT`) trong `backend/agents/analyzer.py`. Tuy nhiên, ViralMint **hoàn toàn không có local/offline inference model** mà phụ thuộc 100% vào Cloud LLM API (OpenAI/Anthropic/OpenRouter). Nếu không có BYOK key, `get_ai_client()` ném ngoại lệ `AIKeyMissingError`.<br>- Minh chứng invocation thật: `data/evidence/viralmint/viralmint_real_capability_audit.json`. |
| 2.4 | Vietnamese Adaptation | ViralMint `GeneratorAgent` | **FAIL** | ViralMint **hoàn toàn không có module, hàm hoặc prompt chuyển thể tiếng Việt** trong toàn bộ codebase. Upstream chỉ có prompt tiếng Anh `SCRIPT_PROMPT` trong `backend/agents/generator_prompts.py` để sinh script tiếng Anh cho TikTok/Shorts qua Cloud LLM. Không có từ điển thuật ngữ, không có prompt chuyển thể văn phong short-form Việt Nam.<br>- Minh chứng audit thật: `data/evidence/viralmint/viralmint_real_capability_audit.json`. |
| 2.5 | Vietnamese TTS (Lồng tiếng) | Edge-TTS (`vi-VN-HoaiMyNeural`, `vi-VN-NamMinhNeural`) | **PASS** | 5/5 mẫu audio sinh thành công, âm thanh trong trẻo, không bị ngắt/truncate (`data/evidence/viralmint/tts_summary.json`). Cần Adapter chuẩn hóa ký tự đặc biệt (dấu `:` trong tỷ lệ `9:16`). |
| 2.6 | Video Render 9:16 | FFmpeg (libx264 + aac) | **PASS** | 5/5 video render đạt chuẩn kích thước dọc **1080x1920 (9:16)**, bao gồm 2 video Douyin thật (`real_douyin_sample1` và `real_douyin_sample2`). Hình ảnh sắc nét, scale & pad chuẩn, không lỗi corrupt (`data/evidence/viralmint/renders/`). |
| 2.7 | Background Audio Mixing | FFmpeg `mix_audio` (Ducking -14dB) | **PASS** | 2/2 mẫu thử nghiệm phối trộn nhạc nền (BGM) với giọng đọc tiếng Việt đạt chuẩn: BGM giảm -14dB kèm fade in/out, giọng đọc rõ ràng (`data/evidence/viralmint/audio_mix_summary.json`). |
| 2.8 | Batch Processing | Async Worker (`asyncio.gather`) | **PASS** | Chạy đồng thời batch 3 video thành công trong **5.69 giây**. Cơ chế try/catch cô lập lỗi độc lập giữa các tác vụ (`data/evidence/viralmint/batch_summary.json`). |
| 2.9 | Programmatic Integration | FastAPI REST API | **PASS** | Kiểm thử thành công toàn bộ luồng: Nạp video qua `POST /api/downloaded/import` -> Kiểm tra trạng thái job qua `GET /api/jobs/{id}` -> Truy xuất kết quả qua `GET /api/downloaded/{id}` (`data/evidence/viralmint/api_integration_summary.json`). |
| 2.10 | Local Benchmark | Local CPU / Memory | **PASS** | Đo đạc chi tiết tài nguyên thực tế trên 1 video đơn (11.07s, 314.7MB RAM) và batch 3 video (`data/evidence/viralmint/local_benchmark_summary.json`). |

---

## 3. Tổng kết Exit Gate Phase 1

- **Bilibili Discovery**: **PASS** (45/20 records, metadata đầy đủ).
- **Douyin Discovery**: **FAIL** (CDP và Direct API đều bị WAF Argus và Login Wall chặn; Chrome App-Bound Encryption `v20` ngăn tái sử dụng profile).
- **Real Douyin Media Inputs**: **PASS** (2 video thật tải từ ByteDance CDN chạy trọn vẹn qua Input -> ASR -> Render 9:16).
- **ViralMint Content Analysis**: **PARTIAL** (Có prompt template & data schema nhưng không có local LLM, phụ thuộc 100% cloud BYOK key).
- **ViralMint Vietnamese Adaptation**: **FAIL** (Không tồn tại trong upstream ViralMint; phải xây module riêng).
- **KẾT LUẬN EXIT GATE**: `STATUS = PHASE 1 BLOCKED` do nguồn Douyin Discovery chưa khả dụng cục bộ không tài khoản.
