import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const jobs = readFileSync(new URL("./hooks/useJobs.js", import.meta.url), "utf8")

test("activity refresh notices changed job titles and metadata", () => {
  assert.match(jobs, /x.title !== y.title/)
  assert.match(jobs, /x.input_json !== y.input_json/)
  assert.match(jobs, /x.completed_at !== y.completed_at/)
})

test("active jobs outside the paginated list are reconciled individually", () => {
  assert.match(jobs, /missingActive\.push\(jobId\)/)
  assert.match(jobs, /http.get\(\`\/api\/jobs\/\$\{jobId\}\`\)/)
  assert.match(jobs, /error.response\?\.status === 404/)
})

test("polling stays responsive to active jobs held outside the latest jobs page", () => {
  assert.match(jobs, /Object.keys\(state.activeJobs \|\| \{\}\)\.length > 0/)
  assert.match(jobs, /requestId !== fetchRequestRef\.current/)
})

test("WebSocket active-job restoration is paginated rather than truncated at 40", () => {
  const websocket = readFileSync(new URL("./hooks/useWebSocket.js", import.meta.url), "utf8")
  assert.match(websocket, /status: "running,pending,queued", limit: 100, offset/)
  assert.match(websocket, /while \(offset < total\)/)
  assert.match(websocket, /if \(!mounted\) return/)
})
