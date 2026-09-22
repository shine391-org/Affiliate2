import { useState } from "react"
import { Box, Typography, Stack, TextField, Alert } from "@mui/material"
import ContentCutIcon from "@mui/icons-material/ContentCutOutlined"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"

// Trim / Cut — keep a [start, end] window of a video. Pure FFmpeg (frame-
// accurate re-encode). Times accept either seconds (12.5) or MM:SS
// (1:23) / HH:MM:SS for convenience.

function parseTime(s) {
  const raw = (s || "").trim()
  if (!raw) return NaN
  if (raw.includes(":")) {
    const parts = raw.split(":").map((p) => parseFloat(p))
    if (parts.some((n) => isNaN(n))) return NaN
    return parts.reduce((acc, n) => acc * 60 + n, 0)
  }
  return parseFloat(raw)
}

export default function ToolTrim() {
  useDocumentTitle("Trim / Cut")
  const [start, setStart] = useState("0:00")
  const [end, setEnd] = useState("")

  const startSec = parseTime(start)
  const endSec = parseTime(end)
  const startOk = !isNaN(startSec) && startSec >= 0
  const endOk = !isNaN(endSec) && endSec > 0
  const rangeOk = startOk && endOk && endSec - startSec >= 0.5
  const tooShort = startOk && endOk && endSec - startSec < 0.5

  return (
    <ToolRunner
      title="Trim / Cut"
      description="Keep just the part you want — set a start and end, drop the rest"
      icon={<ContentCutIcon fontSize="large" />}
      endpoint="/api/tools/trim"
      processLabel="Trim"
      canSubmit={rangeOk}
      disabledReason={
        !endOk ? "Set an end time" : tooShort ? "Range must be at least 0.5s" : "Set a start before the end"
      }
      fieldBuilder={() => ({ start_seconds: startSec, end_seconds: endSec })}
    >
      <Stack spacing={2.5}>
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 1 }}>
            Keep from → to
          </Typography>
          <Stack direction="row" spacing={2}>
            <TextField
              label="Start" size="small" sx={{ width: 150 }}
              placeholder="0:00"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              error={start !== "" && !startOk}
              helperText="seconds or M:SS"
            />
            <TextField
              label="End" size="small" sx={{ width: 150 }}
              placeholder="0:30"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              error={end !== "" && (!endOk || tooShort)}
              helperText={tooShort ? "≥ 0.5s range" : "seconds or M:SS"}
            />
          </Stack>
        </Box>

        <Alert severity="info" sx={{ fontSize: "0.82rem" }}>
          Frame-accurate (the clip is re-encoded, not keyframe-snapped). Source
          aspect ratio and audio are preserved. End is clamped to the real
          video length, so you can overshoot safely.
        </Alert>
      </Stack>
    </ToolRunner>
  )
}
