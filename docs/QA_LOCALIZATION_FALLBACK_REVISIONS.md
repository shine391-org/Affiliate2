# Giọng dự phòng, phiên bản nhật ký và mốc lời nói

Ngày: 2026-10-04 (Việt Nam). Nhánh: `feat/localization-fast-mode`.
Bản nền: `c04d76e29a62af8848df52aed954d142f0bd4aec`. Không gộp vào `master`.

## Thay đổi trong phạm vi yêu cầu

- Ưu tiên giọng khác nhau, cùng giới nguồn; hết giọng phù hợp chưa dùng thì dùng lại giọng phù hợp, không có giọng phù hợp thì dùng giọng mặc định/cấu hình. Lỗi tải danh mục có thời hạn chờ và tiếp tục bằng lựa chọn dự phòng. Bỏ lỗi dừng `matching-gender` và phân loại lỗi tương ứng. Danh mục thành công được lưu theo kết nối của công việc; các mặc định có sẵn chỉ hỗ trợ dự phòng, không thay thế danh mục thực đã tải.
- Cảnh báo lưu tại `dubbing_state.json`, `transcript.metadata.speakers.voice_warnings` và báo cáo QC. Giới tính cảnh báo dựa trên giọng thực sự tổng hợp của từng khối, kể cả giọng sửa riêng từng đoạn. Mâu thuẫn giới tính chỉ được kết luận khi nguồn đã xác nhận; chưa xác nhận dùng cảnh báo bất định. Dùng lại giọng chỉ làm QC thành `NEEDS_REVIEW`, không làm C7 thất bại.
- Mỗi dòng nhật ký có `acceptance_key`. `rewrite_count` chỉ đếm ID đoạn thay đổi đã chấp nhận thuộc khóa hiện tại. Đổi đầu vào hoặc vô hiệu hóa bản dịch/giọng/TTS tạo phiên bản mới; đổi A → B → A không hồi sinh số đếm cũ. Nhật ký bền vững và metadata giai đoạn được đặt về số đếm hiện tại ngay khi vô hiệu hóa. Thử lại C7 cùng đầu vào giữ bản rút gọn và khối âm thanh hợp lệ. Nhật ký cũ không xác định phiên bản được gắn `legacy-unscoped`, không cộng vào hiện tại. Khi nâng cấp từ c04, chỉ chuyển dòng cuối chứng minh từng văn bản rút gọn đang lưu nếu dấu vân tay đầu vào cũ vẫn khớp.
- `_speech_onset_ms()` bỏ qua từng từ hỏng/rỗng/không hữu hạn/ngoài đoạn, vẫn chọn mốc sớm nhất hợp lệ; chỉ dùng đầu đoạn khi không còn mốc hợp lệ. Khóa đầu và cuối tách riêng. Khóa cuối không ngăn lấy mốc đầu từ ASR; khoảng trống vẫn chỉ được mượn ở cạnh không khóa, trong giới hạn cũ. Các lời gọi cũ dùng `timing_locked=True` tiếp tục khóa cả hai cạnh.
- Không thay bộ máy hay thêm giai đoạn/phụ thuộc. C6 Việt hóa tên và bản dịch đã duyệt vẫn bất biến. Viết lại vẫn cần thời lượng TTS thật vượt tốc độ tối đa. Vị trí nội bộ của khối nhiều câu vẫn là ước lượng; giữ nhãn `estimated_chunk_weights` và cảnh báo QC `dubbing_onset_drift`, không gọi đó là căn chỉnh lời tổng hợp.

## Hợp đồng cảnh báo

Cả năm mã dưới đây dùng đúng các trường chung; `reason` là nguyên nhân cụ thể dạng chuỗi, `requires_human_review` luôn bằng `true`:

```json
{
  "code": "speaker_voice_reused",
  "speaker_id": "SPEAKER_01",
  "source_gender": "male",
  "selected_voice": "vi-VN-NamMinhNeural",
  "selected_voice_gender": "male",
  "provider": "edge_tts",
  "reason": "no_second_compatible_voice_selected",
  "requires_human_review": true
}
```

| Mã | Điều kiện |
| --- | --- |
| `speaker_voice_reused` | Giọng phù hợp được dùng chung khi không chọn được giọng phù hợp thứ hai; giữ lý do qua lần chạy lại. |
| `speaker_voice_default_fallback` | Không có giọng phù hợp; dùng giọng đã cấu hình/mặc định. |
| `speaker_gender_mismatch` | Giới tính giọng đã chọn khác giới tính nguồn đã xác nhận. |
| `speaker_gender_unverified` | Giới tính nguồn chưa xác nhận/không rõ hoặc giọng được chọn không rõ giới tính. |
| `speaker_voice_catalog_unavailable` | Danh mục không tải được/rỗng; dùng lựa chọn dự phòng. |

