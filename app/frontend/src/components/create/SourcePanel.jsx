import { useState } from "react"
import { Box, Typography, Button, Stack, Paper } from "@mui/material"

function InsightRow({ label, color, value }) {
  return (
    <Box>
      <Typography variant="caption" sx={{ color, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ fontSize: "0.85rem", lineHeight: 1.5 }}>{value}</Typography>
    </Box>
  )
}

export default function SourcePanel({ source }) {
  if (!source) return null
  const insights = source.insights || {}
  const [showTranscript, setShowTranscript] = useState(false)

  return (
    <Paper variant="outlined" sx={{ p: 2, borderRadius: 2, mb: 2 }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
        <Typography variant="overline" sx={{ color: "text.secondary" }}>Inspiration Source</Typography>
      </Stack>
      <Typography variant="body2" sx={{ fontWeight: 600, mb: 1.5 }}>{source.title || "Untitled"}</Typography>

      {source.transcript && (
        <Box sx={{ mb: 1.5 }}>
          <Button size="small" variant="text" onClick={() => setShowTranscript(!showTranscript)}
            sx={{ p: 0, minWidth: 0 }}>
            {showTranscript ? "Hide transcript" : "Show transcript"}
            {source.transcript_language && ` (${source.transcript_language})`}
          </Button>
          {showTranscript && (
            <Paper variant="outlined" sx={{ p: 1.5, mt: 0.5, maxHeight: 150, overflowY: "auto", fontSize: "0.8rem", color: "text.secondary", lineHeight: 1.6 }}>
              {source.transcript}
            </Paper>
          )}
        </Box>
      )}

      {Object.keys(insights).length > 0 && (
        <Stack spacing={1}>
          {insights.hook && <InsightRow label="Hook" color="secondary.main" value={insights.hook} />}
          {insights.structure && <InsightRow label="Structure" color="secondary.main" value={insights.structure} />}
          {insights.why_viral && <InsightRow label="Why viral" color="warning.main" value={insights.why_viral} />}
          {insights.suggested_angle && <InsightRow label="Your angle" color="primary.main" value={insights.suggested_angle} />}
          {insights.suggested_title && <InsightRow label="Title idea" color="primary.main" value={insights.suggested_title} />}
        </Stack>
      )}
    </Paper>
  )
}
