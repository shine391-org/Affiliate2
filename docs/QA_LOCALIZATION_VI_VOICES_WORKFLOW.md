# Việt hóa, giọng người nói và tính nhất quán của quy trình

Ngày: 2026-10-03. Nhánh: `feat/localization-fast-mode`.
Bản nền: `09bf5aa1a6e8086219bdd3d19db56cec0a7dff38` (sau lần sửa đầu từ `8d79c40657acb2a9d076bf1f2d4dc405f0311a76`).
Không gộp vào `master`. Đây là báo cáo sửa kỹ thuật và kiểm tra tập trung; chưa phải nghiệm thu chất lượng video lồng tiếng.

## Hành vi đã sửa

| Yêu cầu | Hành vi và bằng chứng |
| --- | --- |
| Tên và tuổi bằng tiếng Việt | C6 yêu cầu tên Hán–Việt/thông dụng, dịch cách xưng hô và danh từ; phụ đề và lời đọc dùng cùng bản đã duyệt. Từ điển tên theo công việc được kiểm tra và lưu trong checkpoint, thống nhất qua các lô. C6/C7 chặn chữ Hán còn sót, trừ ngoại lệ người dùng bảo vệ rõ ràng. Kiểm tra tên Ngô Tiểu Đồng, tuổi 18, số tuổi viết bằng chữ Hán; không còn bước AI phiên âm Pinyin sau khi duyệt. |
| Ý nghĩa và nhịp | Giữ các kiểm tra phủ định, số, đơn vị, thuật ngữ và trái nghĩa theo cụm/hai chiều; bổ sung tuổi, nhân dân tệ, dấu hỏi và cảm thán. Tránh nhận nhầm Đồng trong tên thành tiền hoặc 克 trong tên thành gam. Lời nhắc C6 giữ câu ngắn, ngắt, nhấn và cách hỏi/cảm thán. Giữ ranh giới người nói, khóa thời gian, xử lý nhịp/ngắt trước đây; Edge dùng cao độ trung tính thay vì tăng cố định 2 Hz. |
| Giọng khác nhau và đúng giới tính | Dữ liệu giới tính nguồn thuộc công việc, lấy gợi ý từ âm thanh không chồng lấn và xác nhận của người duyệt. Gợi ý âm vực luôn chưa xác nhận. Giọng đích lấy giới tính từ danh mục nhà cung cấp hoặc xác nhận cho ID không rõ. Hai người cùng giới cần hai giọng phù hợp khác nhau; chỉ bổ sung giọng đa ngôn ngữ hỗ trợ tiếng Việt khi nhà cung cấp đang gắn thực sự công bố giọng. Thiếu giọng phù hợp không được lấp bằng giọng khác giới. |
| Studio và QC | Studio hiển thị người nói, gợi ý âm vực, cho xác nhận nam/nữ và chọn giọng từng người. QC giữ trạng thái cần kiểm tra khi giới tính chưa xác nhận/sai, diarization không thành công, giọng trùng hoặc có chồng lấn chưa giải quyết. Chọn chung giọng không miễn kiểm tra giới tính. Lựa chọn chưa lưu không mất do polling; kết quả yêu cầu cũ không tác động công việc mới khi đổi trang. |
| Không viết lại trước khi đo | C7 giữ điều kiện thời lượng TTS thật và `required_speed > dub_speed_max`. Ước lượng chỉ dùng cho đo lường; không cấp quyền gọi AI. Chuẩn bị lời đọc C7 chỉ kiểm tra văn bản, không gọi AI. Hiệu chuẩn tiếp tục theo công việc và đúng giọng/tham chiếu/văn bản đã tổng hợp. |
| Thử lại không làm đổi bản duyệt | C6 không bị ghi đè bởi C7. Văn bản rút gọn đã chấp nhận được lưu riêng và tái sử dụng khi đầu vào/chính sách/TTS/tham chiếu còn khớp. Kiểm tra lỗi TTS sau khi rút gọn: thử lại dùng bản đã nhận, không gọi AI thêm và không tổng hợp lại khối lân cận còn hợp lệ. |
| Đổi nhà cung cấp và lỗi cập nhật | Đổi TTS/model/kết nối xóa ánh xạ giọng, danh mục, hiệu chuẩn và văn bản rút gọn không tương thích, kể cả trước lần C7 đầu tiên. Giữ dữ liệu nguồn khi chỉ đổi TTS; nhận dạng lại nguồn xóa dữ liệu nhãn người nói cũ. Thay đổi hồ sơ/đoạn dùng giao dịch manifest hiện có: lỗi đoạn không tồn tại khôi phục cả manifest, tệp và trạng thái giọng gốc. |
| Số lần viết lại và nhật ký | `rewrite_count` tiếp tục là số ID đoạn có thay đổi trong nhật ký chấp nhận bền vững của công việc. Nhiều lần sửa một đoạn chỉ tính một. Bản thử nhịp bị loại vẫn có before/after với trạng thái `discarded_cadence_trial`, không cộng số đoạn đã chấp nhận. `rewrite_audit.json` vẫn là hiện vật giai đoạn được khai báo và giữ qua vô hiệu hóa bộ nhớ đệm. |
| Retry có giới hạn | Vòng lặp khối dùng một quyết định lỗi chung, tối đa ba lần cho lỗi tạm thời; bỏ vòng retry lồng bên trong Edge và SDK OpenAI. Lỗi cấu hình/HTTP 4xx xác định dừng; 408/429 cho phép thử lại. Auto-repair giai đoạn vẫn có giới hạn cũ và không tự chuyển nhà cung cấp. Azure giữ endpoint/region đã gắn, danh mục chỉ đọc, lời Việt dùng locale vi-VN cả với giọng đa ngôn ngữ. |

