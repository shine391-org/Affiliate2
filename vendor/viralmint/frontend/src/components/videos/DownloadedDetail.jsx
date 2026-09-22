import { useState, useEffect, useRef } from "react"
import { NavLink } from "react-router-dom"
import {
  Box, Typography, Button, Chip, Stack, Paper, IconButton, Divider,
  Grid, CircularProgress, Tooltip, Menu, MenuItem, ListItemText,
} from "@mui/material"
import AutoFixHighIcon from "@mui/icons-material/AutoFixHigh"
import EditIcon from "@mui/icons-material/Edit"
import MovieCreationIcon from "@mui/icons-material/MovieCreation"
import CloseIcon from "@mui/icons-material/Close"
import AccessTimeIcon from "@mui/icons-material/AccessTime"
import StorageIcon from "@mui/icons-material/Storage"
import TravelExploreIcon from "@mui/icons-material/TravelExplore"
import AspectRatioIcon from "@mui/icons-material/AspectRatio"
import PlayCircleOutlineIcon from "@mui/icons-material/PlayCircleOutline"
import ContentCutIcon from "@mui/icons-material/ContentCut"
import WhatshotIcon from "@mui/icons-material/Whatshot"
import LinkIcon from "@mui/icons-material/Link"
import NewspaperIcon from "@mui/icons-material/Newspaper"
import http from "../../api/http"
import useAppStore from "../../store/appStore"
import { WHISPER_QUALITIES } from "./constants"

function InsightRow({ label, color, value }) {
  return (
    <Box>
      <Typography variant="caption" sx={{ color, fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5 }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ fontSize: "0.85rem", lineHeight: 1.5 }}>
        {value}
      </Typography>
    </Box>
  )
}

function AIActionButton({ label, icon, loading, onClick, tooltip }) {
  return (
    <Tooltip title={tooltip || label} placement="top">
      <span>
        <Button
          size="small" variant="outlined"
          startIcon={loading ? <CircularProgress size={14} /> : icon}
          disabled={loading}
          onClick={onClick}
          sx={{
            borderRadius: 2, textTransform: "none", fontSize: "0.75rem",
            fontWeight: 600, py: 0.4, px: 1.5, minWidth: 0,
            borderColor: "divider", color: "text.secondary",
            "&:hover": { borderColor: "primary.main", color: "primary.main", bgcolor: "action.hover" },
          }}
        >
          {label}
        </Button>
      </span>
    </Tooltip>
  )
}

