export function formatMetric(value) {
  return value == null ? "–" : Number(value).toLocaleString()
}

export function performanceDecision(post) {
  const views = post.latest_metrics?.views
  if (views == null) return "No measured result yet"
  if (post.latest_metrics.stale) return "Snapshot is stale; sync before deciding"
  if (post.state === "INSUFFICIENT_DATA") return "Early observation — use as reference, not a proven winner"
  return post.state === "OUTPERFORMING" ? "Consider making another like this" : "Inspect this asset before deciding"
}
