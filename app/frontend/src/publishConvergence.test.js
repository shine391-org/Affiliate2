import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const publish = readFileSync(new URL("./pages/Publish.jsx", import.meta.url), "utf8")
const library = readFileSync(new URL("./pages/Library.jsx", import.meta.url), "utf8")

test("Publish is a native GeneratedVideo workspace, not the legacy candidate overlay", () => {
  assert.ok(publish.includes('http.get("/api/videos"'))
  assert.ok(publish.includes('/publish-approval'))
  assert.ok(publish.includes('/upload'))
  assert.ok(!publish.includes('/api/publish/candidates/'))
  assert.ok(!publish.includes('PublishGate'))
})

test("approval and publish remain separate human actions", () => {
  assert.ok(publish.includes("Duyệt để xuất bản"))
  assert.ok(publish.includes("Việc xuất bản vẫn cần bấm nút nền tảng riêng và xác nhận."))
  assert.ok(publish.includes("window.confirm"))
})

test("native Publish reuses existing upload account ownership", () => {
  assert.ok(publish.includes("/api/settings/youtube-auth"))
  assert.ok(publish.includes("/api/settings/tiktok-upload-auth"))
  assert.ok(publish.includes("settings?.youtube_connected"))
  assert.ok(publish.includes("settings?.tiktok_upload_connected"))
})

test("transitional platforms are stated honestly instead of faked", () => {
  assert.ok(publish.includes("Lịch sử xuất bản Facebook/Instagram và overlay cũ"))
  assert.ok(publish.includes("stay transitional"))
})

test("Library hands irreversible publish actions to the canonical Publish owner", () => {
  assert.ok(library.includes('navigate(`/publish?${query.toString()}`)'))
  assert.ok(!library.includes('http.post(`/api/videos/${videoId}/upload`'))
  assert.ok(!library.includes("Confirm upload to"))
})


test("Publish preserves an explicit Library deep-link instead of substituting a recent video", () => {
  assert.ok(publish.includes('http.get(\`/api/videos/\${requested}\`)'))
  assert.ok(publish.includes("Never substitute another asset for an explicit ID"))
  assert.ok(!publish.includes("(!requested || !eligible.some"))
})

test("an already-published destination cannot be accidentally published twice", () => {
  assert.ok(publish.includes("disabled={!connected || busy || published}"))
  assert.ok(publish.includes('{published ? "Đã xuất bản" : "Xuất bản"}'))
})
