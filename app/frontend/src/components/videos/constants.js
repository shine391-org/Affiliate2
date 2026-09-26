/** Shared constants used across Videos page components */

export const STATUS_COLOR = {
  draft: "default", ready: "success", uploading: "warning", uploaded: "info", failed: "error",
}

export const JOB_TYPE_LABEL = {
  scout: "Quét",
  download: "Tải xuống",
  generate: "Tạo",
  upload: "Tải lên",
  analyze: "Phân tích",
  "tool:voiceover": "Lồng tiếng",
  "tool:captions": "Phụ đề",
  "tool:reframe": "Đổi khung",
  "tool:trim": "Cắt đoạn",
  "tool:speed": "Tốc độ",
  "tool:transform": "Biến đổi",
  "tool:merge_clips": "Ghép clip",
  "tool:watermark": "Watermark",
  "tool:auto_zoom": "Tự động zoom",
  "tool:remove_silence": "Cắt khoảng lặng",
  "tool:compress": "Nén video",
  "tool:crop": "Cắt khung",
  "tool:translate": "Dịch",
  "tool:music_visualizer": "Trực quan hóa nhạc",
  "tool:audio_enhance": "Cải thiện âm thanh",
  "tool:gif": "Tạo GIF",
  "tool:subtitle_export": "Xuất phụ đề",
  "tool:auto_chapters": "Tạo chương",
  "tool:metadata": "Tạo metadata",
  "tool:hook_analysis": "Phân tích hook",
}

export function jobTypeLabel(jobType) {
  const type = String(jobType || "")
  if (JOB_TYPE_LABEL[type]) return JOB_TYPE_LABEL[type]
  if (type.startsWith("tool:")) return "Công cụ"
  return type || "Tác vụ"
}
export const JOB_STATUS_COLOR = { running: "info", pending: "default", success: "success", failed: "error", cancelled: "warning" }

export const WHISPER_QUALITIES = [
  { value: "fast", label: "Nhanh (base)", desc: "~30 giây cho video 5 phút" },
  { value: "balanced", label: "Cân bằng (small)", desc: "~90 giây cho video 5 phút" },
  { value: "accurate", label: "Chính xác (medium)", desc: "~3 phút cho video 5 phút" },
  { value: "best", label: "Tốt nhất (large-v3)", desc: "~8 phút cho video 5 phút" },
]
