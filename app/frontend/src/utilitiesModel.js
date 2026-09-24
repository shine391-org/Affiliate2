export const UTILITY_OWNERSHIP = {
  "video-download": "CONTEXTUAL_PLUS_STANDALONE",
  "merge-clips": "KEEP_STANDALONE",
  "transform": "KEEP_STANDALONE",
  "remove-audio": "KEEP_STANDALONE",
  "compress": "KEEP_STANDALONE",
  "crop": "KEEP_STANDALONE",
  "reframe": "CONTEXTUAL_PLUS_STANDALONE",
  "watermark": "KEEP_STANDALONE",
  "gif": "KEEP_STANDALONE",
  "speed": "KEEP_STANDALONE",
  "trim": "CONTEXTUAL_PLUS_STANDALONE",
  "auto-zoom": "KEEP_STANDALONE",
  "voiceover": "KEEP_STANDALONE",
  "audio-enhance": "KEEP_STANDALONE",
  "remove-silence": "KEEP_STANDALONE",
  "music-visualizer": "KEEP_STANDALONE",
  "captions": "CONTEXTUAL_PLUS_STANDALONE",
  "subtitles": "CONTEXTUAL_PLUS_STANDALONE",
  "translate": "WORKFLOW_OWNED",
  "localization-batch": "WORKFLOW_OWNED",
  "localization-review": "WORKFLOW_OWNED",
  "hook-analysis": "KEEP_STANDALONE",
  "metadata": "KEEP_STANDALONE",
  "auto-chapters": "KEEP_STANDALONE",
}

export const WORKFLOW_OWNED_UTILITY_IDS = new Set(
  Object.entries(UTILITY_OWNERSHIP)
    .filter(([, owner]) => owner === "WORKFLOW_OWNED")
    .map(([id]) => id),
)

export function visibleUtilityIds(ids) {
  return ids.filter((id) => !WORKFLOW_OWNED_UTILITY_IDS.has(id))
}