Không thêm giai đoạn, máy xử lý, hàng đợi, dịch vụ hay phụ thuộc ứng dụng. Tiếp tục dùng các lớp provider, manifest transaction, job, transcript và Studio hiện có. Nhãn `SPEAKER_00` và giới tính theo nhãn này không lưu toàn cục vào hồ sơ dùng lại.

## Kiểm tra thực sự đã chạy

Môi trường Linux, Python 3.12, PyTorch 2.8.0 CPU, NumPy, Node 24, FFmpeg; FastAPI 0.141.1 và SQLAlchemy 2.0.52. Cài phụ thuộc phục vụ kiểm tra tại môi trường này, không thay đổi requirements. Bản dựng React dùng phụ thuộc frontend của kho mã.

Backend: **711 đạt**, không bỏ qua, một cảnh báo serializer trong fixture cố ý dùng ngân sách timeout 0.02 giây cho trường số nguyên:

```sh
cd app
PYTHONPATH=.:.. DEBUG=false python -m pytest -q \
  tests/test_phase_c* tests/test_fast_mode* tests/test_dubbing* \
  tests/test_localization_measured_speakers.py tests/test_localization_voice_workflow.py \
  tests/test_localization_exceptions.py tests/test_localization_profile_ui_contract.py \
  tests/test_localization_publish_service.py tests/test_stability_gap_regressions.py \
  tests/test_phase_f* tests/test_translate_batch_resilience.py tests/test_tts_settings.py \
  --tb=short --show-capture=no
```

Frontend: **78 đạt**, gồm kiểm tra thành phần React thật về polling, xác nhận giới tính, lưu một phần và đổi công việc khi yêu cầu đang chờ:

```sh
cd app/frontend
node --test src/localizationPreview.test.js src/localizationReviewResume.test.js \
  src/providerBinding.test.js src/providerBindingIntent.test.js \
  src/studioProviderBinding.test.js src/ttsLocalizationRecovery.test.js \
  src/speakerVoicePanel.test.js src/studioModel.test.js src/studioReadinessModel.test.js
npm run build
```

Bản dựng sản xuất đạt; còn cảnh báo kích thước bundle lớn. Ruff trên tất cả tệp Python thay đổi và `git diff --check` đạt. Các lần đầu chạy lại gặp thiếu pytest/greenlet/torch trong môi trường; đã cài đủ và chạy lại lệnh cuối ở trên. Không bỏ kiểm tra căn chỉnh để che thiếu phụ thuộc.

Một số fixture cũ được cập nhật theo hợp đồng: bản dịch C6 đã Việt hóa thay cho phiên âm C7; kiểm tra riêng giọng trùng dùng giới tính đã xác nhận; Fast không chạy Demucs; checkpoint phải chứa đầy đủ ID và metadata phiên bản mới. Kiểm tra retry/rebind/QC dùng TTS và suy luận giả lập có thời lượng xác định, không tính là video thật.