Các cảnh báo QC cũ về diarization, chồng lấn và giọng trùng vẫn tồn tại; không biến chúng thành lỗi làm dừng lồng tiếng.

## Kiểm tra đã chạy

**538 kiểm tra đạt**, một cảnh báo serializer đã có trong fixture ngân sách 0.02 giây; không bỏ qua kiểm tra. Linux/Python 3.12/PyTorch 2.8 CPU. Cài thư viện kiểm tra trong môi trường làm việc, không đổi requirements của ứng dụng.

```sh
cd app
PYTHONPATH=.:.. DEBUG=false python -m pytest -q \
  tests/test_localization_revision_fallback.py tests/test_localization_voice_workflow.py \
  tests/test_localization_measured_speakers.py tests/test_phase_c_word_onset.py \
  tests/test_phase_c_rhythm_allocation.py tests/test_phase_c_provider_binding.py \
  tests/test_phase_c_localization_api.py tests/test_phase_c_localization_stages.py \
  tests/test_phase_c_localization_qc.py tests/test_dubbing_speech_preparation.py \
  tests/test_dubbing_rewrite_guard.py tests/test_phase_c_dubbing_planner.py \
  tests/test_phase_c_rewrite_feedback.py tests/test_phase_c_tts_timing.py tests/test_fast_mode* \
  tests/test_phase_c_alignment.py tests/test_phase_c_localization_soft_overflow.py \
  tests/test_translate_batch_resilience.py tests/test_localization_exceptions.py \
  tests/test_localization_publish_service.py --tb=short --show-capture=no
```

Ruff trên tất cả tệp Python thay đổi và `git diff --check` đạt. Không chạy toàn bộ bộ kiểm tra, không thay giao diện nên không dựng lại frontend.

Các ca chính đi qua C7 và QC: hai nam/một giọng nam; không có giọng nam; lỗi danh mục; cảnh báo lưu và giữ qua chạy lại/tái sử dụng âm thanh; đổi bản dịch/model/ánh xạ/thời gian rồi quay lại đầu vào cũ; vô hiệu hóa đếm ngay lập tức; nâng cấp nhật ký c04 vẫn dùng lại âm thanh; từ lỗi trước/sau từ hợp lệ; khóa cuối giữ mốc lời nói; mỗi cạnh chỉ mượn khoảng trống khi không khóa; khối nhiều câu vẫn báo vị trí ước lượng. Các kiểm tra hiện có về cập nhật nguyên tử, đổi kết nối, thử lại khối chỉ định, tên Việt và đo trước khi viết lại đều nằm trong lệnh trên.

## Giới hạn bằng chứng

Lần này kiểm tra điều khiển luồng, hợp đồng cảnh báo và nhật ký bằng dữ liệu/nhà cung cấp giả lập có thời lượng xác định; các kiểm tra nhịp dùng tệp âm thanh tổng hợp và FFmpeg. Không có video lồng tiếng thực mới được tạo hoặc nghe nghiệm thu trong lần sửa này.

Vẫn chưa nghiệm thu: video hoàn chỉnh một người, hai người, nhiều người cùng giới, hội thoại chồng lấn, độ tự nhiên/ngữ điệu, diarization thật và các nhà cung cấp TTS trực tiếp. Lần kiểm tra trước chỉ xác nhận 34 từ có mốc thời gian được giữ trên âm thanh thật; diarization thiếu token và Edge gặp lỗi chứng chỉ. Kết quả đó không chứng minh chất lượng bản lồng tiếng. Không thử nghiệm Windows/CUDA trong lần này.

## Tệp thay đổi

- `PROJECT_STATUS.md`
- `app/backend/api/localization.py`
- `app/backend/core/tool_runners.py`
- `app/backend/services/dubbing_job_state.py`
- `app/backend/services/dubbing_planner.py`
- `app/backend/services/localization_qc_service.py`
- `app/backend/services/localization_service.py`
- `app/backend/services/localization_stage_service.py`
- `app/backend/services/tts_service.py`
- `app/tests/test_localization_measured_speakers.py`
- `app/tests/test_localization_revision_fallback.py`
- `app/tests/test_localization_voice_workflow.py`
- `app/tests/test_phase_c_word_onset.py`
- `docs/ARCHITECTURE.md`
- `docs/CONSOLIDATION_PLAN.md`
- `docs/QA_LOCALIZATION_FALLBACK_REVISIONS.md`
- `docs/QA_LOCALIZATION_VI_VOICES_WORKFLOW.md`
