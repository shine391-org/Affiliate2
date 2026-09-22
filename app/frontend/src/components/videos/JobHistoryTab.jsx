import { useState } from "react"
import {
  Box, Typography, Chip, Stack, Paper, IconButton, TablePagination, Button, Checkbox,
} from "@mui/material"
import WorkIcon from "@mui/icons-material/WorkOutline"
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline"
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline"
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline"
import CancelIcon from "@mui/icons-material/Close"
import { JOB_TYPE_LABEL, JOB_STATUS_COLOR } from "./constants"

export default function JobHistoryTab({ jobs, jobTotal, onDelete, onBulkDelete, onCancel, page, rowsPerPage, onPageChange, onRowsPerPageChange }) {
  const recentJobs = [...jobs].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
  const [selected, setSelected] = useState(new Set())

  const toggleSelect = (id) => setSelected(prev => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  const allOnPage = recentJobs.map(j => j.id)
  const allSelected = allOnPage.length > 0 && allOnPage.every(id => selected.has(id))
  const someSelected = allOnPage.some(id => selected.has(id)) && !allSelected

  const toggleAll = () => {
    if (allSelected) {
      setSelected(new Set())
    } else {
      setSelected(new Set(allOnPage))
    }
  }

  const handleBulkDelete = () => {
    const ids = [...selected]
    if (ids.length > 0) {
      onBulkDelete(ids, () => setSelected(new Set()))
    }
  }

  if (recentJobs.length === 0) {
    return (
      <Box sx={{ textAlign: "center", py: 8, color: "text.secondary" }}>
        <Typography variant="h6" sx={{ mb: 0.5 }}>No jobs yet</Typography>
        <Typography variant="body2">Start scouting from the Chat page to see jobs here.</Typography>
      </Box>
    )
  }

  return (
    <>
    {/* Bulk action bar */}
    <Stack direction="row" alignItems="center" justifyContent="flex-end" spacing={1.5} sx={{ mb: 1, px: 0.5 }}>
      {selected.size > 0 && (
        <Button
          size="small"
          color="error"
          variant="outlined"
          startIcon={<DeleteOutlineIcon sx={{ fontSize: 16 }} />}
          onClick={handleBulkDelete}
          sx={{ textTransform: "none", fontSize: "0.75rem", height: 28 }}
        >
          Delete {selected.size} job{selected.size > 1 ? "s" : ""}
        </Button>
      )}
      <Typography variant="caption" sx={{ color: "text.secondary" }}>
        {selected.size > 0 ? `${selected.size} selected` : "Select all"}
      </Typography>
      <Checkbox
        size="small"
        checked={allSelected}
        indeterminate={someSelected}
        onChange={toggleAll}
        sx={{ p: 0.5 }}
      />
    </Stack>
    <Stack spacing={0.5}>
      {recentJobs.map(j => {
        const isActive = j.status === "running" || j.status === "pending"
        const isDeletable = j.status === "failed" || j.status === "cancelled" || j.status === "success"
        return (
          <Paper key={j.id} elevation={0} sx={{ px: 2, py: 1.25, border: 1, borderColor: selected.has(j.id) ? "primary.main" : "divider", borderRadius: 3, transition: "all 0.15s ease", "&:hover": { borderColor: "action.selected", boxShadow: (theme) => theme.customShadows?.sm } }}>
            <Stack direction="row" alignItems="center" spacing={1.5}>
              {j.status === "success" ? <CheckCircleOutlineIcon sx={{ fontSize: 18, color: "success.main" }} /> :
                j.status === "failed" || j.status === "cancelled" ? <ErrorOutlineIcon sx={{ fontSize: 18, color: "error.main" }} /> :
                <WorkIcon sx={{ fontSize: 18, color: "info.main" }} />}
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Chip label={JOB_TYPE_LABEL[j.job_type] || j.job_type} size="small" variant="outlined" sx={{ height: 20, fontSize: "0.65rem" }} />
                  <Chip label={j.status} size="small" color={JOB_STATUS_COLOR[j.status] || "default"} sx={{ height: 20, fontSize: "0.65rem" }} />
                  {j.title && <Typography variant="body2" sx={{ fontWeight: 500, flex: 1 }} noWrap>{j.title}</Typography>}
                </Stack>
                {j.current_step && <Typography variant="caption" sx={{ color: "text.secondary" }}>{j.current_step}</Typography>}
                {j.error_message && <Typography variant="caption" sx={{ color: "error.main" }}>{j.error_message}</Typography>}
              </Box>
              <Typography variant="caption" sx={{ color: "text.disabled", whiteSpace: "nowrap" }}>
                {j.created_at ? new Date(j.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}
              </Typography>
              {isActive && (
                <IconButton size="small" onClick={() => onCancel(j.id)} title="Cancel job" sx={{ color: "text.disabled", "&:hover": { color: "warning.main" } }}>
                  <CancelIcon sx={{ fontSize: 16 }} />
                </IconButton>
              )}
              {isDeletable && (
                <IconButton size="small" onClick={() => onDelete(j.id)} title="Delete job" sx={{ color: "text.disabled", "&:hover": { color: "error.main" } }}>
                  <DeleteOutlineIcon sx={{ fontSize: 16 }} />
                </IconButton>
              )}
              <Checkbox
                size="small"
                checked={selected.has(j.id)}
                onChange={() => toggleSelect(j.id)}
                sx={{ p: 0.5 }}
              />
            </Stack>
          </Paper>
        )
      })}
    </Stack>
    <TablePagination
      component="div"
      count={jobTotal}
      page={page}
      onPageChange={onPageChange}
      rowsPerPage={rowsPerPage}
      onRowsPerPageChange={onRowsPerPageChange}
      rowsPerPageOptions={[10, 20, 50]}
      sx={{ borderTop: 1, borderColor: "divider", mt: 1 }}
    />
    </>
  )
}
