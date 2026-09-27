// Pure null-safe formatters for Scout metrics.
//
// `virality_score`, `views_per_hour`, `outlier_score` and `radar_creator_outlier`
// are legitimately NULL for stored results: the metric is only computed for
// platforms whose provider returns engagement data. A raw `value.toFixed(1)`
// on those nulls threw a TypeError that the app-level ErrorBoundary turned into
// a full-page "Đã xảy ra lỗi" — the whole Scout Results surface became unusable.
//
// Every helper here must return a display string for ANY input, including
// null/undefined/NaN/non-numeric.

export const ABSENT = "—"

/** Normalize to a finite number, or null when the value is unusable. */
export function finiteOrNull(value) {
  if (value == null) return null
  // `Number("")` and `Number([])` are both 0, so an empty string / empty array
  // would masquerade as a real 0.0 score. Treat blank input as absent.
  if (typeof value === "string" && value.trim() === "") return null
  if (Array.isArray(value) && value.length === 0) return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

/** Fixed-decimal label, or the em-dash placeholder when absent. */
export function formatScore(value, digits = 1) {
  const n = finiteOrNull(value)
  if (n == null) return ABSENT
  return n.toFixed(digits)
}

/** Compact human count (1.2K / 3.4M), or the em-dash placeholder when absent. */
export function formatCount(value) {
  const n = finiteOrNull(value)
  if (n == null) return ABSENT
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

/** MUI chip severity thresholds shared by every virality/radar chip. */
export function scoreSeverity(value, { warn = 40, good = 70 } = {}) {
  const n = finiteOrNull(value)
  if (n == null) return "default"
  if (n >= good) return "success"
  if (n >= warn) return "warning"
  return "error"
}
