const EXACT_UI_MESSAGES = {
  "Starting downloads...": "Đang bắt đầu tải xuống...",
  "Transcribing with Whisper...": "Đang chép lời bằng Whisper...",
  "Transcribing audio...": "Đang chép lời âm thanh...",
  "Correcting transcript...": "Đang hiệu chỉnh bản chép lời...",
  "Extracting insights...": "Đang trích xuất insight...",
  "Re-analysis complete": "Phân tích lại hoàn tất",
  "Loading source data...": "Đang tải dữ liệu nguồn...",
  "Using custom script...": "Đang dùng kịch bản tùy chỉnh...",
  "Generating script...": "Đang tạo kịch bản...",
  "Verifying script quality...": "Đang kiểm tra chất lượng kịch bản...",
  "Extracting word timestamps...": "Đang trích xuất mốc thời gian từng từ...",
  "Generating video (stock footage)...": "Đang tạo video từ footage kho...",
  "Mixing background music...": "Đang trộn nhạc nền...",
  "Adding sound effects...": "Đang thêm hiệu ứng âm thanh...",
  "Merging audio...": "Đang ghép âm thanh...",
  "Applying auto-zoom effects...": "Đang áp dụng hiệu ứng tự động zoom...",
  "Generating metadata...": "Đang tạo metadata...",
  "Generating thumbnail...": "Đang tạo ảnh thu nhỏ...",
  "Video ready!": "Video đã sẵn sàng!",
  "Starting scout...": "Đang bắt đầu quét...",
  "Saving results...": "Đang lưu kết quả...",
  "Preparing upload...": "Đang chuẩn bị tải lên...",
  "Queued for rerun": "Đã xếp hàng chạy lại",
  "Video not found": "Không tìm thấy video",
  "No audio/video file found": "Không tìm thấy tệp âm thanh/video",
  "Source video not found": "Không tìm thấy video nguồn",
  "Generated video not found": "Không tìm thấy video đã tạo",
  "Video file path is missing": "Thiếu đường dẫn tệp video",
  "Video generation failed — check API keys": "Tạo video thất bại — hãy kiểm tra API key",
  "No script provided and no source video to generate from": "Chưa có kịch bản và cũng không có video nguồn để tạo nội dung",
  "No error reported": "Không có thông tin lỗi",
}

export function localizeUiMessage(value) {
  if (typeof value !== "string" || !value) return value
  const exact = EXACT_UI_MESSAGES[value]
  if (exact) return exact

  let match = value.match(/^Downloading video (\d+)\/(\d+)$/)
  if (match) return `Đang tải video ${match[1]}/${match[2]}`

  match = value.match(/^Downloaded (\d+)\/(\d+) videos — all downloads failed$/)
  if (match) return `Đã tải ${match[1]}/${match[2]} video — tất cả lượt tải thất bại`

  match = value.match(/^Downloaded (\d+)\/(\d+) videos$/)
  if (match) return `Đã tải ${match[1]}/${match[2]} video`

  match = value.match(/^All (\d+) videos failed analysis$/)
  if (match) return `Phân tích thất bại với cả ${match[1]} video`

  match = value.match(/^Generating voiceover \((.+)\)\.\.\.$/)
  if (match) return `Đang tạo giọng đọc (${match[1]})...`

  match = value.match(/^Burning (.+) captions\.\.\.$/)
  if (match) return `Đang chèn phụ đề ${match[1]}...`

  match = value.match(/^Refining search for (.+)\.\.\.$/)
  if (match) return `Đang tinh chỉnh tìm kiếm trên ${match[1]}...`

  match = value.match(/^Scouted (.+)$/)
  if (match) return `Đã quét ${match[1]}`

  match = value.match(/^Uploading to (.+)\.\.\.$/)
  if (match) return `Đang tải lên ${match[1]}...`

  return value
}

export default localizeUiMessage
