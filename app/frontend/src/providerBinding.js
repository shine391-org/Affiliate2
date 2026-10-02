// Provider-binding read model for a saved localization job.
//
// The backend already decides whether a job's stored provider snapshot still
// matches the live Settings (``provider_binding.binding_mismatch``) and which
// stages drifted (``changed_stages``). This module never resolves providers or
// compares credentials: it only shapes the canonical verdict into the labels
// and payloads the Studio/Expert surfaces render.

export const PROVIDER_BINDING_MISMATCH_LABEL = "Provider đã thay đổi từ lúc job được tạo"
export const PROVIDER_BINDING_KEEP_LABEL = "Chạy lại với cấu hình của job"
export const PROVIDER_BINDING_CURRENT_LABEL = "Dùng cấu hình hiện tại"
export const PROVIDER_BINDING_CURRENT_UNAVAILABLE_LABEL =
  "Cấu hình hiện tại không khả dụng — không thể dùng cấu hình hiện tại."

const CHANGED_STAGE_LABELS = {
  translation: "Bản dịch C6",
  rewrite: "Viết lại C7",
  tts: "TTS",
}

const REBIND_STAGES = ["semantic", "translation", "dubbing"]

/** The canonical provider-binding block, verbatim, or null when absent. */
export function providerBinding(detail) {
  return detail?.provider_binding || null
}

/**
 * Only the backend boolean counts. A frontend re-derivation would have to
 * compare routes/voices itself and would drift from the server verdict.
 */
export function providerBindingMismatch(detail) {
  return providerBinding(detail)?.binding_mismatch === true
}

export function changedStages(binding) {
  const stages = binding?.changed_stages
  return Array.isArray(stages) ? stages.filter((stage) => CHANGED_STAGE_LABELS[stage]) : []
}

export function changedStagesLabel(binding) {
  return changedStages(binding).map((stage) => CHANGED_STAGE_LABELS[stage]).join(" · ")
}

/** Earliest stage comes from the canonical backend dependency contract. */
export function providerBindingFromStage(binding) {
  const stage = binding?.from_stage
  return REBIND_STAGES.includes(stage) ? stage : null
}

const routes = (side, key) => (Array.isArray(side?.[key]) ? side[key] : [])

export function bindingTranslationRoutes(side) {
  return routes(side, "translation_routes")
}

export function bindingRewriteRoutes(side) {
  return routes(side, "rewrite_routes")
}

export function bindingTtsLabel(side) {
  const provider = side?.tts?.tts_provider || ""
  const voice = side?.tts?.tts_voice || ""
  return [provider, voice].filter(Boolean).join(" · ")
}

/** "Dùng cấu hình hiện tại" is only safe when the backend resolved a current binding. */
export function currentProvidersAvailable(binding) {
  return Boolean(binding?.current) && !binding?.current_error
}

export function rebindProvidersPayload(source = "settings") {
  return { source }
}

/** Compact, ordered identity labels for every primary and fallback route. */
export function bindingSideSummary(side) {
  if (!side) return "không khả dụng"
  const chain = (items, label) => items.map((route, index) => (
    `${label} ${index === 0 ? "Primary" : `Fallback ${index}`}: ` +
    [route.connection_id, route.model].filter(Boolean).join(" / ")
  ))
  return [
    ...chain(bindingTranslationRoutes(side), "C6"),
    ...chain(bindingRewriteRoutes(side), "C7"),
    bindingTtsLabel(side) ? "TTS " + bindingTtsLabel(side) : null,
  ].filter(Boolean).join(" · ") || "chưa cấu hình"
}
