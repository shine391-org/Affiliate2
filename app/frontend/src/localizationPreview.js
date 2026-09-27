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
  const centerY = Math.max(0, Math.min(1, maskBox.y + maskBox.height / 2 + offset))
  const percent = (value) => Number((value * 100).toFixed(3))
  return {
    left: `${percent(centerX)}%`,
    top: `${percent(centerY)}%`,
    width: `${percent(maskBox.width)}%`,
    transform: "translate(-50%, -50%)",
  }
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
