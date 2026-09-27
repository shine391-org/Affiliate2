import { useState, useMemo } from "react"
import {
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TableSortLabel, Checkbox, Chip, Paper, IconButton, Tooltip, Button,
} from "@mui/material"
import LaunchIcon from "@mui/icons-material/Launch"
import DownloadIcon from "@mui/icons-material/Download"
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline"
import ContentCutIcon from "@mui/icons-material/ContentCutOutlined"
import { candidateStatus } from "../../discoverModel"
import { formatCount, formatScore, scoreSeverity } from "../../scoutScore"

function formatViews(n) {
  return formatCount(n)
}

export default function DataTable({
  results, onSelect, onDownload, onDelete, selectedIds, onToggle,
  downloadedIndex, onOpenStudio, showCandidateStatus = false,
}) {
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
    { key: "title", label: "Tiêu đề", width: "30%" },
    { key: "platform", label: "Nền tảng", width: "8%" },
    { key: "views", label: "Lượt xem", width: "10%" },
    { key: "likes", label: "Lượt thích", width: "8%" },
    { key: "comments", label: "Bình luận", width: "8%" },
    { key: "virality_score", label: "Viral", width: "7%" },
    { key: "radar_score", label: "Radar", width: "7%" },
    ...(showCandidateStatus ? [{ key: "_status", label: "Trạng thái", width: "8%", sortable: false }] : []),
    { key: "upload_date", label: "Đã tải lên", width: "10%" },
    { key: "_actions", label: "Thao tác", width: "18%", sortable: false },
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
                  label={formatScore(r.virality_score)}
                  size="small"
                  color={scoreSeverity(r.virality_score)}
                  sx={{ fontWeight: 700, fontSize: "0.75rem" }}
                />
              </TableCell>
              <TableCell>
                {r.radar_score == null ? "—" : (
                  <Tooltip title={`Độ tin cậy Radar: ${Math.round((r.radar_confidence || 0) * 100)}%`} arrow>
                    <Chip
                      label={Number(r.radar_score).toFixed(1)}
                      size="small"
                      color={r.radar_score >= 70 ? "success" : r.radar_score >= 45 ? "warning" : "default"}
                      variant="outlined"
                      sx={{ fontWeight: 700, fontSize: "0.75rem" }}
                    />
                  </Tooltip>
                )}
              </TableCell>
              {showCandidateStatus && (
                <TableCell>
                  <Chip label={candidateStatus(r)} size="small" variant="outlined"
                    color={r.is_analyzed ? "success" : r.is_downloaded ? "info" : "default"} />
                </TableCell>
              )}
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
                {downloadedIndex?.has(r.id) && onOpenStudio && (
                  <Tooltip title="Mở nội dung cục bộ trong Studio" arrow>
                    <IconButton size="small" onClick={(e) => { e.stopPropagation(); onOpenStudio(r) }}
                      sx={{ color: "text.secondary", "&:hover": { color: "primary.main" } }}>
                      <ContentCutIcon sx={{ fontSize: "1.1rem" }} />
                    </IconButton>
                  </Tooltip>
                )}
                {r.video_url && (
                  <Tooltip title="Mở bản gốc" arrow>
                    <IconButton size="small" onClick={(e) => { e.stopPropagation(); window.open(r.video_url, "_blank", "noopener") }}
                      sx={{ color: "text.secondary", "&:hover": { color: "primary.main" } }}>
                      <LaunchIcon sx={{ fontSize: "1.1rem" }} />
                    </IconButton>
                  </Tooltip>
                )}
                <Tooltip title="Xóa" arrow>
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
