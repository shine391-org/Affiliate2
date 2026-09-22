import { useState, useEffect } from "react"
import {
  TTS_PROVIDERS, CAPTION_STYLES, MUSIC_GENRES,
} from "../data/modelRegistry"

/** Map config keys to their hardcoded fallback exports */
const FALLBACKS = {
  tts_providers: TTS_PROVIDERS,
  caption_styles: CAPTION_STYLES,
  music_genres: MUSIC_GENRES,
}

/**
 * Hook to read app config (currently sourced from hardcoded defaults).
 * Returns immediately with the local fallback — no network call.
 */
export default function useRemoteConfig(key) {
  const fallback = FALLBACKS[key] ?? null
  const [data] = useState(fallback)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(false)
  }, [key])

  return { data, loading }
}
