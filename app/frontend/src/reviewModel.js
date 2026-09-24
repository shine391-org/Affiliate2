export const REVIEW_FILTERS = [
  { value: "needs-review", label: "Needs Review", enabled: true },
  { value: "translation", label: "Translation", enabled: true },
  { value: "timing", label: "Timing", enabled: true },
  { value: "audio", label: "Audio", enabled: true },
  { value: "qc", label: "QC", enabled: true },
  { value: "visual", label: "Visual", enabled: false },
  { value: "publish", label: "Publish", enabled: false },
]

export function formatReviewIssue(issue) {
  if (typeof issue === "string") return issue
  if (!issue || typeof issue !== "object") return "Review required"
  if (issue.message) return issue.message
  const label = String(issue.code || issue.reason || "Review required").replaceAll("_", " ")
  const count = Number(issue.count)
  return Number.isFinite(count) && count > 0 ? `${label} · ${count} affected items` : label
}

function categoriesFor(raw) {
  const text = [
    raw.qc_status,
    ...(raw.failed_stages || []).flatMap((row) => [row.stage, row.error]),
    ...(raw.qc_failures || []).map((issue) => issue.code || issue),
    ...(raw.qc_warnings || []).map((issue) => issue.code || issue),
  ].filter(Boolean).join(" ").toLowerCase()
  const categories = []
  if (/translat|semantic|language/.test(text)) categories.push("Translation")
  if (/timing|dubb|speed|overflow|duration/.test(text)) categories.push("Timing")
  if (/audio|mix|voice|tts|clipp|preview_tts_provider/.test(text)) categories.push("Audio")
  categories.push("QC")
  return [...new Set(categories)]
}

export function adaptLocalizationException(raw) {
  const request = raw.request || {}
  const status = raw.qc_status || ((raw.failed_stages || []).length ? "FAILED" : "NEEDS_REVIEW")
  return {
    id: `localization:${raw.job_id}`,
    workflow: "Localize",
    source: request.source_title || request.filename || request.downloaded_video_id || request.target_language || "Localization job",
    project: raw.job_id,
    severity: status === "FAILED" ? "error" : "warning",
    status,
    categories: categoriesFor(raw),
    reasons: [
      ...(raw.failed_stages || []),
      ...(raw.qc_failures || []).map((error) => ({ stage: "qc", error: formatReviewIssue(error) })),
      ...(raw.qc_warnings || []).map((error) => ({ stage: "qc", error: formatReviewIssue(error) })),
    ],
    created_at: raw.created_at || null,
    updated_at: raw.updated_at || null,
    job_id: raw.job_id,
    action_target: `/review?job=${encodeURIComponent(raw.job_id)}`,
    raw,
  }
}

export function reviewItemMatches(item, filter) {
  if (filter === "needs-review") return ["NEEDS_REVIEW", "FAILED"].includes(item.status)
  return item.categories.some((category) => category.toLowerCase() === filter)
}

export function reviewSelectionForItems(items, filter, current) {
  const visible = items.filter((item) => reviewItemMatches(item, filter))
  if (!visible.length) return ""
  if (current && visible.some((item) => item.job_id === current)) return current
  return visible[0].job_id
}