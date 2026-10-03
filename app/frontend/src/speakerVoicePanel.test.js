import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import { runInNewContext } from "node:vm"
import { transformSync } from "esbuild"
import React from "react"
import TestRenderer, { act } from "react-test-renderer"

// Execute the real JSX component with host controls instead of MUI's browser
// DOM, while retaining React effects and the production save/rerun handlers.
function harness({ failSave = false } = {}) {
  const calls = [], notices = []
  const module = { exports: {} }
  const require = createRequire(import.meta.url)
  const source = readFileSync(new URL("./components/tools/SpeakerVoicePanel.jsx", import.meta.url), "utf8")
  const code = transformSync(source, { loader: "jsx", jsx: "automatic", format: "cjs" }).code
  runInNewContext(code, {
    module, exports: module.exports,
    require(name) {
      if (name === "@mui/material") return Object.fromEntries(
        ["Alert", "Autocomplete", "Button", "Checkbox", "FormControlLabel", "Stack", "TextField", "Typography"].map((key) => [key, key]),
      )
      if (name === "../../api/http") return { post: async (url, body) => {
        calls.push([url, JSON.parse(JSON.stringify(body))])
        if (failSave) throw { response: { data: { detail: "Bản dịch đã thay đổi" } } }
      } }
      if (name === "../../store/appStore") return (select) => select({ showSnackbar: (...args) => notices.push(args) })
      return require(name)
    },
  })
  return { Component: module.exports.default, calls, notices }
}

function detail(extra = {}) {
  return {
    translation_review_revision: "current-translation",
    translation_review_pending: false,
    speaker_state: {
      detected_speakers: ["SPEAKER_00", "SPEAKER_01"],
      speaker_voices: { SPEAKER_00: "voice-a", SPEAKER_01: "voice-b" },
      available_voices: ["voice-a", "voice-b", "voice-c"],
      shared_voice: false,
      diarization: { speaker_count: 2, diarization_applied: true },
    },
    ...extra,
  }
}

test("speaker edit survives polling and saves only the current job then reruns its snapshot", async () => {
  const { Component, calls, notices } = harness()
  let tree, saved = 0
  const props = { jobId: "job-a", detail: detail(), onSaved: () => { saved++ } }
  act(() => { tree = TestRenderer.create(React.createElement(Component, props)) })
  assert.equal(tree.root.findAllByType("Autocomplete").length, 2)
  act(() => tree.root.findAllByType("Autocomplete")[1].props.onInputChange(null, "voice-c"))
  act(() => tree.update(React.createElement(Component, { ...props, detail: detail() })))
  assert.equal(tree.root.findAllByType("Autocomplete")[1].props.inputValue, "voice-c")
  await act(async () => tree.root.findByType("Button").props.onClick())
  assert.deepEqual(calls, [
    ["/api/localization/jobs/job-a/override", { revision: "current-translation", profile_updates: {
      speaker_voice_mode: "distinct", speaker_voices: { SPEAKER_00: "voice-a", SPEAKER_01: "voice-c" },
    } }],
    ["/api/localization/jobs/job-a/rerun", { from_stage: "dubbing" }],
  ])
  assert.equal(saved, 1)
  assert.equal(notices[0][1], "success")
  act(() => tree.unmount())
})

test("explicit shared voice is saved during translation review without starting dubbing", async () => {
  const { Component, calls } = harness()
  let tree
  act(() => { tree = TestRenderer.create(React.createElement(Component, { jobId: "paused", detail: detail({ translation_review_pending: true }) })) })
  act(() => tree.root.findByType("FormControlLabel").props.control.props.onChange({ target: { checked: true } }))
  assert.ok(tree.root.findAllByType("Autocomplete").every((row) => row.props.disabled))
  await act(async () => tree.root.findByType("Button").props.onClick())
  assert.equal(calls.length, 1)
  assert.equal(calls[0][1].profile_updates.speaker_voice_mode, "shared")
  act(() => tree.unmount())
})

test("job navigation resets local speaker selections and active jobs cannot save", () => {
  const { Component } = harness()
  let tree
  act(() => { tree = TestRenderer.create(React.createElement(Component, { jobId: "one", detail: detail() })) })
  act(() => tree.root.findAllByType("Autocomplete")[0].props.onInputChange(null, "voice-c"))
  act(() => tree.update(React.createElement(Component, { jobId: "two", detail: detail(), disabled: true })))
  assert.equal(tree.root.findAllByType("Autocomplete")[0].props.inputValue, "voice-a")
  assert.ok(tree.root.findByType("Button").props.disabled)
  assert.ok(tree.root.findAllByType("Autocomplete").every((row) => row.props.disabled))
  act(() => tree.unmount())
})

test("stale translation rejection is visible and never starts a rerun", async () => {
  const { Component, calls, notices } = harness({ failSave: true })
  let tree
  act(() => { tree = TestRenderer.create(React.createElement(Component, { jobId: "stale", detail: detail() })) })
  await act(async () => tree.root.findByType("Button").props.onClick())
  assert.equal(calls.length, 1)
  assert.deepEqual(notices[0], ["Bản dịch đã thay đổi", "error"])
  assert.equal(tree.root.findByType("Button").props.disabled, false)
  act(() => tree.unmount())
})

test("diarization failure and overlap remain visible in Studio speaker controls", () => {
  const { Component } = harness()
  const state = detail().speaker_state
  state.diarization = { speaker_count: 2, diarization_reason: "inference_failed", overlap_intervals: [{ start_ms: 0, end_ms: 500 }] }
  let tree
  act(() => { tree = TestRenderer.create(React.createElement(Component, { jobId: "review", detail: detail({ speaker_state: state }) })) })
  assert.equal(tree.root.findAllByType("Alert").length, 2)
  act(() => tree.unmount())
})
