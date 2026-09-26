import { Typography, Alert } from "@mui/material"

export default function EstimatedCost({ ttsProvider, script }) {
  const wordCount = script ? script.trim().split(/\s+/).filter(Boolean).length : 0
  const estVideoSeconds = wordCount > 0 ? Math.ceil(wordCount / 2.5) : 0

  let ttsCost = 0
  if (ttsProvider === "openai_tts") ttsCost = 0.02

  const total = ttsCost
  return (
    <Alert severity={total === 0 ? "success" : "info"} sx={{ mt: 1.5 }}>
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        Chi phí ước tính: {total === 0 ? "Miễn phí" : `~$${total.toFixed(2)}`}
      </Typography>
      <Typography variant="caption" sx={{ color: "text.secondary", display: "block" }}>
        Giọng đọc: {ttsCost === 0 ? "Miễn phí (Edge TTS)" : `~$${ttsCost.toFixed(2)}`} · Video: Miễn phí (Pexels stock)
      </Typography>
      {estVideoSeconds > 0 && (
        <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: 0.25 }}>
          ~{estVideoSeconds}s video từ {wordCount} từ
        </Typography>
      )}
    </Alert>
  )
}
