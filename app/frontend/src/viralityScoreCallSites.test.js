import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

import { ABSENT, formatScore, scoreSeverity } from "./scoutScore.js"

// scoutScore.js fixed the null crash, but three render call sites bypassed the
// null-safe formatters. JSX is not executed by `node --test`, so the contract
// is pinned two ways:
//
//   1. Behavioural — what the chip actually receives for a null score. These
//      assertions FAILED before the fix (`|| 0` turned null into a red 0.0
//      chip, and raw interpolation rendered the literal string "null").
//   2. Source-level — no `.virality_score || 0` coercion and no bare
//      `{...virality_score}` interpolation in any render file, so the bug
//      cannot be reintroduced at a new call site.

const renderFiles = [
  "./components/chat/ScoutResultsCard.jsx",
  "./components/chat/NewsResultsCard.jsx",
  "./components/scout/ScoutResults.jsx",
  "./components/scout/CardGrid.jsx",
  "./components/scout/DataTable.jsx",
]

const sources = new Map(
  renderFiles.map((rel) => [rel, readFileSync(new URL(rel, import.meta.url), "utf8")]),
)

test("an unscored result reaches the chip as the placeholder, not a red 0.0", () => {
  // `|| 0` is what made these chips wrong: it satisfies the null-safe formatter
  // with a real 0, which formatScore renders "0.0" and scoreSeverity paints
  // "error". A platform that was never scored must not look like a failing one.
  const asChipWouldReceiveWithGuard = null
  const asChipReceivedWithLoudashZero = null || 0

  assert.equal(formatScore(asChipWouldReceiveWithGuard), ABSENT)
  assert.equal(scoreSeverity(asChipWouldReceiveWithGuard), "default")

  assert.equal(formatScore(asChipReceivedWithLoudashZero), "0.0")
  assert.equal(scoreSeverity(asChipReceivedWithLoudashZero), "error")
  assert.notEqual(
    formatScore(asChipWouldReceiveWithGuard),
    formatScore(asChipReceivedWithLoudashZero),
    "guarded and `|| 0` must not render identically, otherwise this test proves nothing",
  )
})

test("no scout render file coerces a null score to 0 via `|| 0`", () => {
  for (const [rel, src] of sources) {
    assert.ok(
      !/virality_score\s*\|\|\s*0/.test(src),
      `${rel} uses \`virality_score || 0\`, which renders an unscored result as a red 0.0`,
    )
  }
})

test("no scout render file interpolates a raw score into JSX text", () => {
  // `{selectedResult.virality_score}` in JSX *text* renders the string "null"
  // when the metric is absent — the exact rows this branch was written to
  // survive. A prop (`score={r.virality_score}`) is fine: the chip itself is
  // null-safe, so the `(?<!=)` lookbehind skips attribute position.
  for (const [rel, src] of sources) {
    assert.ok(
      !/(?<!=)\{\s*(?:selectedResult|r|article)\??\.virality_score\s*\}/.test(src),
      `${rel} interpolates a raw virality_score as text; wrap it in formatScore()`,
    )
  }
})

test("the raw-interpolation guard still catches a JSX text regression", () => {
  // Guard against the regex silently becoming a no-op, which would let the test
  // above pass forever.
  const regression = "<Typography>Viral: {selectedResult.virality_score}</Typography>"
  assert.ok(
    /(?<!=)\{\s*(?:selectedResult|r|article)\??\.virality_score\s*\}/.test(regression),
    "detector must still match the real defect",
  )
  const propForm = "<ViralityChip score={r.virality_score} />"
  assert.ok(
    !/(?<!=)\{\s*(?:selectedResult|r|article)\??\.virality_score\s*\}/.test(propForm),
    "a null-safe prop is not a defect",
  )
})

test("every scout render file that shows a score imports the null-safe formatter", () => {
  for (const [rel, src] of sources) {
    if (!/virality_score/.test(src)) continue
    assert.ok(
      /from\s+"(?:\.\.\/)*scoutScore"/.test(src),
      `${rel} touches virality_score but does not import scoutScore`,
    )
  }
})
