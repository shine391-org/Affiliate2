import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const publish = readFileSync(new URL("./pages/Publish.jsx", import.meta.url), "utf8")

test("Publish is a native GeneratedVideo workspace, not the legacy candidate overlay", () => {
  assert.ok(publish.includes('http.get("/api/videos"'))
  assert.ok(publish.includes('/publish-approval'))
  assert.ok(publish.includes('/upload'))
  assert.ok(!publish.includes('/api/publish/candidates/'))
  assert.ok(!publish.includes('PublishGate'))
})

test("approval and publish remain separate human actions", () => {
  assert.ok(publish.includes("Approve for publishing"))
  assert.ok(publish.includes("Publishing still requires a separate platform button and confirmation."))
  assert.ok(publish.includes("window.confirm"))
})

test("native Publish reuses existing upload account ownership", () => {
  assert.ok(publish.includes("/api/settings/youtube-auth"))
  assert.ok(publish.includes("/api/settings/tiktok-upload-auth"))
  assert.ok(publish.includes("settings?.youtube_connected"))
  assert.ok(publish.includes("settings?.tiktok_upload_connected"))
})

test("transitional platforms are stated honestly instead of faked", () => {
  assert.ok(publish.includes("Facebook/Instagram and legacy overlay publication history"))
  assert.ok(publish.includes("stay transitional"))
})
