import { useState } from "react"
import { Box, Stack, Paper, Typography, LinearProgress, CircularProgress, IconButton, Chip, Collapse, alpha } from "@mui/material"
import CancelIcon from "@mui/icons-material/Cancel"
import ExpandMoreIcon from "@mui/icons-material/ExpandMore"
import ExpandLessIcon from "@mui/icons-material/ExpandLess"
import useAppStore from "../../store/appStore"

/**
 * Shared active-jobs progress banner used across all video/clip pages.
 *
 * Props:
 *  - filter(job) → bool    Optional predicate to pick which jobs to show.
 *  - onCancel(jobId)        If provided, a cancel button is shown per job.
 *  - fallbackLabel          Text shown when step/message are empty (default "Processing…").
 *  - collapseThreshold      Number of jobs before auto-collapsing (default 2).
 */
export default function ActiveJobsBanner({ filter, onCancel, fallbackLabel = "Processing…", collapseThreshold = 2 }) {
  const activeJobs = useAppStore((s) => s.activeJobs)
  const [expanded, setExpanded] = useState(false)

  const defaultFilter = (j) => j.status === "running" && j.jobType === "generate"
  const runningJobs = Object.values(activeJobs).filter(filter || defaultFilter)

  if (runningJobs.length === 0) return null

  const shouldCollapse = runningJobs.length > collapseThreshold

  return (
    <Box sx={{ px: 3, py: 1.5, borderBottom: 1, borderColor: "divider", flexShrink: 0 }}>
      {/* Collapse / expand toggle when many jobs */}
      {shouldCollapse && (
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1, cursor: "pointer" }}
          onClick={() => setExpanded(!expanded)}>
          <Chip
            label={`${runningJobs.length} active jobs`}
            size="small"
            color="primary"
            sx={{ fontWeight: 600, fontSize: "0.75rem" }}
          />
          {expanded ? <ExpandLessIcon fontSize="small" sx={{ color: "text.secondary" }} /> : <ExpandMoreIcon fontSize="small" sx={{ color: "text.secondary" }} />}
        </Stack>
      )}

      {/* Show first job always; rest collapse */}
      <Stack spacing={1.5}>
        <JobCard job={runningJobs[0]} fallbackLabel={fallbackLabel} onCancel={onCancel} />
        {shouldCollapse ? (
          <Collapse in={expanded}>
            <Stack spacing={1.5}>
              {runningJobs.slice(1).map(job => (
                <JobCard key={job.jobId} job={job} fallbackLabel={fallbackLabel} onCancel={onCancel} />
              ))}
            </Stack>
          </Collapse>
        ) : (
          runningJobs.slice(1).map(job => (
            <JobCard key={job.jobId} job={job} fallbackLabel={fallbackLabel} onCancel={onCancel} />
          ))
        )}
      </Stack>
    </Box>
  )
}

function JobCard({ job, fallbackLabel, onCancel }) {
  return (
    <Paper elevation={0} sx={{
      p: 2, borderRadius: 2.5,
      border: 1, borderColor: "primary.main",
      bgcolor: (t) => alpha(t.palette.primary.main, 0.04),
    }}>
      <Stack direction="row" spacing={2} alignItems="center">
        <CircularProgress size={20} thickness={5} sx={{ color: "primary.main", flexShrink: 0 }} />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 0.5 }}>
            <Typography variant="body2" noWrap sx={{ fontWeight: 600, fontSize: "0.85rem", flex: 1, minWidth: 0, mr: 1 }}>
              {job.step || job.message || fallbackLabel}
            </Typography>
            <Stack direction="row" spacing={0.5} alignItems="center" sx={{ flexShrink: 0 }}>
              <Typography variant="caption" sx={{ color: "primary.main", fontWeight: 700 }}>
                {Math.round(job.percent || 0)}%
              </Typography>
              {onCancel && (
                <IconButton size="small" onClick={() => onCancel(job.jobId)}
                  sx={{ p: 0.25, color: "text.disabled", "&:hover": { color: "error.main" } }}
                  title="Cancel job">
                  <CancelIcon sx={{ fontSize: 16 }} />
                </IconButton>
              )}
            </Stack>
          </Stack>
          <LinearProgress
            variant={job.percent > 0 ? "determinate" : "indeterminate"}
            value={job.percent || 0}
            sx={{
              height: 6, borderRadius: 3,
              bgcolor: (t) => alpha(t.palette.primary.main, 0.12),
              "& .MuiLinearProgress-bar": { borderRadius: 3 },
            }}
          />
        </Box>
      </Stack>
    </Paper>
  )
}
