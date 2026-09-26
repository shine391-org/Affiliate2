import test from "node:test"
import assert from "node:assert/strict"

import { localizeUiMessage } from "./localizeUiMessage.js"

test("localizes common persisted and websocket progress messages", () => {
  assert.equal(localizeUiMessage("Starting downloads..."), "Đang bắt đầu tải xuống...")
  assert.equal(localizeUiMessage("Downloading video 2/5"), "Đang tải video 2/5")
  assert.equal(localizeUiMessage("Generating voiceover (Edge TTS)..."), "Đang tạo giọng đọc (Edge TTS)...")
  assert.equal(localizeUiMessage("Uploading to TikTok..."), "Đang tải lên TikTok...")
  assert.equal(localizeUiMessage("Queued for rerun"), "Đã xếp hàng chạy lại")
})

test("keeps unknown backend messages intact", () => {
  assert.equal(localizeUiMessage("Custom provider error"), "Custom provider error")
  assert.equal(localizeUiMessage(""), "")
  assert.equal(localizeUiMessage(null), null)
})
