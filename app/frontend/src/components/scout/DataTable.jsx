import { useState, useMemo } from "react"
import {
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TableSortLabel, Checkbox, Chip, Paper, IconButton, Tooltip, Button,
} from "@mui/material"
import LaunchIcon from "@mui/icons-material/Launch"
import DownloadIcon from "@mui/icons-material/Download"
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline"

function formatViews(n) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

export default function DataTable({ results, onSelect, onDownload, onDelete, selectedIds, onToggle }) {
  const [sortKey, setSortKey] = useState("virality_score")
  const [sortDir, setSortDir] = useState("desc")
  const [loadingId, setLoadingId] = useState(null)

  const sorted = useMemo(() => {
    if (!results) return []
    return [...results].sort((a, b) => {
      let va = a[sortKey] ?? 0
      let vb = b[sortKey] ?? 0
      if (sortKey === "upload_date") {
        va = va ? new Date(va).getTime() : 0
        vb = vb ? new Date(vb).getTime() : 0
      }
      return sortDir === "desc" ? vb - va : va - vb
    })
  }, [results, sortKey, sortDir])

  const handleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === "desc" ? "asc" : "desc")
    else { setSortKey(key); setSortDir("desc") }
  }

  const handleDownload = async (e, id) => {
    e.stopPropagation()
    setLoadingId(id)
    try { await onDownload(id) } finally { setLoadingId(null) }
  }

  const columns = [
    { key: "title", label: "Title", width: "30%" },
    { key: "platform", label: "Platform", width: "8%" },
    { key: "views", label: "Views", width: "10%" },
    { key: "likes", label: "Likes", width: "8%" },
    { key: "comments", label: "Comments", width: "8%" },
    { key: "virality_score", label: "Score", width: "8%" },
    { key: "upload_date", label: "Uploaded", width: "10%" },
    { key: "_actions", label: "Actions", width: "18%", sortable: false },
  ]

  return (
    <TableContainer component={Paper} elevation={0} sx={{ border: 1, borderColor: "divider" }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            {onToggle && <TableCell padding="checkbox" />}
            {columns.map(col => (
              <TableCell key={col.key} sx={{ width: col.width }}>
                {col.sortable === false ? col.label : (
                  <TableSortLabel
                    active={sortKey === col.key}
                    direction={sortKey === col.key ? sortDir : "desc"}
                    onClick={() => handleSort(col.key)}
                  >
                    {col.label}
                  </TableSortLabel>
                )}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {sorted.map(r => (
            <TableRow
              key={r.id}
              hover
              onClick={() => onSelect?.(r)}
              selected={selectedIds?.has(r.id)}
              sx={{ cursor: "pointer" }}
            >
              {onToggle && (
                <TableCell padding="checkbox">
                  <Checkbox
                    checked={selectedIds?.has(r.id) || false}
                    onChange={(e) => onToggle(r.id, e)}
                    onClick={(e) => e.stopPropagation()}
                    size="small"
                  />
                </TableCell>
              )}
              <TableCell sx={{ maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {r.title}
              </TableCell>
              <TableCell>
                <Chip label={r.platform} size="small" variant="outlined" sx={{ textTransform: "uppercase", fontSize: "0.65rem", height: 22 }} />
              </TableCell>
              <TableCell>{formatViews(r.views)}</TableCell>
              <TableCell>{formatViews(r.likes)}</TableCell>
              <TableCell>{formatViews(r.comments)}</TableCell>
              <TableCell>
                <Chip
                  label={(r.virality_score || 0).toFixed(1)}
                  size="small"
                  color={(r.virality_score || 0) >= 70 ? "success" : (r.virality_score || 0) >= 40 ? "warning" : "error"}
                  sx={{ fontWeight: 700, fontSize: "0.75rem" }}
                />
              </TableCell>
              <TableCell sx={{ fontSize: "0.8rem", color: "text.secondary", whiteSpace: "nowrap" }}>
                {r.upload_date
                  ? new Date(r.upload_date).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })
                  : "—"}
              </TableCell>
              <TableCell padding="none" sx={{ whiteSpace: "nowrap" }}>
                {!r.is_downloaded && (
                  <Button
                    size="small"
                    variant="contained"
                    startIcon={<DownloadIcon />}
                    onClick={(e) => handleDownload(e, r.id)}
                    disabled={loadingId === r.id}
                    sx={{ mr: 0.5 }}
                  >
                    {loadingId === r.id ? "..." : "Download"}
                  </Button>
                )}
                {r.video_url && (
                  <Tooltip title="Open original" arrow>
                    <IconButton size="small" onClick={(e) => { e.stopPropagation(); window.open(r.video_url, "_blank", "noopener") }}
                      sx={{ color: "text.secondary", "&:hover": { color: "primary.main" } }}>
                      <LaunchIcon sx={{ fontSize: "1.1rem" }} />
                    </IconButton>
                  </Tooltip>
                )}
                <Tooltip title="Delete" arrow>
                  <IconButton size="small" onClick={(e) => { e.stopPropagation(); onDelete(r.id) }}
                    sx={{ color: "text.secondary", "&:hover": { color: "error.main" } }}>
                    <DeleteOutlineIcon sx={{ fontSize: "1.1rem" }} />
                  </IconButton>
                </Tooltip>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}