export default function DownloadedDetail({ video, onUseAsInspiration, onClose, onDetailUpdate }) {
  const [reanalyzeAnchor, setReanalyzeAnchor] = useState(null)
  const [reanalyzing, setReanalyzing] = useState(false)
  const [aiActionLoading, setAiActionLoading] = useState(null)
  const [aiActionResult, setAiActionResult] = useState(null)
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const pollRef = useRef(null)

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current.pollId)
        clearTimeout(pollRef.current.safetyTimeout)
      }
    }
  }, [])

  const handleAiAction = async (action, extra = {}) => {
    setAiActionLoading(action)
    setAiActionResult(null)
    try {
      const res = await http.post(`/api/downloaded/${video.id}/ai-action`, { action, ...extra })
      setAiActionResult({ action, ...res.data })
      showSnackbar(`AI action "${action}" complete`, "success")
    } catch (e) {
      showSnackbar(e.response?.data?.detail || `AI action failed: ${e.message}`, "error")
    } finally {
      setAiActionLoading(null)
    }
  }

  const handleReanalyze = async (quality) => {
    setReanalyzeAnchor(null)
    setReanalyzing(true)
    try {
      const res = await http.post(`/api/downloaded/${video.id}/reanalyze`, { whisper_quality: quality })
      const jobId = res.data.job_id

      // Poll job status until complete
      const pollId = setInterval(async () => {
        try {
          const jr = await http.get(`/api/jobs/${jobId}`)
          const { status, error_message } = jr.data
          if (status === "success") {
            clearInterval(pollId)
            clearTimeout(safetyTimeout)
            setReanalyzing(false)
            showSnackbar("Re-analysis complete!", "success")
            const detail = await http.get(`/api/downloaded/${video.id}`)
            if (onDetailUpdate) onDetailUpdate({ ...detail.data, _type: "downloaded" })
          } else if (status === "failed") {
            clearInterval(pollId)
            clearTimeout(safetyTimeout)
            setReanalyzing(false)
            showSnackbar(`Re-analysis failed: ${error_message || "unknown error"}`, "error")
          }
        } catch { /* keep polling */ }
      }, 3000)

      // Safety: stop polling after 10 minutes (large models take time)
      const safetyTimeout = setTimeout(() => { clearInterval(pollId); setReanalyzing(false) }, 600000)

      // Store cleanup refs so unmount can clear them
      pollRef.current = { pollId, safetyTimeout }
    } catch (e) {
      setReanalyzing(false)
      showSnackbar(`Re-analyze failed: ${e.response?.data?.detail || e.message}`, "error")
    }
  }

  return (
    <Paper
      elevation={0}
      sx={{
        mx: -0.5, mb: 1.5, p: 0,
        border: 2, borderColor: "secondary.main",
        borderRadius: 3,
        overflow: "hidden",
        bgcolor: "background.paper",
      }}
    >
      {/* Header bar */}
      <Stack direction="row" alignItems="center" justifyContent="space-between"
        sx={{ px: 2.5, py: 1.5, bgcolor: "action.hover" }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, flex: 1, minWidth: 0 }} noWrap>
          {video.title || "Untitled"}
        </Typography>
        <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
          {video.platform !== "news" && (
            <>
              <Button variant="outlined" size="small"
                startIcon={reanalyzing ? <CircularProgress size={16} /> : <AutoFixHighIcon />}
                disabled={reanalyzing}
                onClick={(e) => setReanalyzeAnchor(e.currentTarget)}>
                {reanalyzing ? "Re-analyzing..." : "Re-analyze"}
              </Button>
              <Menu anchorEl={reanalyzeAnchor} open={Boolean(reanalyzeAnchor)}
                onClose={() => setReanalyzeAnchor(null)}>
                {WHISPER_QUALITIES.map((q) => (
                  <MenuItem key={q.value} onClick={() => handleReanalyze(q.value)}>
                    <ListItemText primary={q.label} secondary={q.desc} />
                  </MenuItem>
                ))}
              </Menu>
              <Button variant="contained" color="success" size="small"
                startIcon={<AutoFixHighIcon />}
                onClick={async () => {
                  try {
                    await http.post(`/api/downloaded/${video.id}/generate`, {
                      aspect_ratio: "9:16", tts_provider: "edge_tts",
                      caption_enabled: true, caption_style: "viral", music_enabled: true, music_genre: "lofi",
                    })
                    showSnackbar("Quick video generation started!", "success")
                  } catch (e) { showSnackbar(e.response?.data?.detail || e.message, "error") }
                }}
                sx={{ fontWeight: 700 }}>
                Quick Stock Video
              </Button>
              <Button variant="outlined" size="small"
                startIcon={<ContentCutIcon />}
                component={NavLink} to="/clips">
                Clip Studio
              </Button>
            </>
          )}
          <Button variant="contained" startIcon={<MovieCreationIcon />}
            onClick={(e) => onUseAsInspiration(video.id, e)}>
            {video.platform === "news" ? "Create Video from Article" : "Use as Inspiration"}
          </Button>
        </Stack>
        <IconButton size="small" onClick={onClose} sx={{ ml: 1 }}><CloseIcon fontSize="small" /></IconButton>
      </Stack>

      {/* Main content */}
      <Box sx={{ p: 2.5 }}>
        <Grid container spacing={3}>
          {/* Left column: Video player + meta chips */}
          <Grid size={{ xs: 12, md: 4 }}>
            {video.video_path ? (
              <Box
                component="video"
                controls
                sx={{
                  width: "100%", borderRadius: 2,
                  bgcolor: "#000", display: "block", mx: "auto", objectFit: "contain",
                  maxHeight: 480,
                }}
                src={`/api/downloaded/${video.id}/stream`}
              />
            ) : video.platform === "news" ? (
              <Paper variant="outlined" sx={{ p: 2, borderRadius: 2, bgcolor: "action.hover" }}>
                <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
                  <NewspaperIcon sx={{ color: "warning.main" }} />
                  <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>News Article</Typography>
                  {video.source_url && (
                    <Chip label={(() => { try { return new URL(video.source_url).hostname.replace("www.", "") } catch { return "source" } })()}
                      size="small" variant="outlined" sx={{ fontSize: "0.65rem", height: 20 }} />
                  )}
                </Stack>
                {video.source_url && (
                  <Button size="small" variant="outlined" href={video.source_url} target="_blank" rel="noopener"
                    startIcon={<LinkIcon />} sx={{ textTransform: "none", fontSize: "0.78rem", mb: 1.5 }}>
                    Read original article
                  </Button>
                )}
                {/* Show AI analysis summary if available */}
                {video.insights && (
                  <Stack spacing={1} sx={{ mb: 1.5 }}>
                    {video.insights.why_trending && (
                      <Box sx={{ p: 1, borderRadius: 1, bgcolor: (t) => t.palette.mode === "dark" ? "rgba(255,152,0,0.08)" : "rgba(255,152,0,0.06)" }}>
                        <Typography variant="caption" sx={{ fontWeight: 700, color: "warning.main" }}>Why Trending</Typography>
                        <Typography variant="body2" sx={{ fontSize: "0.82rem" }}>{video.insights.why_trending}</Typography>
                      </Box>
                    )}
                    {video.insights.hook && (
                      <Box>
                        <Typography variant="caption" sx={{ fontWeight: 700, color: "text.secondary" }}>Video Hook</Typography>
                        <Typography variant="body2" sx={{ fontSize: "0.85rem", fontWeight: 500 }}>&ldquo;{video.insights.hook}&rdquo;</Typography>
                      </Box>
                    )}
                    {video.insights.suggested_angle && (
                      <Box>
                        <Typography variant="caption" sx={{ fontWeight: 700, color: "text.secondary" }}>Suggested Angle</Typography>
                        <Typography variant="body2" sx={{ fontSize: "0.82rem" }}>{video.insights.suggested_angle}</Typography>
                      </Box>
                    )}
                    {video.insights.talking_points?.length > 0 && (
                      <Box>
                        <Typography variant="caption" sx={{ fontWeight: 700, color: "text.secondary" }}>Talking Points</Typography>
                        {video.insights.talking_points.map((pt, i) => (
                          <Typography key={i} variant="body2" sx={{ fontSize: "0.8rem", pl: 1 }}>• {pt}</Typography>
                        ))}
                      </Box>
                    )}
                  </Stack>
                )}
                {video.transcript && (
                  <>
                    <Divider sx={{ my: 1 }} />
                    <Typography variant="caption" sx={{ fontWeight: 700, color: "text.secondary", display: "block", mb: 0.5 }}>Article Text</Typography>
                    <Typography variant="body2" sx={{ fontSize: "0.82rem", color: "text.secondary", lineHeight: 1.6, maxHeight: 250, overflowY: "auto" }}>
                      {video.transcript.slice(0, 2000)}{video.transcript.length > 2000 ? "..." : ""}
                    </Typography>
                  </>
                )}
              </Paper>
            ) : (
              <Box sx={{ height: 200, borderRadius: 2, bgcolor: "action.hover", display: "flex", alignItems: "center", justifyContent: "center" }}>
                <Typography sx={{ color: "text.disabled" }}>No video file</Typography>
              </Box>
            )}

            {/* Meta chips */}
            <Stack direction="row" spacing={1} sx={{ mt: 1.5 }} flexWrap="wrap" useFlexGap>
              {video.platform !== "news" && video.duration_seconds && (
                <Chip icon={<AccessTimeIcon />} label={`${Math.floor(video.duration_seconds / 60)}m${video.duration_seconds % 60}s`}
                  size="small" variant="outlined" />
              )}
              {video.platform !== "news" && video.file_size_mb && (
                <Chip icon={<StorageIcon />} label={`${video.file_size_mb} MB`} size="small" variant="outlined" />
              )}
              <Chip
                label={video.platform === "news"
                  ? (video.insights ? "Analyzed" : "Saved")
                  : (video.insights ? "Analyzed" : video.transcript ? "Transcribed" : "Downloaded")}
                size="small"
                color={video.insights ? "success" : video.transcript ? "warning" : "default"}
              />
              {video.created_at && (
                <Typography variant="caption" sx={{ color: "text.secondary", alignSelf: "center" }}>
                  {new Date(video.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                </Typography>
              )}
            </Stack>
          </Grid>

          {/* Right column: AI Insights + Transcript */}
          <Grid size={{ xs: 12, md: 8 }}>
            {video.insights ? (<>
              <Typography variant="overline" sx={{ color: "text.secondary", mb: 1, display: "block" }}>AI Insights</Typography>
              <Stack spacing={1.5}>
                {video.insights.hook && <InsightRow label="Hook" color="secondary.main" value={video.insights.hook} />}
                {video.insights.structure && <InsightRow label="Structure" color="secondary.main" value={video.insights.structure} />}
                {video.insights.tone && <InsightRow label="Tone" color="secondary.main" value={video.insights.tone} />}
                {video.insights.why_viral && <InsightRow label="Why viral" color="warning.main" value={video.insights.why_viral} />}
                {video.insights.suggested_angle && <InsightRow label="Your angle" color="primary.main" value={video.insights.suggested_angle} />}
                {video.insights.suggested_title && <InsightRow label="Title idea" color="primary.main" value={video.insights.suggested_title} />}
              </Stack>

              {/* ── AI Action Buttons (Notion-style inline refinement) ────── */}
              <Divider sx={{ my: 2 }} />
              <Typography variant="overline" sx={{ color: "text.secondary", mb: 1, display: "block" }}>
                Refine with AI
              </Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ gap: 0.75 }}>
                <AIActionButton
                  label="Stronger Hook"
                  icon={<WhatshotIcon sx={{ fontSize: 16 }} />}
                  loading={aiActionLoading === "strengthen_hook"}
                  onClick={() => handleAiAction("strengthen_hook")}
                  tooltip="Rewrite the hook to be more attention-grabbing"
                />
                <AIActionButton
                  label="Improve Angle"
                  icon={<AutoFixHighIcon sx={{ fontSize: 16 }} />}
                  loading={aiActionLoading === "improve_angle"}
                  onClick={() => handleAiAction("improve_angle")}
                  tooltip="Elaborate and strengthen the suggested video angle"
                />
                <AIActionButton
                  label="5 Title Ideas"
                  icon={<EditIcon sx={{ fontSize: 16 }} />}
                  loading={aiActionLoading === "suggest_titles"}
                  onClick={() => handleAiAction("suggest_titles")}
                  tooltip="Generate 5 click-worthy title alternatives"
                />
                <AIActionButton
                  label="Make Shorter"
                  icon={<ContentCutIcon sx={{ fontSize: 16 }} />}
                  loading={aiActionLoading === "rewrite_shorter"}
                  onClick={() => handleAiAction("rewrite_shorter")}
                  tooltip="Shorten the suggested angle to 1-2 punchy sentences"
                />
                <AIActionButton
                  label="For TikTok"
                  icon={<AspectRatioIcon sx={{ fontSize: 16 }} />}
                  loading={aiActionLoading === "rewrite_for_platform"}
                  onClick={() => handleAiAction("rewrite_for_platform", { platform: "TikTok" })}
                  tooltip="Adapt concept specifically for TikTok's format"
                />
                <AIActionButton
                  label="Translate"
                  icon={<TravelExploreIcon sx={{ fontSize: 16 }} />}
                  loading={aiActionLoading === "translate"}
                  onClick={() => handleAiAction("translate", { language: "Chinese" })}
                  tooltip="Translate all insights into Chinese"
                />
              </Stack>

              {/* ── AI Action Result ──────────────────────────── */}
              {aiActionResult && (
                <Paper variant="outlined" sx={{
                  mt: 2, p: 2, borderRadius: 2,
                  borderColor: "primary.main", borderWidth: 1,
                  bgcolor: (t) => t.palette.mode === "dark" ? "rgba(25,118,210,0.06)" : "rgba(25,118,210,0.04)",
                }}>
                  <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
                    <Typography variant="caption" sx={{ fontWeight: 700, color: "primary.main", textTransform: "uppercase", letterSpacing: 0.5 }}>
                      AI Result — {aiActionResult.action?.replace(/_/g, " ")}
                    </Typography>
                    <IconButton size="small" onClick={() => setAiActionResult(null)} sx={{ p: 0.25 }}>
                      <CloseIcon sx={{ fontSize: 14 }} />
                    </IconButton>
                  </Stack>
                  {aiActionResult.action === "suggest_titles" && Array.isArray(aiActionResult.result) ? (
                    <Stack spacing={0.75}>
                      {aiActionResult.result.map((title, i) => (
                        <Paper key={i} variant="outlined" sx={{ px: 1.5, py: 0.75, borderRadius: 1.5, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
                          onClick={() => { navigator.clipboard?.writeText(title); showSnackbar("Copied to clipboard", "success") }}>
                          <Typography variant="body2" sx={{ fontSize: "0.85rem" }}>
                            {i + 1}. {title}
                          </Typography>
                        </Paper>
                      ))}
                      <Typography variant="caption" sx={{ color: "text.disabled" }}>Click a title to copy</Typography>
                    </Stack>
                  ) : aiActionResult.action === "rewrite_for_platform" && typeof aiActionResult.result === "object" ? (
                    <Stack spacing={1}>
                      {aiActionResult.result.hook && <InsightRow label="Hook" color="secondary.main" value={aiActionResult.result.hook} />}
                      {aiActionResult.result.suggested_angle && <InsightRow label="Angle" color="primary.main" value={aiActionResult.result.suggested_angle} />}
                      {aiActionResult.result.suggested_title && <InsightRow label="Title" color="primary.main" value={aiActionResult.result.suggested_title} />}
                      {aiActionResult.result.format_tips && <InsightRow label="Format Tips" color="warning.main" value={aiActionResult.result.format_tips} />}
                    </Stack>
                  ) : aiActionResult.action === "translate" && typeof aiActionResult.result === "object" ? (
                    <Stack spacing={1}>
                      {Object.entries(aiActionResult.result).map(([key, val]) => (
                        <InsightRow key={key} label={key.replace(/_/g, " ")} color="text.secondary" value={typeof val === "string" ? val : JSON.stringify(val)} />
                      ))}
                    </Stack>
                  ) : (
                    <Typography variant="body2" sx={{ fontSize: "0.85rem", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                      {typeof aiActionResult.result === "string" ? aiActionResult.result : JSON.stringify(aiActionResult.result, null, 2)}
                    </Typography>
                  )}
                </Paper>
              )}
            </>) : !video.transcript && (
              <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", minHeight: 200, color: "text.disabled" }}>
                <Typography variant="body2" sx={{ mb: 1 }}>No analysis yet</Typography>
                <Typography variant="caption">Use the Re-analyze button to transcribe and extract insights</Typography>
              </Box>
            )}

            {video.transcript && (
              <Box sx={{ mt: video.insights ? 2.5 : 0 }}>
                <Typography variant="overline" sx={{ color: "text.secondary" }}>
                  Transcript {video.transcript_language && `(${video.transcript_language})`}
                </Typography>
                <Paper variant="outlined" sx={{ p: 1.5, maxHeight: 320, overflowY: "auto", fontSize: "0.82rem", color: "text.secondary", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                  {video.transcript}
                </Paper>
              </Box>
            )}
          </Grid>
        </Grid>
      </Box>

    </Paper>
  )
}
