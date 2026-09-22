import { useState } from "react"
import { useNavigate } from "react-router-dom"
import {
  Box, Typography, Card, CardMedia, CardContent, Chip, Button,
  Stack, IconButton, Checkbox,
} from "@mui/material"
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft"
import ChevronRightIcon from "@mui/icons-material/ChevronRight"
import DownloadIcon from "@mui/icons-material/DownloadOutlined"
import LaunchIcon from "@mui/icons-material/Launch"
import VisibilityIcon from "@mui/icons-material/VisibilityOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

function formatViews(n) {
  if (!n) return "0"
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function ViralityChip({ score }) {
  const color = score >= 70 ? "success" : score >= 40 ? "warning" : "error"
  return <Chip label={score.toFixed(1)} size="small" color={color} sx={{ fontWeight: 700, fontSize: "0.7rem", height: 22 }} />
}

export default function ScoutResultsCard({ results, platform, jobId }) {
  const [scrollIdx, setScrollIdx] = useState(0)
  const [selectedIds, setSelectedIds] = useState(new Set())
  const [downloading, setDownloading] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const navigate = useNavigate()

  if (!results || results.length === 0) return null

  const sorted = [...results].sort((a, b) => (b.virality_score || 0) - (a.virality_score || 0))
  const visible = expanded ? sorted : sorted.slice(0, 8)
  const CARD_WIDTH = 200
  const GAP = 10
  const maxScroll = Math.max(0, visible.length - 3)

  const toggleSelect = (id, e) => {
    e.stopPropagation()
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleDownload = async () => {
    if (selectedIds.size === 0) return
    setDownloading(true)
    try {
      const { data } = await http.post("/api/scout/download", { scout_result_ids: [...selectedIds] })
      showSnackbar(`Downloading ${data.count} videos...`, "success")
      setSelectedIds(new Set())
    } catch (err) {
      showSnackbar(err.response?.data?.detail || err.message, "error")
    } finally {
      setDownloading(false)
    }
  }

  const selectTop = (n) => {
    setSelectedIds(new Set(sorted.slice(0, n).map(r => r.id)))
  }

  return (
    <Box sx={{
      bgcolor: "background.paper",
      border: 1, borderColor: "divider",
      borderRadius: 3, p: 2, mb: 0.5,
    }}>
      {/* Header */}
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Chip label={platform?.toUpperCase() || "SCOUT"} size="small" variant="outlined" sx={{ fontSize: "0.7rem" }} />
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            {results.length} result{results.length !== 1 ? "s" : ""} found
          </Typography>
        </Stack>
        <Stack direction="row" spacing={0.5}>
          <Button size="small" variant="text" onClick={() => selectTop(5)}
            sx={{ minWidth: 0, color: "text.secondary" }}>
            Top 5
          </Button>
          <Button size="small" variant="text"
            onClick={() => setSelectedIds(prev => prev.size === sorted.length ? new Set() : new Set(sorted.map(r => r.id)))}
            sx={{ minWidth: 0, color: "text.secondary" }}>
            {selectedIds.size === sorted.length ? "None" : "All"}
          </Button>
        </Stack>
      </Stack>

      {/* Carousel */}
      <Box sx={{ position: "relative" }}>
        {scrollIdx > 0 && (
          <IconButton size="small" onClick={() => setScrollIdx(i => Math.max(0, i - 2))}
            sx={{ position: "absolute", left: -6, top: "40%", zIndex: 2, bgcolor: "background.paper", border: 1, borderColor: "divider", "&:hover": { bgcolor: "action.hover" } }}>
            <ChevronLeftIcon fontSize="small" />
          </IconButton>
        )}
        {scrollIdx < maxScroll && (
          <IconButton size="small" onClick={() => setScrollIdx(i => Math.min(maxScroll, i + 2))}
            sx={{ position: "absolute", right: -6, top: "40%", zIndex: 2, bgcolor: "background.paper", border: 1, borderColor: "divider", "&:hover": { bgcolor: "action.hover" } }}>
            <ChevronRightIcon fontSize="small" />
          </IconButton>
        )}

        <Box sx={{ overflow: "hidden" }}>
          <Stack direction="row" spacing={`${GAP}px`} sx={{
            transform: `translateX(-${scrollIdx * (CARD_WIDTH + GAP)}px)`,
            transition: "transform 0.3s ease",
          }}>
            {visible.map((r) => {
              const isSelected = selectedIds.has(r.id)
              return (
                <Card key={r.id} elevation={0} sx={{
                  minWidth: CARD_WIDTH, maxWidth: CARD_WIDTH, flexShrink: 0,
                  border: 1, borderColor: isSelected ? "primary.main" : "rgba(0,0,0,0.08)",
                  bgcolor: isSelected ? "rgba(201,100,66,0.04)" : "background.paper",
                  cursor: "pointer", transition: "border-color 0.2s",
                  "&:hover": { borderColor: "text.disabled" },
                  position: "relative", borderRadius: 2,
                }}>
                  <Checkbox
                    checked={isSelected}
                    onChange={(e) => toggleSelect(r.id, e)}
                    onClick={(e) => e.stopPropagation()}
                    size="small"
                    sx={{ position: "absolute", top: 2, left: 2, zIndex: 2, bgcolor: "background.paper", borderRadius: 0.5, p: 0.25 }}
                  />
                  {r.thumbnail_url ? (
                    <CardMedia component="img" height={110} image={r.thumbnail_url} alt={r.title}
                      sx={{ objectFit: "cover" }} />
                  ) : (
                    <Box sx={{ height: 110, bgcolor: "action.hover", display: "flex", alignItems: "center", justifyContent: "center" }}>
                      <Typography variant="caption" sx={{ color: "text.disabled" }}>No thumbnail</Typography>
                    </Box>
                  )}
                  <CardContent sx={{ p: 1, "&:last-child": { pb: 1 } }}>
                    <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 0.25 }}>
                      <Chip label={r.platform} size="small" variant="outlined"
                        sx={{ fontSize: "0.55rem", height: 18, textTransform: "uppercase" }} />
                      <Stack direction="row" alignItems="center" spacing={0.25}>
                        <ViralityChip score={r.virality_score || 0} />
                        {r.video_url && (
                          <IconButton
                            size="small"
                            onClick={(e) => { e.stopPropagation(); window.open(r.video_url, "_blank", "noopener") }}
                            sx={{ p: 0.25, color: "text.secondary", "&:hover": { color: "primary.main" } }}
                          >
                            <LaunchIcon sx={{ fontSize: "0.85rem" }} />
                          </IconButton>
                        )}
                      </Stack>
                    </Stack>
                    <Typography variant="caption" sx={{
                      fontWeight: 500, display: "-webkit-box", color: "text.primary",
                      WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                      overflow: "hidden", lineHeight: 1.3, fontSize: "0.75rem",
                    }}>
                      {r.title}
                    </Typography>
                    <Typography variant="caption" sx={{ color: "text.secondary", fontSize: "0.65rem" }}>
                      <VisibilityIcon sx={{ fontSize: 11, verticalAlign: "middle", mr: 0.3 }} />{formatViews(r.views)}
                      {r.upload_date && ` \u00B7 Uploaded: ${new Date(r.upload_date).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`}
                    </Typography>
                  </CardContent>
                </Card>
              )
            })}
          </Stack>
        </Box>
      </Box>

      {/* Actions */}
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1.5 }}>
        {selectedIds.size > 0 && (
          <Button size="small" variant="contained" startIcon={<DownloadIcon />}
            onClick={handleDownload} disabled={downloading}>
            {downloading ? "Starting..." : `Download & Analyze (${selectedIds.size})`}
          </Button>
        )}
        {!expanded && sorted.length > 8 && (
          <Button size="small" variant="text" sx={{ color: "text.secondary" }} onClick={() => setExpanded(true)}>
            Show all {sorted.length}
          </Button>
        )}
        <Box sx={{ flex: 1 }} />
        <Button size="small" variant="text" startIcon={<LaunchIcon />}
          onClick={() => navigate("/videos")} sx={{ color: "text.secondary" }}>
          Library
        </Button>
      </Stack>
    </Box>
  )
}
