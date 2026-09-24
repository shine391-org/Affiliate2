export const STUDIO_MODES = [
  { value: "localize", label: "Localize", path: "/studio/localize", active: true, phase: "C" },
  { value: "smart-cut", label: "Smart Cut", path: "/studio/smart-cut", active: false, phase: "D" },
  { value: "remake", label: "Creative Remake", path: "/studio/remake", active: false, phase: "E" },
  { value: "manual", label: "Manual Edit", path: "/studio/manual", active: true, phase: "C" },
]

export const LOCALIZE_STEPS = [
  { value: "source", label: "Source" },
  { value: "transcript", label: "Transcript" },
  { value: "translation", label: "Translation" },
  { value: "voice-timing", label: "Voice & Timing" },
  { value: "render", label: "Render" },
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