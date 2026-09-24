export const PHASE_D_SMART_CUT_STEPS = [
  "source",
  "transcript",
  "range-proposals",
  "approval",
  "format",
  "render",
  "qc",
]

export const PHASE_E_REMAKE_STEPS = [
  "source",
  "scene-map",
  "creative-beats",
  "asset-map",
  "storyboard",
  "approval",
  "compose",
  "render",
  "qc",
]

export const STUDIO_ASSET_PROVIDERS = [
  {
    id: "stock",
    label: "Stock / Smart Video",
    path: "/stock",
    capabilities: ["b-roll", "generated-video", "script-driven"],
  },
  {
    id: "motion",
    label: "Motion Graphics",
    path: "/motion",
    capabilities: ["motion-graphics", "kinetic-type", "lower-thirds"],
  },
]

const ms = (seconds) => {
  const value = Number(seconds)
  return Number.isFinite(value) ? Math.round(value * 1000) : null
}

export function normalizeTranscriptSegments(segments = []) {
  return (Array.isArray(segments) ? segments : [])
    .map((segment, index) => {
      const startMs = segment.start_ms ?? ms(segment.start)
      const endMs = segment.end_ms ?? ms(segment.end)
      return {
        id: String(segment.id ?? index + 1),
        start_ms: startMs != null && Number.isFinite(Number(startMs)) ? Number(startMs) : null,
        end_ms: endMs != null && Number.isFinite(Number(endMs)) ? Number(endMs) : null,
        text: String(segment.text || "").trim(),
        words: Array.isArray(segment.words) ? segment.words : [],
      }
    })
    .filter((segment) => segment.start_ms != null && segment.end_ms != null && segment.end_ms >= segment.start_ms)
}

export function buildStudioSourceContext(source) {
  if (!source?.id) return null
  const segments = normalizeTranscriptSegments(source.transcript_segments)
  const durationMs = Number(source.duration_seconds) > 0
    ? Math.round(Number(source.duration_seconds) * 1000)
    : null
  const transcriptEndMs = segments.length ? Math.max(...segments.map((segment) => segment.end_ms)) : null
  const monotonic = segments.every((segment, index) => (
    index === 0 || segment.start_ms >= segments[index - 1].start_ms
  ))
  return {
    source_id: source.id,
    library_key: `dl:${source.id}`,
    title: source.title || source.id,
    platform: source.platform || null,
    source_url: source.source_url || null,
    media_metadata: source.media_metadata || {},
    duration_ms: durationMs,
    transcript: {
      available: Boolean(source.transcript || segments.length),
      text: source.transcript || "",
      language: source.transcript_language || null,
      source: source.transcript_source || null,
      segments,
    },
    timeline: {
      duration_ms: durationMs,
      transcript_end_ms: transcriptEndMs,
      monotonic,
      within_source: durationMs == null || transcriptEndMs == null || transcriptEndMs <= durationMs + 1000,
    },
  }
}

export function studioAssetProviderHref(provider, sourceId = "") {
  const record = typeof provider === "string"
    ? STUDIO_ASSET_PROVIDERS.find((item) => item.id === provider)
    : provider
  if (!record) return ""
  if (!sourceId) return record.path
  return `${record.path}?source=${encodeURIComponent(sourceId)}`
}

export const STUDIO_READINESS = {
  "smart-cut": {
    phase: "D",
    title: "Smart Cut",
    promise: "Reuse source media, canonical transcript and source timeline; propose bounded ranges for human approval before render.",
    steps: PHASE_D_SMART_CUT_STEPS,
    requires: ["source", "timeline"],
    review_categories: ["Timing", "Visual", "QC"],
  },
  remake: {
    phase: "E",
    title: "Creative Remake",
    promise: "Reuse the same source/project context, map external assets, and require explicit storyboard approval before composition/render.",
    steps: PHASE_E_REMAKE_STEPS,
    requires: ["source", "asset-providers", "storyboard-approval"],
    review_categories: ["Storyboard", "Visual", "QC"],
  },
}