## Âm thanh thật và giới hạn nghiệm thu

| Mẫu/đường chạy | Kết quả quan sát |
| --- | --- |
| `douyin_derived_sample3.mp4` | Tệp thực là âm thanh AAC 20.015625 giây. Chạy đường căn chỉnh Fast hiện tại với transcript ASR thật có 4 đoạn/34 từ: giữ nguyên toàn bộ dữ liệu từ và mốc thời gian. Diarization yêu cầu nhưng không áp dụng, lý do `missing_huggingface_token`, 0 người phát hiện, 4 đoạn chưa gán. |
| Phân tích âm vực tệp trên | Giải mã thực sang mono 16 kHz và chạy hàm gợi ý mới. Âm vực trung vị 290.9 Hz, 5560 ms cửa sổ có tiếng; kết luận **unknown**, **confirmed=false**. Đây chỉ là chẩn đoán toàn bản ghi, không phải phát hiện người nói thành công hay phân loại giới tính được xác minh. |
| Danh mục Edge trực tiếp | Không truy cập được do lỗi xác minh chứng chỉ SSL tại endpoint Edge. Không tắt TLS; không có âm thanh TTS thật để nghe nghiệm thu. |
| Mẫu khác từ lần kiểm tra trước | `douyin_sample1.mp4` không có audio; `local_sample1.mp4` không nhận được lời nói. Mẫu Bilibili/lớn hơn không tải được nội dung nhị phân qua kết nối kho mã. |

Chưa chứng minh: chất lượng nghe/ngữ điệu/nhịp của video hoàn chỉnh một người, hai người, nhiều người cùng giới; hội thoại chồng lấn; suy luận diarization thật với mô hình/token hợp lệ; Azure/nhà cung cấp trả phí/clone thật; Windows/CUDA và kiểm tra bố cục bằng trình duyệt. Không có khóa/token thích hợp hoặc TTS trực tiếp hoạt động để hoàn tất vòng dịch–lồng tiếng–render và nghe đối chiếu.

Tên Hán–Việt bất kỳ và diễn đạt tự nhiên vẫn phụ thuộc mô hình C6/từ điển được duyệt: không có bộ chuyển mọi tên riêng hay mô hình chứng minh tương đương ý nghĩa trong thay đổi này. Các kiểm tra số/đơn vị/phủ định/dấu câu là hữu hạn; không chứng minh phạm vi phủ định, mọi số viết bằng chữ hoặc ngữ điệu tương đương. Âm vực không chứng minh giới tính (nhất là trẻ em, hát, nhiễu); bất định phải qua xác nhận, không được tự thành READY.

## Tệp thay đổi

- `PROJECT_STATUS.md`
- `app/backend/api/localization.py`
- `app/backend/api/settings.py`
- `app/backend/core/localization.py`
- `app/backend/core/tool_runners.py`
- `app/backend/services/alignment_service.py`
- `app/backend/services/azure_tts_service.py`
- `app/backend/services/dubbing_job_state.py`
- `app/backend/services/dubbing_rewrite_guard.py`
- `app/backend/services/dubbing_speech.py`
- `app/backend/services/edge_tts_service.py`
- `app/backend/services/localization_qc_service.py`
- `app/backend/services/localization_service.py`
- `app/backend/services/localization_stage_service.py`
- `app/backend/services/openai_tts_service.py`
- `app/backend/services/tts_service.py`
- `app/frontend/src/components/tools/SpeakerVoicePanel.jsx`
- `app/frontend/src/speakerVoicePanel.test.js`
- `app/tests/test_dubbing_speech_preparation.py`
- `app/tests/test_localization_measured_speakers.py`
- `app/tests/test_localization_voice_workflow.py`
- `app/tests/test_phase_c_localization_api.py`
- `app/tests/test_phase_c_provider_binding.py`
- `app/tests/test_stability_gap_regressions.py`
- `docs/ARCHITECTURE.md`
- `docs/CONSOLIDATION_PLAN.md`
- `docs/QA_LOCALIZATION_MEASURED_SPEAKERS.md`
- `docs/QA_LOCALIZATION_VI_VOICES_WORKFLOW.md`
