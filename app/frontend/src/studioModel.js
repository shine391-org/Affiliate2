export const STUDIO_MODES = [
  { value: "localize", label: "Bản địa hóa", path: "/studio/localize", active: true, phase: "C" },
  { value: "smart-cut", label: "Cắt thông minh", path: "/studio/smart-cut", active: true, phase: "D" },
  { value: "remake", label: "Làm lại sáng tạo", path: "/studio/remake", active: false, phase: "E" },
  { value: "manual", label: "Chỉnh thủ công", path: "/studio/manual", active: true, phase: "C" },
]

export const LOCALIZE_STEPS = [
  { value: "source", label: "Nguồn" },
  { value: "transcript", label: "Bản chép lời" },
  { value: "translation", label: "Bản dịch" },
  { value: "voice-timing", label: "Giọng đọc & Nhịp" },
  { value: "render", label: "Kết xuất" },
  { value: "qc", label: "QC" },
]

export function studioModeForPath(pathname = "") {
  const slug = pathname.split("/").filter(Boolean)[1]
  return STUDIO_MODES.some((mode) => mode.value === slug) ? slug : "localize"
}

export function buildStudioModeHref(mode, search = "") {
  const record = STUDIO_MODES.find((item) => item.value === mode) || STUDIO_MODES[0]
  return `${record.path}${search || ""}`
}

export function localizeStepForDetail(detail) {
  if (!detail) return "source"
  if (detail.translation_review_pending) return "translation"
  const stages = detail.manifest?.stages || {}
  if (stages.qc?.status === "complete") return "qc"
  if (stages.final_render?.status === "complete") return "qc"
  if (stages.dubbing?.status === "complete" || stages.audio_mix?.status === "complete") return "render"
  if (stages.translation?.status === "complete") return "voice-timing"
  if (stages.asr?.status === "complete" || stages.semantic?.status === "complete") return "transcript"
  return "source"
}