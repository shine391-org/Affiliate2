import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const studio = readFileSync(new URL("./pages/Studio.jsx", import.meta.url), "utf8")
const review = readFileSync(new URL("./pages/LocalizationReview.jsx", import.meta.url), "utf8")

test("reserved Creative Remake does not link to an empty live review queue", () => {
  assert.ok(studio.includes("Storyboard review · Phase E"))
  assert.ok(studio.includes("queue stays disabled until a real Creative Remake engine registers live exceptions"))
  assert.ok(!studio.includes('navigate("/review?filter=storyboard")'))
})

test("Review keeps one stable poller and a bounded mobile master-detail layout", () => {
  assert.ok(review.includes("const loadedOnce = useRef(false)"))
  assert.ok(review.includes('setInterval(load, 5000)'))
  assert.ok(review.includes('gridTemplateRows: { xs: "minmax(180px, 42%) minmax(0, 1fr)"'))
  assert.ok(review.includes("if (loading || loadError) return"))
  assert.ok(review.includes("Could not refresh the review queue."))
})
