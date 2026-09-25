import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { activityFromJob } from "./components/librarynext/assetModel.js"

const studio = readFileSync(new URL("./pages/Studio.jsx", import.meta.url), "utf8")
const clipStudio = readFileSync(new URL("./pages/ClipStudio.jsx", import.meta.url), "utf8")
const sourceBench = readFileSync(new URL("./components/clip/bench/SourceBench.jsx", import.meta.url), "utf8")

test("Studio Smart Cut mounts the existing Clip Studio engine instead of a second implementation", () => {
  assert.ok(studio.includes('<ClipStudio key="smart-cut" workflow="smart-cut" />'))
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
  assert.ok(clipStudio.includes('storageNamespace={smartCut ? "smart-cut" : "clipper"}'))
  assert.ok(sourceBench.includes('allowAutoCut = true'))
  assert.ok(sourceBench.includes('allowAutoCut && onOpenDialog'))
  assert.ok(sourceBench.includes('storageKey(storageNamespace'))
})

test("Smart Cut uses the Studio header as its source owner", () => {
  assert.ok(clipStudio.includes('display: smartCut ? "none" : "flex"'))
  assert.ok(clipStudio.includes('Studio header is the one Smart Cut source owner'))
  assert.ok(clipStudio.includes('Select a source in the Studio header'))
})

test("Smart Cut render jobs appear under Smart Cut ownership and open their first Library output", () => {
  const item = activityFromJob({
    id: "job-smart-1",
    job_type: "generate",
    status: "success",
    input_json: JSON.stringify({
      type: "smart_cut",
      mode: "manual",
      max_clips: 2,
    }),
    output_json: JSON.stringify({
      clip_ids: ["cut-1", "cut-2"],
      source_type: "smart_cut",
    }),
  })
  assert.equal(item.where, "Cắt thông minh")
  assert.equal(item.producer, "Cắt thông minh")
  assert.equal(item.origin, "edited")
  assert.equal(item.libraryBound, true)
  assert.equal(item.result_key, "gv:cut-1")
})
