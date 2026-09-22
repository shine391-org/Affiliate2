/** Shared constants used across Videos page components */

export const STATUS_COLOR = {
  draft: "default", ready: "success", uploading: "warning", uploaded: "info", failed: "error",
}

export const JOB_TYPE_LABEL = { scout: "Scout", download: "Download", generate: "Generate", upload: "Upload", analyze: "Analyze" }
export const JOB_STATUS_COLOR = { running: "info", pending: "default", success: "success", failed: "error", cancelled: "warning" }

export const WHISPER_QUALITIES = [
  { value: "fast", label: "Fast (base)", desc: "~30s per 5min video" },
  { value: "balanced", label: "Balanced (small)", desc: "~90s per 5min video" },
  { value: "accurate", label: "Accurate (medium)", desc: "~3min per 5min video" },
  { value: "best", label: "Best (large-v3)", desc: "~8min per 5min video" },
]
