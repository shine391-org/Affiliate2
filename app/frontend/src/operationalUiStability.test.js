import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const read = (file) => readFileSync(new URL(file, import.meta.url), "utf8")
const localize = read("./pages/StudioLocalize.jsx")
const studio = read("./pages/Studio.jsx")
const channels = read("./pages/Channels.jsx")
const batch = read("./pages/LocalizationBatch.jsx")
const source = read("./hooks/useSourceVideo.js")

test("localization detail rejects late responses from another job and stops polling terminal jobs", () => {
  assert.match(localize, /currentJobRef\.current !== requestedJob/)
  assert.match(localize, /requestId !== detailRequestRef\.current/)
  assert.match(localize, /detail && !\["pending", "running"\]\.includes\(detail\.job_status\)/)
  assert.match(localize, /timer = setTimeout\(tick, 3000\)/)
  assert.doesNotMatch(localize, /setInterval\(\(\) => \{ if \(alive\) loadDetail\(\) \}, 3000\)/)
})

test("Channels ignores superseded list and video-page responses", () => {
  assert.match(channels, /requestId !== videoRequestRef\.current/)
  assert.match(channels, /requestId !== channelRequestRef\.current/)
  assert.match(channels, /return \(\) => \{ videoRequestRef\.current \+= 1 \}/)
})

test("Studio surfaces failed source loading with a retry action", () => {
  assert.match(studio, /setSourcesError\(error\.response/)
  assert.match(studio, /onClick=\{loadSources\}>Thử lại/)
})

test("batch selection explicitly warns when files exceed the cap", () => {
  assert.match(batch, /chosen\.length > 20/)
  assert.match(batch, /Đã bỏ qua/)
})

test("legacy source hook clears removed source and ignores late requests", () => {
  assert.match(source, /if \(!sourceId\) \{ setSource\(null\)/)
  assert.match(source, /if \(!alive\) return/)
})
