# KNOWN GAPS & TECHNICAL RISKS — Phase 1 Validation

Tài liệu tổng hợp các khoảng trống kỹ thuật, rào cản và rủi ro được phát hiện thông qua kiểm thử thực nghiệm trên hệ thống.

---

## 1. Khoảng trống trong MediaCrawler & Khám phá Nguồn (Discovery)

### Gap 1.1: Rào cản Douyin WAF, Mandatory Login Wall & Thất bại của CDP Mode (Critical Gap)
- **Mô tả hiện tượng**:
  - **Direct Search API**: `/aweme/v1/web/general/search/single/` trả về `status_code: 2483` (Argus Anti-Crawler WAF).
  - **Direct Detail API**: `/aweme/v1/web/aweme/detail/` trả về lỗi `account blocked` / `Signature Not Found`.
  - **CDP Mode với Chrome Profile**: Đã triển khai kiểm thử CDP kết nối tới trình duyệt Chrome thật (`tests/validate_mediacrawler_douyin_cdp.py`). Kết quả: **FAIL** (thu được **0/20 records**).
- **Phân tích nguyên nhân gốc rễ (Root Cause)**:
  1. **Mandatory Login Wall trên giao diện Web Search**: Trang tìm kiếm `https://www.douyin.com/search/{kw}?type=video` tự động kích hoạt modal đăng nhập bắt buộc ("登录后免费看" / "扫码登录"). Toàn bộ danh sách video card trong DOM bị chặn không hiển thị (0 cards), và frontend không dispatch các request API lấy danh sách video nếu không có phiên đăng nhập đã xác thực.
  2. **Thiếu phiên đăng nhập tài khoản thực tế**: Kiểm tra cơ sở dữ liệu cookie của trình duyệt cục bộ cho thấy profile chỉ chứa 30 cookie vãng lai/tracking (`UIFID`, `s_v_web_id`, `ttwid`, `passport_auth_mix_state`), hoàn toàn **không có cookie tài khoản đăng nhập (`sessionid`, `sessionid_ss`)**.
  3. **Chrome App-Bound Encryption (`v20`) trên Windows**: Kể từ Chrome 127+ (máy hiện cài Chrome 153), cookie trong SQLite `Network/Cookies` được mã hóa bằng dịch vụ bảo mật Windows `elevation_service.exe` với tiền tố `v20`. Không thể sao chép hoặc trích xuất cookie sang profile riêng biệt bên ngoài quy trình chính thức của chính executable Chrome đó.
  4. **Cổng CDP 9222 không mở sẵn**: Trình duyệt Chrome đang chạy thông thường không bật cổng gỡ lỗi từ xa trừ khi người dùng khởi động với cờ `--remote-debugging-port=9222` hoặc kích hoạt trong `chrome://inspect/#remote-debugging`.

---

### Gap 1.2: Rào cản đăng nhập của Xiaohongshu (XHS)
- **Mô tả hiện tượng**:
  - Lệnh tìm kiếm trả về `DataFetchError: 您当前登录的账号没有权限访问` (HTTP 403).
- **Nguyên nhân gốc rễ**:
  - Xiaohongshu không cho phép người dùng ẩn danh (khách vãng lai) tìm kiếm qua API web. Bắt buộc phải có cookie `a1` và session hợp lệ.

---

### Gap 1.3: Captcha chống cào dữ liệu của Kuaishou
- **Mô tả hiện tượng**:
  - Tìm kiếm bài viết thành công (thu thập được 21 records vào `data/evidence/mediacrawler/kuaishou/`), nhưng khi cào bình luận (comments), server Kuaishou trả về `REST API V2 error 400002: ANTICRAWL_COMMON` yêu cầu giải captcha trượt iframe.
- **Đánh giá rủi ro**:
  - Mức độ thấp cho discovery metadata cấp 1. Chỉ cần tắt cờ cào bình luận khi quét xu hướng.

---

### Gap 1.4: Rào cản kiểm tra đăng nhập bắt buộc trong MediaCrawler Bilibili Core
- **Mô tả hiện tượng**:
  - Lệnh gốc `main.py --platform bili` tự động thoát với `sys.exit(1)` nếu không phát hiện `SESSDATA` (tài khoản đã đăng nhập).
