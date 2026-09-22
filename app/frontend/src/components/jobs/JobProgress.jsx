import { useEffect, useState } from "react"
import { Box, LinearProgress, Typography } from "@mui/material"
import { ws } from "../../api/websocket"

export default function JobProgress({ jobId }) {
  const [progress, setProgress] = useState({ percent: 0, step: "" })

  useEffect(() => {
    const unsub = ws.on("job_progress", (msg) => {
      if (msg.job_id === jobId) {
        setProgress({ percent: msg.percent, step: msg.step })
      }
    })
    return unsub
  }, [jobId])

  return (
    <Box sx={{ mb: 0.5 }}>
      <LinearProgress
        variant="determinate"
        value={progress.percent}
        color={progress.percent >= 100 ? "success" : "secondary"}
        sx={{ borderRadius: 1, height: 6 }}
      />
      {progress.step && (
        <Typography variant="caption" sx={{ color: "text.secondary", mt: 0.25, display: "block" }}>
          {progress.step}
        </Typography>
      )}
    </Box>
  )
}
