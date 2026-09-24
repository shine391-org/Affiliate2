import test from "node:test"
import assert from "node:assert/strict"

import { REVIEW_SOURCES, loadReviewItems } from "./reviewSources.js"

test("Review has one live source and reserved Phase D E extension points", () => {
  assert.deepEqual(
    REVIEW_SOURCES.map(({ id, enabled, phase }) => [id, enabled, phase || null]),
    [
      ["localize", true, null],
      ["smart-cut", false, "D"],
      ["creative-remake", false, "E"],
    ],
  )
})

test("Review loader only calls enabled real sources", async () => {
  const calls = []
  const http = {
    async get(endpoint) {
      calls.push(endpoint)
      return {
        data: {
          items: [{
            job_id: "job-1",
            qc_status: "NEEDS_REVIEW",
            request: { source_title: "Demo" },
          }],
        },
      }
    },
  }
  const items = await loadReviewItems(http)
  assert.deepEqual(calls, ["/api/localization/exceptions?limit=100"])
  assert.equal(items.length, 1)
  assert.equal(items[0].workflow, "Localize")
})
