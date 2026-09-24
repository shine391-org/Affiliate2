import test from "node:test"
import assert from "node:assert/strict"

import {
  LOCALIZE_STEPS,
  STUDIO_MODES,
  buildStudioModeHref,
  localizeStepForDetail,
  studioModeForPath,
} from "./studioModel.js"

test("Studio exposes Smart Cut as active while Creative Remake stays reserved", () => {
  assert.deepEqual(
    STUDIO_MODES.map(({ value, active, phase }) => [value, active, phase]),
    [
      ["localize", true, "C"],
      ["smart-cut", true, "D"],
      ["remake", false, "E"],
      ["manual", true, "C"],
    ],
  )
})

test("Studio mode routing keeps canonical defaults", () => {
  assert.equal(studioModeForPath("/studio"), "localize")
  assert.equal(studioModeForPath("/studio/localize"), "localize")
  assert.equal(studioModeForPath("/studio/manual"), "manual")
  assert.equal(studioModeForPath("/studio/smart-cut"), "smart-cut")
  assert.equal(studioModeForPath("/studio/remake"), "remake")
})

test("switching Studio modes preserves source and job context", () => {
  assert.equal(
    buildStudioModeHref("manual", "?source=downloaded-1&job=job-9&step=translation"),
    "/studio/manual?source=downloaded-1&job=job-9&step=translation",
  )
})

test("Localize is one coherent six-step workflow", () => {
  assert.deepEqual(
    LOCALIZE_STEPS.map(({ value }) => value),
    ["source", "transcript", "translation", "voice-timing", "render", "qc"],
  )
})

test("translation review pending keeps the workflow before C7", () => {
  assert.equal(localizeStepForDetail({ translation_review_pending: true }), "translation")
  assert.equal(localizeStepForDetail({
    translation_review_pending: false,
    manifest: { stages: { dubbing: { status: "complete" }, final_render: { status: "complete" }, qc: { status: "complete" } } },
  }), "qc")
})