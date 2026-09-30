import assert from "node:assert/strict"
import test from "node:test"

import {
  canonicalChannelVideoUrl,
  channelRequestConfig,
  reconcileSelectedChannel,
  channelsWithoutChannel,
  selectedChannelAfterDisconnect,
} from "./channelVideoIdentity.js"


test("Douyin download URL is rebuilt from the card aweme id", () => {
  const video = {
    video_id: "123",
    aweme_id: "123",
    url: "https://www.douyin.com/video/999",
  }
  assert.equal(
    canonicalChannelVideoUrl("douyin", video),
    "https://www.douyin.com/video/123",
  )
})


test("different Douyin cards retain different canonical identities", () => {
  const first = canonicalChannelVideoUrl("douyin", { video_id: "123" })
  const second = canonicalChannelVideoUrl("douyin", { video_id: "456" })
  assert.notEqual(first, second)
})


test("channel detail requests outlive the bounded backend operation without hard-coding the first 18 videos", () => {
  assert.equal(channelRequestConfig.timeout, 50_000)
  assert.equal("params" in channelRequestConfig, false)
})


test("the first connected channel stays on the list until explicitly selected", () => {
  const channels = [{ id: "one", platform: "douyin" }]
  assert.equal(reconcileSelectedChannel(null, channels, "douyin"), null)
})


test("stale selected channel is cleared after platform or list changes", () => {
  const selected = { id: "old", platform: "douyin" }
  assert.equal(
    reconcileSelectedChannel(selected, [{ id: "new", platform: "douyin" }], "douyin"),
    null,
  )
  assert.equal(
    reconcileSelectedChannel(selected, [{ id: "old", platform: "youtube" }], "youtube"),
    null,
  )
})

test("disconnecting the shown channel also drops the detail view", () => {
  const channels = [
    { id: "a", platform: "douyin" },
    { id: "b", platform: "douyin" },
  ]
  const selected = { id: "a", platform: "douyin" }

  assert.deepEqual(
    channelsWithoutChannel(channels, "a").map((c) => c.id),
    ["b"],
  )
  // The bug: the list drops the row but the detail view keeps rendering it,
  // so the next fetch hits a deleted DB row and comes back 404.
  assert.equal(selectedChannelAfterDisconnect(selected, "a"), null)
  assert.deepEqual(selectedChannelAfterDisconnect(selected, "b"), selected)
  assert.equal(selectedChannelAfterDisconnect(null, "a"), null)
})
