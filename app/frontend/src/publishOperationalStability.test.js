import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const publish = readFileSync(new URL("./pages/Publish.jsx", import.meta.url), "utf8")

test("a transient publish polling failure schedules a retry rather than orphaning the upload", () => {
  assert.match(publish, /nextFailures = failures \+ 1/)
  assert.match(publish, /Math\.min\(30000, 1800 \* 2/)
  assert.match(publish, /setTimeout\(\(\) => tick\(nextFailures\), delay\)/)
  assert.match(publish, /không gửi lại video/)
})

test("Publish ties polling and upload completion to exact GeneratedVideo identity", () => {
  assert.match(publish, /selectedIdRef\.current === videoId/)
  assert.match(publish, /stopPolling\(\)\s+setJob\(null\)\s+setPollError\(""/)
  assert.match(publish, /pollJob\(data\.job_id, publishingVideoId\)/)
  assert.match(publish, /candidate\.input_json/)
})

test("Publish restores an existing active upload after page refresh", () => {
  assert.match(publish, /status: "pending,running", job_type: "upload", limit: 200/)
  assert.match(publish, /pollJob\(matched\.id, videoId\)/)
})

test("Publish ignores late source-list requests when the selected video changes", () => {
  assert.match(publish, /const version = \+\+loadVersionRef\.current/)
  assert.match(publish, /const isCurrent = \(\) => loadVersionRef\.current === version/)
  assert.match(publish, /if \(!isCurrent\(\)\) return/)
  assert.match(publish, /if \(isCurrent\(\)\) setLoading\(false\)/)
})

test("restoring an upload cannot overwrite a newer upload, and reselecting same video keeps its watcher", () => {
  assert.match(publish, /pollVersionRef\.current !== restoreVersion/)
  assert.match(publish, /if \(id === selected\?\.id\) return/)
})
