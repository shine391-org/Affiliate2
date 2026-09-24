import test from "node:test"
import assert from "node:assert/strict"

import { candidateStatus, downloadedByScoutResult, thumbnailRequestKey } from "./discoverModel.js"

test("candidate status reflects canonical persisted state", () => {
  assert.equal(candidateStatus({ is_analyzed: true, is_downloaded: true }), "Analyzed")
  assert.equal(candidateStatus({ is_downloaded: true }), "Downloaded")
  assert.equal(candidateStatus({}), "Discovered")
})

test("downloaded media is indexed by its scout lineage", () => {
  const index = downloadedByScoutResult([
    { id: "local-1", scout_result_id: "candidate-1", file_exists: true },
    { id: "gone", scout_result_id: "candidate-2", file_exists: false },
    { id: "imported", scout_result_id: null, file_exists: true },
  ])

  assert.equal(index.get("candidate-1"), "local-1")
  assert.equal(index.has("candidate-2"), false)
  assert.equal(index.has(null), false)
})

test("thumbnail failure identity follows the thumbnail URL", () => {
  const first = { id: "candidate-1", platform: "bilibili", thumbnail_url: "https://i0.hdslb.com/old.jpg" }
  const refreshed = { ...first, thumbnail_url: "https://i0.hdslb.com/new.jpg" }

  assert.notEqual(thumbnailRequestKey(first), thumbnailRequestKey(refreshed))
  assert.equal(
    thumbnailRequestKey(first),
    "bilibili::candidate-1::https://i0.hdslb.com/old.jpg",
  )
})
