import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import React from "react"
import TestRenderer, { act } from "react-test-renderer"
import { useJobBoundBindingPrompt } from "./useJobBoundBindingPrompt.js"

// Run the same production hook consumed by both pages, including a retained
// confirmation from A after navigation to B (before and after effect cleanup).
test("provider rerun intent belongs only to the job that opened it", () => {
  let current
  let duringSwitch
  const calls = []
  function Host({ jobId }) {
    const prompt = useJobBoundBindingPrompt(jobId)
    current = {
      ...prompt,
      confirm: () => {
        const action = prompt.consumeBindingPrompt()
        if (action !== null) calls.push([jobId, action])
      },
    }
    if (jobId === "B" && !duringSwitch) duringSwitch = current
    return null
  }
  let tree
  act(() => { tree = TestRenderer.create(React.createElement(Host, { jobId: "A" })) })
  act(() => current.openBindingPrompt("dubbing"))
  assert.deepEqual(current.bindingPrompt, { jobId: "A", action: "dubbing" })
  const stale = current.confirm
  act(() => tree.update(React.createElement(Host, { jobId: "B" })))
  assert.equal(current.bindingPrompt, null)
  act(() => { stale(); duringSwitch.confirm(); current.confirm() })
  assert.deepEqual(calls, [])
  act(() => current.openBindingPrompt("rewrite"))
  assert.deepEqual(current.bindingPrompt, { jobId: "B", action: "rewrite" })
  act(() => current.confirm())
  assert.deepEqual(calls, [["B", "rewrite"]])
  act(() => tree.unmount())
})

// Check the real handlers use the tested guard rather than a locally invented
// confirmation closure, and do not expose the stale prompt on either page.
for (const [file, handlers] of [
  ["./pages/StudioLocalize.jsx", ["keepSnapshotAndRerun", "useCurrentProvidersAndRerun"]],
  ["./components/tools/LocalizationExpertPanel.jsx", ["keepSnapshotRerun", "useCurrentProvidersRerun"]],
]) {
  test(`${file}: both confirmations consume job-bound intent`, () => {
    const source = readFileSync(new URL(file, import.meta.url), "utf8")
    assert.ok(source.includes("useJobBoundBindingPrompt(jobId)"))
    assert.ok(source.includes("open={Boolean(bindingPrompt && bindingPrompt.jobId === jobId)}"))
    for (const name of handlers) {
      const start = source.indexOf(`const ${name} = async () => {`)
      assert.ok(start >= 0, name)
      const body = source.slice(start, source.indexOf("\n  }", start))
      assert.match(body, /consumeBindingPrompt\(\)/)
      assert.match(body, /=== null\) return/)
    }
  })
}
