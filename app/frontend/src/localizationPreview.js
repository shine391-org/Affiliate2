const TARGET_RATIOS = {
  "9:16": 9 / 16,
  "16:9": 16 / 9,
  "1:1": 1,
}

export const MASK_PRESETS = {
  bottom: { x: 0.05, y: 0.72, width: 0.9, height: 0.15 },
  "bottom-full": { x: 0, y: 0.72, width: 1, height: 0.22 },
  top: { x: 0.05, y: 0.08, width: 0.9, height: 0.14 },
}

export function maskPreset(name = "bottom") {
  return { ...(MASK_PRESETS[name] || MASK_PRESETS.bottom) }
}

export function pickPreviewReframeMethod(sourceWidth, sourceHeight, targetAspect) {
  if (!targetAspect || targetAspect === "source") return "source"
  const targetRatio = TARGET_RATIOS[targetAspect]
  if (!targetRatio) return "source"
  const width = Number(sourceWidth)
  const height = Number(sourceHeight)
  if (!(width > 0) || !(height > 0)) return "blur_fill"
  return targetRatio > (width / height) * 1.02 ? "crop" : "blur_fill"
}

export function previewObjectFit(method) {
  return method === "crop" ? "cover" : "contain"
}

export function captionPreviewStyle(maskBox, captionPlacement) {
  if (captionPlacement?.mode !== "mask_region" || !maskBox) return null
  const offset = Number(captionPlacement.vertical_offset || 0)
  const centerX = Math.max(0, Math.min(1, maskBox.x + maskBox.width / 2))
  const safePadding = 0.03
  const minY = Number(maskBox.y) + safePadding
  const maxY = Number(maskBox.y) + Number(maskBox.height) - safePadding
  if (minY > maxY) return null
  const requestedY = Number(maskBox.y) + Number(maskBox.height) / 2 + offset * Number(maskBox.height)
  const centerY = Math.max(minY, Math.min(maxY, requestedY))
  const percent = (value) => Number((value * 100).toFixed(3))
  return {
    left: `${percent(centerX)}%`,
    top: `${percent(centerY)}%`,
    width: `${percent(maskBox.width)}%`,
    transform: "translate(-50%, -50%)",
  }
}

export function hydrateLocalizationJobConfig(detail) {
  const manifest = detail?.manifest
  if (!manifest) return null
  const request = manifest.request || {}
  const savedMask = request.mask_box || null
  const placement = request.caption_placement || {}
  return {
    jobId: manifest.job_id || "",
    profileId: request.profile_id || "",
    target: request.target_language || "vi",
    mode: request.mode === "captions_only" ? "captions_only" : "full_dub",
    reviewTranslation: Boolean(request.review_translation),
    autoRepair: Boolean(request.auto_repair),
    targetAspect: request.target_aspect || manifest.effective_profile?.target_aspect || "9:16",
    enableMask: Boolean(savedMask),
    maskBox: savedMask ? {
      x: Number(savedMask.x),
      y: Number(savedMask.y),
      width: Number(savedMask.width),
      height: Number(savedMask.height),
    } : null,
    maskMode: savedMask?.mode === "solid" ? "solid" : "blur",
    captionPlacementMode: placement.mode === "mask_region" ? "mask_region" : "default",
    captionVerticalOffset: Number(placement.vertical_offset || 0),
  }
}

export function resolveProfileTargetAspect({
  currentAspect,
  selectedProfile,
  aspectOverridden,
  jobConfigHydrated,
}) {
  if (jobConfigHydrated || aspectOverridden || !selectedProfile?.target_aspect) {
    return currentAspect
  }
  return selectedProfile.target_aspect
}

const STAGE_ERROR_LABELS = {
  separation: "Tách nguồn",
  asr: "Bản chép lời (ASR)",
  semantic: "Phân đoạn ngữ nghĩa",
  translation: "Bản dịch",
  dubbing: "Lồng tiếng",
  audio_mix: "Trộn âm thanh",
  final_render: "Kết xuất",
  qc: "QC",
}

const cleanText = (value) => {
  const text = typeof value === "string" ? value.trim() : ""
  return text && text !== "null" && text !== "undefined" ? text : ""
}

/**
 * Surface a FAILED localization job's real reason.
 *
 * A job that died mid-pipeline (e.g. "Library cublas64_12.dll ...") used to
 * render as a healthy-looking page: every stage chip read "đang chờ" and the
 * only error surface was `detailError`, which is reserved for a failed HTTP
 * fetch of the detail itself. The canonical backend fields win; a failed
 * manifest stage is only a fallback when the job row carries no message.
 */
export function localizationJobFailure(detail) {
  if (detail?.job_status !== "failed") return null
  const stages = detail?.manifest?.stages || {}
  const failedStage = Object.keys(stages).find((name) => stages[name]?.status === "failed")
  const step = cleanText(detail.job_current_step) || (failedStage ? STAGE_ERROR_LABELS[failedStage] || failedStage : "")
  const message = cleanText(detail.job_error_message)
    || cleanText(failedStage ? stages[failedStage]?.error : "")
    || "Tác vụ bản địa hóa thất bại mà không có thông báo lỗi."
  return { step, message, stage: failedStage || null }
}

export function canRerenderLocalization(detail) {
  const manifest = detail?.manifest
  if (!manifest || detail?.translation_review_pending) return false
  const request = manifest.request || {}
  const stages = manifest.stages || {}
  if (stages.translation?.status !== "complete") return false
  if (stages.dubbing?.status !== "complete") return false
  if (stages.audio_mix?.status !== "complete") return false
  return request.mode === "full_dub" || request.mode === "captions_only"
}

export function buildLocalizationRenderPayload({
  enableMask,
  maskBox,
  maskMode,
  captionPlacementMode,
  captionVerticalOffset,
  targetAspect,
}) {
  const normalizedMask = enableMask
    ? { ...maskBox, mode: maskMode, normalized: true }
    : null
  return {
    target_aspect: targetAspect,
    mask_box: normalizedMask,
    caption_placement: {
      mode: enableMask && captionPlacementMode === "mask_region" ? "mask_region" : "default",
      vertical_offset: Number(captionVerticalOffset || 0),
    },
  }
}


export function resolveAiStageSelection(metadata = {}, effective = {}, stage = "translation") {
  const rewrite = stage === "rewrite"
  const aiKey = rewrite ? "rewrite_ai" : "translation_ai"
  const providerKey = rewrite ? "rewrite_provider" : "translation_provider"
  const modelKey = rewrite ? "rewrite_model" : "translation_model"
  const routesKey = rewrite ? "rewrite_routes" : "translation_routes"
  const audit = metadata?.[aiKey] || {}
  const routes = audit.route_chain || metadata?.[routesKey] || effective?.[routesKey] || []
  const connectionId = audit.selected_connection_id || metadata?.[providerKey] || effective?.[providerKey] || null
  const selectedRoute = Array.isArray(routes)
    ? routes.find((route) => route?.connection_id === connectionId)
    : null
  const failureChain = Array.isArray(audit.failure_chain) ? audit.failure_chain : []
  return {
    connectionId,
    model: selectedRoute?.model || metadata?.[modelKey] || effective?.[modelKey] || null,
    failureChain,
    failureCount: failureChain.length,
  }
}
