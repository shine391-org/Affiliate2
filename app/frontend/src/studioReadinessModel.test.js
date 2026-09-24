import test from "node:test"
import assert from "node:assert/strict"

import {
  PHASE_D_SMART_CUT_STEPS,
  PHASE_E_REMAKE_STEPS,
  STUDIO_ASSET_PROVIDERS,
  STUDIO_READINESS,
  buildStudioSourceContext,
  normalizeTranscriptSegments,
  studioAssetProviderHref,
} from "./studioReadinessModel.js"

test("Phase D and E reuse the shared Studio shell instead of defining top-level apps", () => {
  assert.equal(STUDIO_READINESS["smart-cut"].phase, "D")
  assert.equal(STUDIO_READINESS.remake.phase, "E")
  assert.ok(PHASE_D_SMART_CUT_STEPS.includes("approval"))
  assert.ok(PHASE_E_REMAKE_STEPS.includes("storyboard"))
  assert.ok(PHASE_E_REMAKE_STEPS.includes("approval"))
})

test("downloaded source transcript is normalized onto one millisecond timeline", () => {
  const context = buildStudioSourceContext({
    id: "source-1",
    title: "Demo",
    duration_seconds: 10,
    transcript: "hello world",
    transcript_language: "en",
    transcript_source: "whisper",
    transcript_segments: [
      { start: 0.25, end: 1.5, text: "hello", words: [] },
      { start_ms: 1600, end_ms: 2200, text: "world", words: [] },
    ],
  })
  assert.equal(context.library_key, "dl:source-1")
  assert.equal(context.duration_ms, 10000)
  assert.equal(context.transcript.available, true)
  assert.deepEqual(
    context.transcript.segments.map(({ start_ms, end_ms }) => [start_ms, end_ms]),
    [[250, 1500], [1600, 2200]],
  )
  assert.equal(context.timeline.transcript_end_ms, 2200)
  assert.equal(context.timeline.monotonic, true)
  assert.equal(context.timeline.within_source, true)
})

test("timeline contract flags transcript drift beyond the source", () => {
  const context = buildStudioSourceContext({
    id: "source-2",
    duration_seconds: 5,
    transcript_segments: [{ start: 0, end: 7, text: "late" }],
  })
  assert.equal(context.timeline.within_source, false)
})

test("asset providers remain contextual and preserve source identity", () => {
  assert.deepEqual(STUDIO_ASSET_PROVIDERS.map(({ id }) => id), ["stock", "motion"])
  assert.equal(studioAssetProviderHref("stock", "abc 123"), "/stock?source=abc%20123")
  assert.equal(studioAssetProviderHref("motion", ""), "/motion")
})

test("normalizer rejects unusable segment timing without inventing it", () => {
  assert.deepEqual(normalizeTranscriptSegments([{ text: "no timing" }]), [])
})
