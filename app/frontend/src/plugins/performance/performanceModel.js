export function formatMetric(value) {
  return value == null ? "–" : Number(value).toLocaleString()
}

export function performanceDecision(post) {
  const views = post.latest_metrics?.views
  if (views == null) return "Chưa có kết quả đo"
  if (post.latest_metrics.stale) return "Snapshot đã cũ; hãy đồng bộ trước khi quyết định"
  if (post.state === "INSUFFICIENT_DATA") return "Quan sát ban đầu — chỉ dùng tham khảo, chưa phải nội dung chiến thắng"
  return post.state === "OUTPERFORMING" ? "Có thể cân nhắc làm thêm nội dung tương tự" : "Hãy xem lại nội dung này trước khi quyết định"
}
