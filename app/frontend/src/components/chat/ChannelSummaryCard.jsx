import { useState } from "react"
import {
  Box, Typography, Stack, Chip, Button, Divider, Avatar, Checkbox,
  Collapse, alpha, useTheme,
} from "@mui/material"
import DownloadIcon from "@mui/icons-material/DownloadOutlined"
import VisibilityIcon from "@mui/icons-material/VisibilityOutlined"
import CalendarTodayIcon from "@mui/icons-material/CalendarTodayOutlined"
import PeopleIcon from "@mui/icons-material/PeopleOutline"
import ExpandMoreIcon from "@mui/icons-material/ExpandMore"
import ExpandLessIcon from "@mui/icons-material/ExpandLess"
import InsightsIcon from "@mui/icons-material/InsightsOutlined"
import ReactMarkdown from "react-markdown"
import { ws } from "../../api/websocket"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

function formatCount(n) {
  if (!n) return "N/A"
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatDuration(seconds) {
  if (!seconds) return ""
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${String(s).padStart(2, "0")}`
}

function OutlierBadge({ score }) {
  if (!score || score < 3) return null
  let color, label
  if (score >= 20) { color = "error"; label = `${score}x` }
  else if (score >= 10) { color = "error"; label = `${score}x` }
  else if (score >= 5) { color = "warning"; label = `${score}x` }
  else { color = "info"; label = `${score}x` }
  return <Chip label={label} size="small" color={color} variant="outlined" sx={{ fontWeight: 700, fontSize: "0.6rem", height: 20 }} />
}

export default function ChannelSummaryCard({ summary }) {
  const [selected, setSelected] = useState(new Set())
  const [downloading, setDownloading] = useState(false)
  const [videosExpanded, setVideosExpanded] = useState(false)
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const theme = useTheme()

  if (!summary) return null

  const {
    channel_title, channel_description, subscriber_count,
    videos = [], channel_url, ai_analysis,
  } = summary

  const handleDownloadTop = (count = 5) => {
    ws.send({
      type: "chat_message",
      content: `Download and analyze the top ${count} videos from this channel: ${channel_url}`,
    })
  }

  const handleToggle = (index) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  const handleSelectAll = () => {
    if (selected.size === videos.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(videos.map((_, i) => i)))
    }
  }

  const handleDownloadSelected = async () => {
    const urls = [...selected].map((i) => ({
      url: videos[i]?.url || "",
      title: videos[i]?.title || "",
    })).filter((u) => u.url)

    if (urls.length === 0) return

    setDownloading(true)
    try {
      await http.post("/api/downloaded/batch-download", { urls })
      showSnackbar(`Downloading ${urls.length} videos... (Job started)`, "info")
    } catch (e) {
      showSnackbar(`Download failed: ${e.response?.data?.detail || e.message}`, "error")
    } finally {
      setDownloading(false)
    }
  }

  const hasSelection = selected.size > 0

  return (
    <Box sx={{
      border: 1, borderColor: "divider", borderRadius: 3, overflow: "hidden", bgcolor: "background.paper",
      boxShadow: (theme) => theme.customShadows?.sm,
      transition: "all 0.2s ease",
    }}>
      {/* Channel header */}
      <Box sx={{ p: 2, bgcolor: "action.hover" }}>
        <Stack direction="row" spacing={2} alignItems="center">
          {summary.thumbnail && (
            <Avatar src={summary.thumbnail} sx={{ width: 48, height: 48 }} />
          )}
          <Box sx={{ flex: 1 }}>
            <Typography variant="subtitle1" fontWeight={700}>{channel_title}</Typography>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
              {subscriber_count > 0 && (
                <Chip
                  icon={<PeopleIcon sx={{ fontSize: 14 }} />}
                  label={`${formatCount(subscriber_count)} followers`}
                  size="small"
                  variant="outlined"
                />
              )}
              <Chip label={`${videos.length} videos listed`} size="small" variant="outlined" />
              {summary.median_views > 0 && (
                <Chip label={`Median: ${formatCount(summary.median_views)} views`} size="small" variant="outlined" color="info" />
              )}
            </Stack>
          </Box>
        </Stack>
        {channel_description && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1, lineHeight: 1.4 }}>
            {channel_description.slice(0, 200)}{channel_description.length > 200 ? "..." : ""}
          </Typography>
        )}
      </Box>

      {/* AI Analysis section */}
      {ai_analysis && (
        <>
          <Divider />
          <Box sx={{
            px: 2.5, py: 2,
            background: `linear-gradient(135deg, ${alpha(theme.palette.primary.main, 0.04)}, ${alpha(theme.palette.primary.main, 0.01)})`,
          }}>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
              <InsightsIcon sx={{ fontSize: 18, color: "primary.main" }} />
              <Typography variant="subtitle2" sx={{ fontWeight: 700, color: "primary.main", fontSize: "0.85rem" }}>
                AI Strategic Analysis
              </Typography>
            </Stack>
            <Box sx={{
              "& h2": {
                fontSize: "0.9rem", fontWeight: 700, mt: 2, mb: 0.75,
                color: "text.primary",
                "&:first-of-type": { mt: 0 },
              },
              "& p": {
                fontSize: "0.82rem", lineHeight: 1.65, color: "text.secondary",
                my: 0.5,
              },
              "& ul, & ol": {
                pl: 2.5, my: 0.5,
                "& li": {
                  fontSize: "0.82rem", lineHeight: 1.6, color: "text.secondary",
                  mb: 0.5, pl: 0.5,
                },
              },
              "& table": {
                width: "100%", borderCollapse: "collapse", my: 1,
                fontSize: "0.8rem",
                "& th": {
                  textAlign: "left", fontWeight: 600, color: "text.primary",
                  py: 0.75, px: 1.5, borderBottom: 2, borderColor: "divider",
                  fontSize: "0.78rem",
                },
                "& td": {
                  py: 0.75, px: 1.5, borderBottom: 1, borderColor: "divider",
                  color: "text.secondary", fontSize: "0.8rem",
                },
              },
              "& strong": { color: "text.primary", fontWeight: 600 },
              "& code": {
                bgcolor: alpha(theme.palette.text.primary, 0.06),
                px: 0.5, py: 0.15, borderRadius: 0.5,
                fontSize: "0.78rem",
              },
            }}>
              <ReactMarkdown>{ai_analysis}</ReactMarkdown>
            </Box>
          </Box>
        </>
      )}

      <Divider />

      {/* Video list toggle */}
      <Box
        onClick={() => setVideosExpanded(!videosExpanded)}
        sx={{
          px: 2, py: 1.25,
          display: "flex", alignItems: "center", justifyContent: "space-between",
          cursor: "pointer",
          bgcolor: "action.hover",
          "&:hover": { bgcolor: alpha(theme.palette.text.primary, 0.06) },
          transition: "background 0.15s",
        }}
      >
        <Typography variant="body2" sx={{ fontWeight: 600, fontSize: "0.85rem" }}>
          Videos ({videos.length})
        </Typography>
        {videosExpanded ? <ExpandLessIcon sx={{ fontSize: 20 }} /> : <ExpandMoreIcon sx={{ fontSize: 20 }} />}
      </Box>

      <Collapse in={videosExpanded}>
        {/* Select all bar */}
        <Box sx={{ px: 2, py: 0.5, display: "flex", alignItems: "center", borderTop: 1, borderColor: "divider" }}>
          <Checkbox
            size="small"
            checked={selected.size === videos.length && videos.length > 0}
            indeterminate={selected.size > 0 && selected.size < videos.length}
            onChange={handleSelectAll}
          />
          <Typography variant="caption" color="text.secondary">
            {hasSelection ? `${selected.size} selected` : "Select videos to download"}
          </Typography>
        </Box>

        <Divider />

        {/* Video list */}
        <Box sx={{ maxHeight: 320, overflowY: "auto" }}>
          {videos.map((v, i) => (
            <Box
              key={v.video_id || i}
              sx={{
                px: 1, py: 0.5,
                display: "flex", alignItems: "center", gap: 1,
                borderBottom: 1, borderColor: "divider",
                "&:last-child": { borderBottom: 0 },
                "&:hover": { bgcolor: "action.hover" },
                cursor: "pointer",
              }}
              onClick={() => handleToggle(i)}
            >
              <Checkbox
                size="small"
                checked={selected.has(i)}
                onChange={() => handleToggle(i)}
                onClick={(e) => e.stopPropagation()}
              />
              <Typography variant="body2" color="text.secondary" sx={{ minWidth: 20, textAlign: "right" }}>
                {i + 1}.
              </Typography>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="body2" noWrap fontWeight={500}>
                  {v.title || "Untitled"}
                </Typography>
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                  {v.view_count != null && (
                    <Typography variant="caption" color="text.secondary">
                      <VisibilityIcon sx={{ fontSize: 11, mr: 0.3, verticalAlign: "middle" }} />
                      {formatCount(v.view_count)}
                    </Typography>
                  )}
                  <OutlierBadge score={v.outlier_score} />
                  {v.duration != null && (
                    <Typography variant="caption" color="text.secondary">
                      {formatDuration(v.duration)}
                    </Typography>
                  )}
                  {v.upload_date && (
                    <Typography variant="caption" color="text.secondary">
                      <CalendarTodayIcon sx={{ fontSize: 11, mr: 0.3, verticalAlign: "middle" }} />
                      {v.upload_date}
                    </Typography>
                  )}
                </Stack>
              </Box>
            </Box>
          ))}
        </Box>
      </Collapse>

      <Divider />

      {/* Action buttons */}
      <Box sx={{ p: 1.5, display: "flex", gap: 1, justifyContent: "flex-end" }}>
        {hasSelection ? (
          <Button
            size="small"
            variant="contained"
            startIcon={<DownloadIcon />}
            onClick={handleDownloadSelected}
            disabled={downloading}
          >
            {downloading ? "Starting..." : `Download ${selected.size} Selected & Analyze`}
          </Button>
        ) : (
          <>
            <Button size="small" variant="outlined" startIcon={<DownloadIcon />} onClick={() => handleDownloadTop(3)}>
              Download Top 3
            </Button>
            <Button size="small" variant="contained" startIcon={<DownloadIcon />} onClick={() => handleDownloadTop(5)}>
              Download Top 5 & Analyze
            </Button>
          </>
        )}
      </Box>
    </Box>
  )
}