- **Nguyên nhân gốc rễ**:
  - Upstream `core.py` gài điều kiện kiểm tra nghiêm ngặt nhằm đảm bảo người dùng tải được video 1080p/4K. Tuy nhiên, với vai trò **tìm kiếm nội dung và trích xuất metadata**, Bilibili API hoàn toàn mở cho khách vãng lai nếu có chữ ký WBI.
- **Giải pháp**:
  - Tái sử dụng `BilibiliClient` và logic WBI signing qua Adapter chuyên biệt, bỏ qua assertion đăng nhập của `core.py`.

---

## 2. Khoảng trống trong ViralMint & Pipeline Xử lý Nội dung

### Gap 2.1: ViralMint Content Analysis phụ thuộc 100% Cloud BYOK LLM
- **Mô tả hiện tượng**:
  - Kiểm thử invocation trực tiếp `AnalyzerAgent` trên 3 video thật (`tests/verify_viralmint_real_capability.py`) bị chặn ngay lập tức với lỗi `AIKeyMissingError: No AI provider configured`.
- **Nguyên nhân gốc rễ**:
  - ViralMint upstream chỉ cung cấp prompt templates (`INSIGHTS_PROMPT`, `SEGMENT_ANALYSIS_PROMPT`) và schema dữ liệu, **hoàn toàn không tích hợp mô hình cục bộ (local/offline model)**. Dự án phụ thuộc hoàn toàn vào việc người dùng tự cung cấp API Key (OpenAI, Anthropic, OpenRouter).
- **Quyết định kiến trúc**:
  - `REUSE WITH ADAPTER`: Tận dụng prompt template và schema của ViralMint, nhưng xây Adapter để kết nối với LLM service hoặc local LLM được quản lý tập trung.

---

### Gap 2.2: ViralMint hoàn toàn không có tính năng Việt hóa (Vietnamese Adaptation)
- **Mô tả hiện tượng**:
  - Toàn bộ codebase ViralMint không có bất kỳ prompt, class hay module nào phục vụ dịch thuật hoặc chuyển thể tiếng Việt.
  - Upstream chỉ có một prompt tiếng Anh duy nhất `SCRIPT_PROMPT` trong `backend/agents/generator_prompts.py` nhằm tạo kịch bản tiếng Anh cho thị trường phương Tây.
- **Nguyên nhân gốc rễ**:
  - Dự án gốc ViralMint được thiết kế cho người dùng quốc tế sáng tạo nội dung tiếng Anh trên TikTok / YouTube Shorts, không hỗ trợ localization cho thị trường Việt Nam.
- **Quyết định kiến trúc**:
  - `BUILD`: Tự xây dựng module Vietnamese Adaptation Engine riêng biệt (Prompt chuyển thể văn phong short-form tiếng Việt, xử lý thuật ngữ song ngữ, căn chỉnh độ dài nhịp điệu phát âm).

---

### Gap 2.3: Nhạy cảm ký tự phân tách tỷ lệ trong Edge-TTS
- **Mô tả hiện tượng**:
  - Khi văn bản có chứa chuỗi dạng tỷ lệ có dấu hai chấm như `9:16`, Edge-TTS WebSocket báo lỗi `NoAudioReceived`.
- **Nguyên nhân gốc rễ**:
  - Client Edge-TTS đóng gói văn bản vào XML/SSML payload. Ký tự hai chấm trong ngữ cảnh này bị parser XML của dịch vụ Microsoft hiểu nhầm thành namespace prefix hoặc lỗi cú pháp SSML.
- **Giải pháp**:
  - Thêm bộ tiền xử lý chuẩn hóa văn bản (Text Normalizer Adapter) trước khi gửi tới TTS (chuyển đổi `9:16` -> `chín mười sáu` hoặc `9 trên 16`).

---

### Gap 2.4: Thiếu vắng Module Candidate Ranking trong OSS
- **Mô tả hiện tượng**:
  - Cả MediaCrawler và ViralMint đều không cung cấp thuật toán chấm điểm tiềm năng (Candidate Ranking), tính tỷ lệ tương tác vượt trội (Outlier Score) hay so sánh creator baseline.
- **Quyết định kiến trúc**:
  - `BUILD`: Xây dựng Candidate Ranking Engine dựa trên SQLite.
