import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { formatMetric, performanceDecision } from "./plugins/performance/performanceModel.js"

const page = readFileSync(new URL("./plugins/performance/Performance.jsx", import.meta.url), "utf8")

test("unknown is not a fabricated zero; only observed views suggest a reference", () => {
  assert.equal(formatMetric(null), "–")
  assert.equal(formatMetric(0), "0")
  assert.equal(performanceDecision({ latest_metrics: null }), "Chưa có kết quả đo")
  assert.match(performanceDecision({ latest_metrics: { views: 300 }, state: "INSUFFICIENT_DATA" }), /Quan sát ban đầu/i)
})

test("canonical performance page reads native owner and links exact GeneratedVideo", () => {
  assert.ok(page.includes('/api/videos/performance/overview'))
  assert.ok(page.includes('`/api/videos/${post.generated_video_id}/performance/${sync ? "sync" : "metrics"}`'))
  assert.ok(page.includes('/publish?video=${encodeURIComponent(post.generated_video_id)}'))
  assert.ok(!page.includes('/api/performance/publications/'))
})
