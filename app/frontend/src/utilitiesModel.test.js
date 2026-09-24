import test from "node:test"
import assert from "node:assert/strict"

import {
  UTILITY_OWNERSHIP,
  WORKFLOW_OWNED_UTILITY_IDS,
  visibleUtilityIds,
} from "./utilitiesModel.js"

test("localization workflows are not owned by Utilities", () => {
  assert.deepEqual(
    [...WORKFLOW_OWNED_UTILITY_IDS].sort(),
    ["localization-batch", "localization-review", "translate"],
  )
})

test("standalone ViralMint utilities remain available", () => {
  for (const id of ["video-download", "trim", "crop", "compress", "voiceover", "captions", "subtitles"]) {
    assert.notEqual(UTILITY_OWNERSHIP[id], "WORKFLOW_OWNED")
  }
})

test("visible utility filtering hides workflow owners without deleting routes", () => {
  assert.deepEqual(
    visibleUtilityIds(["trim", "translate", "localization-review", "video-download"]),
    ["trim", "video-download"],
  )
})
