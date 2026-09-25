/** Shared constants used across Videos page components */

export const STATUS_COLOR = {
  draft: "default", ready: "success", uploading: "warning", uploaded: "info", failed: "error",
}

export const JOB_TYPE_LABEL = { scout: "Quét", download: "Tải xuống", generate: "Tạo", upload: "Tải lên", analyze: "Phân tích" }
export const JOB_STATUS_COLOR = { running: "info", pending: "default", success: "success", failed: "error", cancelled: "warning" }

export const WHISPER_QUALITIES = [
  { value: "fast", label: "Nhanh (base)", desc: "~30 giây cho video 5 phút" },
  { value: "balanced", label: "Cân bằng (small)", desc: "~90 giây cho video 5 phút" },
  { value: "accurate", label: "Chính xác (medium)", desc: "~3 phút cho video 5 phút" },
  { value: "best", label: "Tốt nhất (large-v3)", desc: "~8 phút cho video 5 phút" },
]
