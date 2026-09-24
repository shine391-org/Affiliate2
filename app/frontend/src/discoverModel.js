export function candidateStatus(candidate) {
  if (candidate?.is_analyzed) return "Analyzed"
  if (candidate?.is_downloaded) return "Downloaded"
  return "Discovered"
}

export function downloadedByScoutResult(videos = []) {
  return new Map(
    videos
      .filter((video) => video?.scout_result_id && video?.file_exists)
      .map((video) => [video.scout_result_id, video.id]),
  )
}
