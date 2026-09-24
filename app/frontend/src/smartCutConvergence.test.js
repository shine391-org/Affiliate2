import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const studio = readFileSync(new URL("./pages/Studio.jsx", import.meta.url), "utf8")
const clipStudio = readFileSync(new URL("./pages/ClipStudio.jsx", import.meta.url), "utf8")
const sourceBench = readFileSync(new URL("./components/clip/bench/SourceBench.jsx", import.meta.url), "utf8")

test("Studio Smart Cut mounts the existing Clip Studio engine instead of a second implementation", () => {
  assert.ok(studio.includes('<ClipStudio workflow="smart-cut" />'))
  assert.ok(!studio.includes('(mode === "smart-cut" || mode === "remake")'))
})

test("Smart Cut persists its own derivative identity through the shared extraction endpoint", () => {
  assert.ok(clipStudio.includes('const outputSourceType = smartCut ? "smart_cut" : "clip_extraction"'))
  assert.ok(clipStudio.includes('payload.output_source_type = "smart_cut"'))
  assert.ok(clipStudio.includes('source_type: outputSourceType'))
})

test("Smart Cut is review-first and does not expose direct Auto-cut", () => {
  assert.ok(clipStudio.includes('!smartCut && selectedSourceId'))
  assert.ok(clipStudio.includes('allowAutoCut={!smartCut}'))
  assert.ok(sourceBench.includes('allowAutoCut = true'))
  assert.ok(sourceBench.includes('allowAutoCut && onOpenDialog'))
})

test("Smart Cut uses the Studio header as its source owner", () => {
  assert.ok(clipStudio.includes('display: smartCut ? "none" : "flex"'))
  assert.ok(clipStudio.includes('Select a source in the Studio header'))
})
