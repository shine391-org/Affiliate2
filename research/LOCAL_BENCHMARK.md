# LOCAL BENCHMARK — Phase 1 Validation

Tài liệu ghi nhận số liệu đo đạc thực tế về hiệu năng phần cứng, mức tiêu thụ tài nguyên và các điểm nghẽn (bottlenecks) khi vận hành pipeline cục bộ trên máy trạm.

Dữ liệu thô được lưu trữ tại `data/evidence/viralmint/local_benchmark_summary.json`.

---

## 1. Môi trường kiểm chuẩn (Testbed Environment)

| Thông số | Giá trị thực tế |
| :--- | :--- |
| **Hệ điều hành** | Windows 10/11 x86_64 |
| **CPU** | Đa nhân (Logical Cores: 32 cores) |
| **RAM hệ thống** | 31.8 GB |
| **Python Runtime** | CPython 3.11.16 (`uv` managed virtual environment) |
| **FFmpeg Version** | 9.0.1-full_build (gyan.dev) |
| **Trình tăng tốc ASR** | faster-whisper (CTranslate2 backend trên CPU / INT8 quantization) |
| **Engine TTS** | Microsoft Edge TTS WebSocket |

---

## 2. Kết quả đo đạc thời gian và tài nguyên

### 2.1. Đơn tác vụ: Pipeline hoàn chỉnh cho 1 video (Single Video Pipeline)
Quy trình: Video Ingestion → Chinese ASR (faster-whisper) → Content Analysis → Script Adaptation → Edge-TTS → Render 9:16 (FFmpeg)

| Công đoạn | Thời gian xử lý (Latency) | RAM tiêu thụ đỉnh (Peak RSS) | Ghi chú tài nguyên |
| :--- | :--- | :--- | :--- |
| **ASR (faster-whisper)** | 2.50 s | ~280 MB | Tải model `base` (~140MB), CPU decode âm thanh |
| **Content Analysis & Adaptation** | 0.05 s | ~285 MB | Xử lý JSON & phân tích cấu trúc kịch bản |
| **Vietnamese TTS (Edge-TTS)** | 2.80 s | ~290 MB | Phụ thuộc độ trễ mạng tới Microsoft CDN |
| **FFmpeg Render 9:16 (libx264)** | 5.72 s | ~314 MB | Scale 1080x1920, Crop, Mux âm thanh AAC |
| **Tổng toàn bộ quy trình (E2E)** | **11.07 giây** | **314.7 MB** | File xuất xấp xỉ 1.3 MB (1080x1920, 20 giây) |

### 2.2. Xử lý đồng thời: Batch 3 video (Concurrent Batch Pipeline)
Chạy đồng thời 3 tác vụ ASR & phân tích dữ liệu qua `asyncio.gather`:

| Tác vụ | Thời gian thực thi | RAM tiêu thụ đỉnh | Tỷ lệ thành công |
| :--- | :--- | :--- | :--- |
| **Batch 3 Videos ASR** | **5.69 giây** | **313.8 MB** | **3/3 (100%)** |

*Nhận xét*: Nhờ kiến trúc không khóa (non-blocking) và CTranslate2 worker hiệu quả, chạy 3 video đồng thời chỉ mất 5.69 giây (trung bình ~1.9 giây/video), nhanh hơn đáng kể so với chạy tuần tự từng video một.

---

## 3. Dung lượng lưu trữ đĩa cứng (Disk Usage Footprint)

| Thư mục lưu trữ | Dung lượng xấp xỉ | Mô tả |
| :--- | :--- | :--- |
| `tools/MediaCrawler` | ~45 MB | Mã nguồn và script JS của crawler |
| `tools/ViralMint` | ~120 MB | Mã nguồn backend, API và frontend static |
| `data/samples/` | ~9.5 MB | 7 video mẫu (Bilibili, Douyin-derived, Local) |
| `data/evidence/` | ~18.5 MB | Toàn bộ bằng chứng: JSON metadata, MP3 TTS, MP4 Render 9:16 |
| `.venv/` | ~1.4 GB | Các gói phụ thuộc Python (Playwright, Torch/CTranslate2, FFmpeg wrappers) |
| **Tổng dung lượng Phase 1** | **~1.6 GB** | Hoàn toàn nằm trong mức kiểm soát của máy trạm local |

---

## 4. Xác định các điểm nghẽn chính (Major Bottlenecks)

1. **FFmpeg CPU Re-encoding Bottleneck**:
   - Việc biến đổi video ngang (16:9) sang video dọc (9:16) bằng bộ lọc `scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920` chiếm hơn 50% tổng thời gian E2E khi dùng phần mềm mã hóa CPU (`libx264`).
   - *Khuyến nghị tương lai*: Chuyển sang phần cứng tăng tốc phần cứng (`h264_nvenc` hoặc `h264_qsv`) nếu máy có GPU rời, hoặc dùng preset `fast`/`veryfast`.
2. **Network Latency của Edge-TTS**:
   - Edge-TTS là dịch vụ cloud miễn phí của Microsoft, độ trễ phụ thuộc vào kết nối mạng quốc tế (dao động 2s - 6s cho mỗi câu thoại 20-30 từ).
   - Khi chạy liên tục nhiều request song song, có thể gặp hiện tượng `NoAudioReceived` nếu không có cơ chế giãn cách hoặc retry.
3. **Whisper Memory Footprint**:
   - Model `base` tiêu thụ ~150MB RAM. Nếu chuyển sang model `medium` hoặc `large-v3` để tăng độ chính xác tiếng Trung địa phương, RAM sẽ tăng lên 1.5GB - 3.0GB.
   - Cần giữ cơ chế `_evict_when_idle` như ViralMint đã cài đặt sẵn để giải phóng bộ nhớ khi không có tác vụ.
