# KNOWN GAPS & TECHNICAL RISKS — Phase 1 Validation

Tài liệu tổng hợp các khoảng trống kỹ thuật, rào cản và rủi ro được phát hiện trong quá trình kiểm thử thực tế mã nguồn mở.

---

## 1. Khoảng trống trong MediaCrawler & Các nền tảng mạng xã hội Trung Quốc

### Gap 1.1: Cơ chế phòng thủ Argus / WAF của Douyin (Critical Gap)
- **Mô tả hiện tượng**:
  - Khi gọi API tìm kiếm `/aweme/v1/web/general/search/single/`, Douyin trả về `status_code: 2483`.
  - Khi gọi API chi tiết `/aweme/v1/web/aweme/detail/`, hệ thống trả về lỗi `account blocked` hoặc `Signature Not Found`.
  - Khi chạy Playwright ở chế độ `headless: True`, trang đăng nhập ẩn nút đăng nhập hoặc hiển thị captcha trượt, dẫn đến timeout 30s.
  - Khi dùng `yt-dlp` tải trực tiếp link Douyin, server trả về `HTTP 403: Forbidden / Fresh cookies needed`.
- **Nguyên nhân gốc rễ (Root Cause)**:
  - Douyin áp dụng hệ thống bảo mật ArgusSecurityPlugin cực kỳ nghiêm ngặt. Để vượt qua, client bắt buộc phải có cặp cookie đồng bộ (`UIFID` và `s_v_web_id`) kèm chữ ký thuật toán JS (`a_bogus`). Các request từ IP datacenter hoặc browser headless chưa có fingerprint người dùng thật đều bị đánh dấu rủi ro cao.
- **Giải pháp khả thi (Mitigation)**:
  - **Phương án A (Khuyến nghị)**: Sử dụng chế độ CDP kết nối với Google Chrome của người dùng (`--remote-debugging-port=9222`) có sẵn cookie đăng nhập Douyin.
  - **Phương án B (Dự phòng)**: Sử dụng dịch vụ API bên thứ ba (ví dụ TikHub API như ViralMint đã tích hợp sẵn) nếu muốn hoàn toàn không phụ thuộc browser local.

---

### Gap 1.2: Rào cản đăng nhập của Xiaohongshu (XHS)
- **Mô tả hiện tượng**:
  - Lệnh tìm kiếm trả về `DataFetchError: 您当前登录的账号没有权限访问` (HTTP 403).
- **Nguyên nhân gốc rễ**:
  - Xiaohongshu không cho phép người dùng ẩn danh (khách vãng lai) tìm kiếm qua API web. Bắt buộc phải có cookie `a1` và session hợp lệ.
- **Đánh giá rủi ro**:
  - Không block Phase 1 (XHS là optional). Cần adapter nạp cookie thủ công nếu muốn kích hoạt nền tảng này trong tương lai.

---

### Gap 1.3: Captcha chống cào dữ liệu của Kuaishou
- **Mô tả hiện tượng**:
  - Tìm kiếm bài viết thành công (đã lấy được 21 records vào `data/evidence/mediacrawler/kuaishou/`), nhưng khi cào bình luận (comments), server Kuaishou trả về `REST API V2 error 400002: ANTICRAWL_COMMON` yêu cầu giải captcha trượt iframe.
- **Đánh giá rủi ro**:
  - Mức độ thấp. Việc khám phá nội dung (Content Discovery) vẫn khả thi, chỉ cần bỏ cờ `--get_comment` khi quét từ khóa.

---

### Gap 1.4: Rào cản kiểm tra đăng nhập bắt buộc trong MediaCrawler Bilibili Core
- **Mô tả hiện tượng**:
  - Lệnh gốc `main.py --platform bili` tự động thoát với `sys.exit(1)` nếu không phát hiện `SESSDATA` (tài khoản đã đăng nhập).
- **Nguyên nhân gốc rễ**:
  - Upstream `core.py` gài điều kiện kiểm tra nghiêm ngặt nhằm đảm bảo người dùng tải được video 1080p/4K. Tuy nhiên, với vai trò **tìm kiếm nội dung và trích xuất metadata**, Bilibili API hoàn toàn mở cho khách vãng lai nếu có chữ ký WBI.
- **Giải pháp khả thi**:
  - Áp dụng quyết định kiến trúc **REUSE WITH ADAPTER**: Dự án tự viết một adapter mỏng bọc `BilibiliClient` và `BilibiliSign`, tái sử dụng nguyên vẹn logic ký WBI và trích xuất dữ liệu của MediaCrawler mà không kích hoạt đoạn code `sys.exit(1)` ở `core.py`.

---

## 2. Khoảng trống trong ViralMint & Pipeline xử lý Media

### Gap 2.1: Nhạy cảm ký tự phân tách tỷ lệ trong Edge-TTS
- **Mô tả hiện tượng**:
  - Khi văn bản có chứa chuỗi dạng tỷ lệ có dấu hai chấm như `9:16`, Edge-TTS WebSocket báo lỗi `NoAudioReceived`.
- **Nguyên nhân gốc rễ**:
  - Client Edge-TTS đóng gói văn bản vào XML/SSML payload. Ký tự hai chấm trong ngữ cảnh này bị parser XML của dịch vụ Microsoft hiểu nhầm thành namespace prefix hoặc lỗi cú pháp SSML.
- **Giải pháp khả thi**:
  - Thêm bộ tiền xử lý chuẩn hóa văn bản (Text Normalizer Adapter) trước khi gửi tới TTS (chuyển đổi số, tỷ lệ `9:16` -> `chín mười sáu` hoặc `9 trên 16`).

---

### Gap 2.2: Phụ thuộc mã hóa luồng subprocess trên hệ điều hành Windows
- **Mô tả hiện tượng**:
  - Khi gọi `PyExecJS` hoặc pipe dữ liệu chứa ký tự tiếng Trung/tiếng Việt ra console hoặc subprocess trên Windows, xuất hiện `UnicodeEncodeError: 'charmap' codec can't encode characters`.
- **Nguyên nhân gốc rễ**:
  - Mặc định Windows sử dụng bảng mã ANSI (cp1252) cho standard streams nếu không có cờ UTF-8.
- **Giải pháp khả thi**:
  - Thiết lập biến môi trường chuẩn `PYTHONUTF8=1` và `PYTHONIOENCODING=utf-8` trong toàn bộ script thực thi và virtualenv.

---

### Gap 2.3: Thiếu vắng Module Candidate Ranking trong OSS
- **Mô tả hiện tượng**:
  - Cả MediaCrawler và ViralMint đều không cung cấp thuật toán chấm điểm tiềm năng (Candidate Ranking), tính tỷ lệ tương tác vượt trội (Outlier Score) hay so sánh creator baseline.
- **Quyết định**:
  - Phân loại rõ ràng: **BUILD**. Đây chính là thành phần cốt lõi của **Source Radar MVP** cần xây dựng ở Phase 2.
