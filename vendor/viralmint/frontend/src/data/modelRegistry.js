/**
 * Shared configuration constants for stock video generation.
 * Used by StockVideo studio page and supporting components.
 */

/* ── TTS Providers ────────────────────────────────────────────────── */

export const TTS_PROVIDERS = [
  { value: "edge_tts",   label: "Edge TTS — Free",     cost: "$0" },
  { value: "openai_tts", label: "OpenAI TTS — Budget", cost: "~$0.015/1K chars" },
]

/* ── Caption Styles ───────────────────────────────────────────────── */

export const CAPTION_STYLES = [
  { value: "viral",   label: "Viral — Word-by-word highlight" },
  { value: "classic", label: "Classic — Full sentence" },
  { value: "bold",    label: "Bold — 2-word highlight" },
  { value: "neon",    label: "Neon — Pink & cyan glow" },
  { value: "minimal", label: "Minimal — Clean & subtle" },
  { value: "karaoke", label: "Karaoke — Gray-to-yellow" },
  { value: "glow",    label: "Glow — Orange-gold accent" },
]

/* ── Music Genres ─────────────────────────────────────────────────── */

export const MUSIC_GENRES = [
  { value: "lofi",       label: "Lo-fi Chill" },
  { value: "cinematic",  label: "Cinematic" },
  { value: "upbeat",     label: "Upbeat Electronic" },
  { value: "ambient",    label: "Ambient" },
  { value: "corporate",  label: "Corporate / Motivational" },
  { value: "jazz",       label: "Jazz" },
  { value: "hiphop",     label: "Hip-Hop / Trap" },
  { value: "classical",  label: "Classical" },
  { value: "edm",        label: "EDM / Dance" },
  { value: "acoustic",   label: "Acoustic / Folk" },
  { value: "rnb",        label: "R&B / Soul" },
  { value: "rock",       label: "Rock" },
]
