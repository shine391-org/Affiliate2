export const REVIEW_FILTERS = [
  { value: "needs-review", label: "Needs Review", enabled: true },
  { value: "translation", label: "Translation", enabled: true },
  { value: "timing", label: "Timing", enabled: true },
  { value: "audio", label: "Audio", enabled: true },
  { value: "storyboard", label: "Storyboard", enabled: true },
  { value: "visual", label: "Visual", enabled: true },
  { value: "qc", label: "QC", enabled: true },
  { value: "publish", label: "Publish", enabled: false },
]

export const REVIEW_WORKFLOW_CONTRACTS = {
  Localize: {
    phase: "C",
    detail_kind: "localization-expert",
    categories: ["Translation", "Timing", "Audio", "QC"],
  },
  "Smart Cut": {
    phase: "D",
    detail_kind: "smart-cut",
    categories: ["Timing", "Visual", "QC"],
  },
  "Creative Remake": {
    phase: "E",
    detail_kind: "creative-remake",
    categories: ["Storyboard", "Visual", "QC"],
    human_approval: "storyboard",
  },
}

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

export function createReviewItem({
  id,
  workflow,
  source,
  project,
  severity = "warning",
  status = "NEEDS_REVIEW",
  categories = [],
  reasons = [],
  created_at = null,
  updated_at = null,
  action_target = "",
  selection_id = "",
  detail_kind = "",
  raw = null,
}) {
  const contract = REVIEW_WORKFLOW_CONTRACTS[workflow] || {}
  return {
    id,
    workflow,
    source: source || workflow || "Review item",
    project: project || "",
    severity,
    status,
    categories: [...new Set(categories.filter(Boolean))],
    reasons,
    created_at,
    updated_at,
    action_target,
    selection_id: selection_id || id,
    detail_kind: detail_kind || contract.detail_kind || "generic",
    raw,
  }
}

export function adaptWorkflowException(raw, {
  workflow,
  prefix,
  defaultCategories = [],
  detailKind,
} = {}) {
  const idValue = raw.id || raw.job_id || raw.project_id
  const status = raw.status || raw.qc_status || "NEEDS_REVIEW"
  const categories = [
    ...defaultCategories,
    ...(Array.isArray(raw.categories) ? raw.categories : []),
  ]
  return createReviewItem({
    id: `${prefix || String(workflow || "workflow").toLowerCase()}:${idValue}`,
    workflow,
    source: raw.source || raw.source_title || raw.title || idValue,
    project: raw.project || raw.project_id || raw.job_id || idValue,
    severity: status === "FAILED" ? "error" : "warning",
    status,
    categories,
    reasons: Array.isArray(raw.reasons) ? raw.reasons : [],
    created_at: raw.created_at || null,
    updated_at: raw.updated_at || null,
    action_target: raw.action_target || "",
    selection_id: raw.selection_id || idValue,
    detail_kind: detailKind,
    raw,
  })
}

export function adaptLocalizationException(raw) {
  const request = raw.request || {}
  const status = raw.qc_status || ((raw.failed_stages || []).length ? "FAILED" : "NEEDS_REVIEW")
  return {
    ...createReviewItem({
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
      action_target: `/review?job=${encodeURIComponent(raw.job_id)}`,
      selection_id: raw.job_id,
      detail_kind: "localization-expert",
      raw,
    }),
    job_id: raw.job_id,
  }
}

export function reviewSelectionId(item) {
  return item?.selection_id || item?.job_id || item?.id || ""
}

export function reviewItemMatches(item, filter) {
  if (filter === "needs-review") return ["NEEDS_REVIEW", "FAILED"].includes(item.status)
  return item.categories.some((category) => category.toLowerCase() === filter)
}

export function reviewSelectionForItems(items, filter, current) {
  const visible = items.filter((item) => reviewItemMatches(item, filter))
  if (!visible.length) return ""
  if (current && visible.some((item) => reviewSelectionId(item) === current)) return current
  return reviewSelectionId(visible[0])
}
