import { adaptLocalizationException } from "./reviewModel.js"

export const REVIEW_SOURCES = [
  {
    id: "localize",
    workflow: "Localize",
    enabled: true,
    endpoint: "/api/localization/exceptions?limit=100",
    adapt: adaptLocalizationException,
  },
  {
    id: "smart-cut",
    workflow: "Smart Cut",
    enabled: false,
    endpoint: null,
    phase: "D",
  },
  {
    id: "creative-remake",
    workflow: "Creative Remake",
    enabled: false,
    endpoint: null,
    phase: "E",
  },
]

export async function loadReviewItems(httpClient, sources = REVIEW_SOURCES) {
  const active = sources.filter((source) => source.enabled && source.endpoint && source.adapt)
  const payloads = await Promise.all(active.map(async (source) => {
    const { data } = await httpClient.get(source.endpoint)
    return (data?.items || []).map(source.adapt)
  }))
  return payloads.flat()
}
