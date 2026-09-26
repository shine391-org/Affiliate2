import { Box, Typography, LinearProgress, Stack } from "@mui/material"
import useAppStore from "../../store/appStore"
import { jobTypeLabel } from "../videos/constants"

const JOB_COLORS = {
  scout: "primary", download: "info", generate: "warning", upload: "success",
}

export default function JobProgressCard({ jobId, jobType, message }) {
  const job = useAppStore((s) => s.activeJobs[jobId])

  // Job removed from store (auto-cleaned after completion) — hide the card
  if (!job) return null

  const status = job.status || "running"
  const percent = job.percent || 0
  const step = job.step || message || ""

  const color = JOB_COLORS[jobType] || "primary"
  const label = jobTypeLabel(jobType)
  const isRunning = status === "running"
  const isSuccess = status === "success"
  const isFailed = status === "failed"

  return (
    <Box sx={{ mb: 0.5 }}>
      <Stack direction="row" spacing={0.75} alignItems="center" sx={{ mb: 0.25 }}>
        <Typography variant="caption" sx={{
          fontWeight: 600, fontSize: "0.75rem",
          color: isSuccess ? "success.main" : isFailed ? "error.main" : "text.secondary",
        }}>
          {isSuccess ? `${label} hoàn tất` : isFailed ? `${label} thất bại` : `${label}...`}
        </Typography>
        {isRunning && percent > 0 && (
          <Typography variant="caption" sx={{ fontSize: "0.65rem", color: "text.disabled" }}>
            {Math.round(percent)}%
          </Typography>
        )}
      </Stack>

      <LinearProgress
        variant={isRunning ? (percent > 0 ? "determinate" : "indeterminate") : "determinate"}
        value={isRunning ? percent : 100}
        color={isSuccess ? "success" : isFailed ? "error" : color}
        sx={{ borderRadius: 1, height: 3 }}
      />

      {step && !isSuccess && (
        <Typography variant="caption" sx={{ color: "text.disabled", fontSize: "0.65rem", mt: 0.25, display: "block" }}>
          {step}
        </Typography>
      )}
    </Box>
  )
}
