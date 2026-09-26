import test from "node:test"
import assert from "node:assert/strict"

import {
  REVIEW_FILTERS,
  REVIEW_WORKFLOW_CONTRACTS,
  adaptLocalizationException,
  adaptWorkflowException,
  formatReviewIssue,
  normalizeReviewFilter,
  reviewItemMatches,
  reviewSelectionForItems,
} from "./reviewModel.js"

test("localization exceptions adapt to the generic review envelope", () => {
  const item = adaptLocalizationException({
    job_id: "job-1",
    updated_at: "2026-09-24T03:00:00Z",
    qc_status: "NEEDS_REVIEW",
    failed_stages: [{ stage: "dubbing", error: "timing overflow" }],
    request: { target_language: "vi", source_title: "Source title" },
  })
  assert.equal(item.id, "localization:job-1")
  assert.equal(item.workflow, "Localize")
  assert.equal(item.source, "Source title")
  assert.equal(item.severity, "warning")
  assert.equal(item.status, "NEEDS_REVIEW")
  assert.equal(item.job_id, "job-1")
  assert.equal(item.action_target, "/review?job=job-1")
  assert.deepEqual(item.categories, ["Timing", "QC"])
})

test("review filters reserve future categories without fake counts", () => {
  assert.deepEqual(
    REVIEW_FILTERS.map(({ value, enabled }) => [value, enabled]),
    [
      ["needs-review", true], ["translation", true], ["timing", true],
      ["audio", true], ["storyboard", false], ["visual", false],
      ["qc", true], ["publish", false],
    ],
  )
})

test("future-only review filters fall back to the live queue", () => {
  assert.equal(normalizeReviewFilter("storyboard"), "needs-review")
  assert.equal(normalizeReviewFilter("visual"), "needs-review")
  assert.equal(normalizeReviewFilter("audio"), "audio")
  assert.equal(normalizeReviewFilter("unknown"), "needs-review")
})

test("Phase D and E review contracts are registered without fabricating live items", () => {
  assert.deepEqual(REVIEW_WORKFLOW_CONTRACTS["Smart Cut"].categories, ["Timing", "Visual", "QC"])
  assert.equal(REVIEW_WORKFLOW_CONTRACTS["Creative Remake"].human_approval, "storyboard")

  const storyboard = adaptWorkflowException({
    id: "remake-1",
    source_title: "Product demo",
    status: "NEEDS_REVIEW",
    categories: ["Storyboard", "Visual"],
    reasons: [{ stage: "storyboard", error: "Human approval required" }],
  }, {
    workflow: "Creative Remake",
    prefix: "remake",
    detailKind: "creative-remake",
  })

  assert.equal(storyboard.workflow, "Creative Remake")
  assert.equal(storyboard.detail_kind, "creative-remake")
  assert.equal(reviewItemMatches(storyboard, "storyboard"), true)
  assert.equal(reviewItemMatches(storyboard, "visual"), true)
  assert.equal(reviewSelectionForItems([storyboard], "storyboard", ""), "remake-1")
})

test("review category matching uses real reason categories", () => {
  const item = { status: "FAILED", categories: ["Audio", "QC"] }
  assert.equal(reviewItemMatches(item, "needs-review"), true)
  assert.equal(reviewItemMatches(item, "audio"), true)
  assert.equal(reviewItemMatches(item, "translation"), false)
})

test("structured QC issues are summarized without dumping raw JSON", () => {
  assert.equal(
    formatReviewIssue({ code: "high_speech_speed", count: 2, issues: [{ chunk_id: 3 }, { chunk_id: 5 }] }),
    "high speech speed · 2 mục bị ảnh hưởng",
  )
  assert.equal(
    formatReviewIssue({ code: "preview_tts_provider", provider: "edge_tts", message: "Human approval required." }),
    "Human approval required.",
  )
})

test("structured QC codes still drive category filters when a message exists", () => {
  const item = adaptLocalizationException({
    job_id: "job-audio",
    qc_status: "NEEDS_REVIEW",
    qc_warnings: [{ code: "preview_tts_provider", message: "Human approval required." }],
  })
  assert.equal(reviewItemMatches(item, "audio"), true)
})

test("review selection stays inside the active filtered queue", () => {
  const items = [
    { job_id: "job-qc", status: "NEEDS_REVIEW", categories: ["QC"] },
    { job_id: "job-audio", status: "NEEDS_REVIEW", categories: ["Audio", "QC"] },
  ]
  assert.equal(reviewSelectionForItems(items, "audio", "job-qc"), "job-audio")
  assert.equal(reviewSelectionForItems(items, "translation", "job-audio"), "")
  assert.equal(reviewSelectionForItems(items, "audio", "job-audio"), "job-audio")
})