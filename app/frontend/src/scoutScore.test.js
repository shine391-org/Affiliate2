import test from "node:test"
import assert from "node:assert/strict"

import { ABSENT, finiteOrNull, formatCount, formatScore, scoreSeverity } from "./scoutScore.js"

// Regression cover for the app-wide "Đã xảy ra lỗi" crash: real scout results
// carry `virality_score: null` for platforms whose provider never computed it
// (57 of 59 rows in the live DB at the time of the fix). A bare
// `value.toFixed(1)` threw a TypeError inside render, the app ErrorBoundary
// caught it, and the ENTIRE Scout Results page became unusable. These tests
// pin the contract that every formatter survives absent metrics.

test("virality_score null from real scout results never throws", () => {
  // The exact shape the API returns for a douyin row.
  const row = { views: 0, likes: 671, comments: 32, virality_score: null, radar_creator_outlier: null }
  assert.equal(formatScore(row.virality_score), ABSENT)
  assert.equal(formatScore(row.radar_creator_outlier), ABSENT)
  assert.equal(formatCount(row.views), "0")
  assert.equal(formatCount(row.likes), "671")
})

test("formatScore renders numbers and honours the digit count", () => {
  assert.equal(formatScore(42.146), "42.1")
  assert.equal(formatScore(42.146, 0), "42")
  assert.equal(formatScore(0), "0.0")
  assert.equal(formatScore("57.2"), "57.2")
})

test("formatCount compacts thousands and millions", () => {
  assert.equal(formatCount(0), "0")
  assert.equal(formatCount(999), "999")
  assert.equal(formatCount(1000), "1.0K")
  assert.equal(formatCount(22_800), "22.8K")
  assert.equal(formatCount(3_200_000), "3.2M")
})

test("every unusable input degrades to the placeholder instead of NaN", () => {
  for (const bad of [null, undefined, NaN, "", "abc", {}, [], Infinity, -Infinity]) {
    assert.equal(formatScore(bad), ABSENT, `formatScore(${String(bad)})`)
    assert.equal(formatCount(bad), ABSENT, `formatCount(${String(bad)})`)
    assert.equal(finiteOrNull(bad), null, `finiteOrNull(${String(bad)})`)
  }
})

test("Number(null) is 0, so an explicit null guard is mandatory", () => {
  // Documents WHY the guard exists: without `value == null` first, the naive
  // `Number.isFinite(Number(value)) ? Number(value).toFixed(1) : "—"` would
  // silently report a missing metric as 0.0.
  assert.ok(Number.isFinite(Number(null)))
  assert.equal(Number(null), 0)
  assert.equal(finiteOrNull(null), null, "finiteOrNull must reject null before coercion")
})

test("scoreSeverity is neutral for absent scores, not 'error' red", () => {
  // An unscored row must not be painted as a 0/10 failure.
  assert.equal(scoreSeverity(null), "default")
  assert.equal(scoreSeverity(undefined), "default")
  assert.equal(scoreSeverity(NaN), "default")
  assert.equal(scoreSeverity(12), "error")
  assert.equal(scoreSeverity(50), "warning")
  assert.equal(scoreSeverity(88), "success")
})
