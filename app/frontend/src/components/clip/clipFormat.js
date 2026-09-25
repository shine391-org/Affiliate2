// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
/**
 * Shared Clip Studio formatting helpers.
 *
 * Extracted from ClipStudio.jsx so the cutting-bench components don't each
 * carry a copy — and so the page and the bench agree on what a timecode looks
 * like, which matters when the same number appears on a range block, in the
 * ranges rail and in the clip inspector.
 */
import { formatClock } from "../../utils/format"

/**
 * Clip Studio timecode — "1:30:00" / "2:51", or "--:--" when unknown.
 *
 * A thin alias over the app-wide `formatClock`: the "--:--" placeholder is
 * specific to this surface, and four call sites read better as
 * `formatTime(clip.clip_start_seconds)`. It is NOT a second implementation —
 * it used to be one, and that copy stopped at minutes, so a 90-minute source
 * video rendered as "90:00".
 */
export function formatTime(seconds) {
  return formatClock(seconds, { fallback: "--:--" })
}

export function viralityColor(score) {
  if (score >= 8) return "success"
  if (score >= 6) return "warning"
  return "default"
}

export function viralityLabel(score) {
  if (score >= 9) return "Rất viral"
  if (score >= 8) return "Mạnh"
  if (score >= 6) return "Tốt"
  if (score >= 4) return "Trung bình"
  return "Thấp"
}

// Map AI-returned hook_type values to short user-facing labels. The closed set
// lives server-side in clip_extractor; "general" is the catch-all and renders
// as null (no chip) so we don't crowd the UI with an uninformative label.
const HOOK_TYPE_LABEL = {
  curiosity_gap:   "Gợi tò mò",
  contrarian:      "Trái chiều",
  emotional_peak:  "Cao trào cảm xúc",
  question:        "Hook câu hỏi",
  number_promise:  "Lời hứa bằng con số",
  story_loop:      "Vòng lặp câu chuyện",
  actionable_tip:  "Mẹo áp dụng ngay",
  shocking_claim:  "Tuyên bố gây sốc",
}

export function hookTypeLabel(t) {
  if (!t || t === "general") return null
  return HOOK_TYPE_LABEL[t] || null
}

/**
 * CSS `aspect-ratio` for a clip's persisted `aspect_ratio` ("16:9" / "9:16" /
 * "1:1" — the vocabulary of backend video_utils.aspect_from_dims). Portrait
 * when the row carries nothing (legacy rows), since that is what extraction
 * produces by default.
 *
 * The clip card used to hardcode 9:16; a landscape cut (extraction only
 * reframes when "vertical" is on) then had its thumbnail centre-cropped to a
 * vertical sliver.
 */
export function clipAspectCss(aspect) {
  const [w, h] = String(aspect || "").split(":").map(Number)
  if (!(w > 0) || !(h > 0)) return "9 / 16"
  return `${w} / ${h}`
}
