export function candidateStatus(candidate) {
  if (candidate?.is_analyzed) return "Đã phân tích"
  if (candidate?.is_downloaded) return "Đã tải"
  return "Đã phát hiện"
}

export function downloadedByScoutResult(videos = []) {
  return new Map(
    videos
      .filter((video) => video?.scout_result_id && video?.file_exists)
      .map((video) => [video.scout_result_id, video.id]),
  )
}

export function thumbnailRequestKey(candidate) {
  const platform = candidate?.platform || ""
  const id = candidate?.id || candidate?.video_id || ""
  const url = candidate?.thumbnail_url || ""
  return `${platform}::${id}::${url}`
}
