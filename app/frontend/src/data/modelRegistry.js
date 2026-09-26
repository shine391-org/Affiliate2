/**
 * Shared configuration constants for stock video generation.
 * Used by StockVideo studio page and supporting components.
 */

/* ── TTS Providers ────────────────────────────────────────────────── */

export const TTS_PROVIDERS = [
  { value: "edge_tts",   label: "Edge TTS — Miễn phí",     cost: "$0" },
  { value: "openai_tts", label: "OpenAI TTS — Tiết kiệm", cost: "~$0.015/1K chars" },
]

/* ── Caption Styles ───────────────────────────────────────────────── */

export const CAPTION_STYLES = [
  { value: "viral",   label: "Viral — Tô sáng từng từ" },
  { value: "classic", label: "Cổ điển — Cả câu" },
  { value: "bold",    label: "Đậm — Tô sáng 2 từ" },
  { value: "neon",    label: "Neon — Phát sáng hồng & cyan" },
  { value: "minimal", label: "Tối giản — Sạch & nhẹ" },
  { value: "karaoke", label: "Karaoke — Xám sang vàng" },
  { value: "glow",    label: "Phát sáng — Nhấn cam-vàng" },
]

/* ── Music Genres ─────────────────────────────────────────────────── */

export const MUSIC_GENRES = [
  { value: "lofi",       label: "Lo-fi thư giãn" },
  { value: "cinematic",  label: "Cinematic" },
  { value: "upbeat",     label: "Điện tử sôi động" },
  { value: "ambient",    label: "Ambient" },
  { value: "corporate",  label: "Doanh nghiệp / Truyền cảm hứng" },
  { value: "jazz",       label: "Jazz" },
  { value: "hiphop",     label: "Hip-Hop / Trap" },
  { value: "classical",  label: "Cổ điển" },
  { value: "edm",        label: "EDM / Dance" },
  { value: "acoustic",   label: "Acoustic / Folk" },
  { value: "rnb",        label: "R&B / Soul" },
  { value: "rock",       label: "Rock" },
]
