export const channelRequestConfig = Object.freeze({
  timeout: 50_000,
  params: Object.freeze({ limit: 18 }),
})

export function canonicalChannelVideoUrl(platform, video = {}) {
  if (platform !== "douyin") {
    return String(video.url || video.source_url || "").trim()
  }

  const awemeId = String(video.aweme_id || "").trim()
  const videoId = String(video.video_id || "").trim()
  if (awemeId && videoId && awemeId !== videoId) return ""
  const expectedId = awemeId || videoId
  if (!/^\d+$/.test(expectedId)) return ""
  return `https://www.douyin.com/video/${expectedId}`
}

export function reconcileSelectedChannel(selected, channels, platform) {
  if (!selected || selected.platform !== platform) return null
  return channels.find((channel) => (
    channel.id === selected.id && channel.platform === platform
  )) || null
}

// Disconnecting the channel the detail view is showing must drop the detail
// view too, otherwise it keeps rendering against a deleted DB row and the
// next fetch comes back 404.
export function channelsWithoutChannel(channels, channelId) {
  return channels.filter((channel) => channel.id !== channelId)
}

export function selectedChannelAfterDisconnect(selected, channelId) {
  if (!selected) return null
  return selected.id === channelId ? null : selected
}
