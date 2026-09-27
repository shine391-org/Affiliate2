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
