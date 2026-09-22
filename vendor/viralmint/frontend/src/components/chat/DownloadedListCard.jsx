import { Box, Typography, Stack, Paper, Chip, Button, IconButton, Tooltip } from "@mui/material"
import MovieCreationIcon from "@mui/icons-material/MovieCreationOutlined"
import OpenInNewIcon from "@mui/icons-material/OpenInNew"
import LightbulbIcon from "@mui/icons-material/LightbulbOutlined"
import VisibilityIcon from "@mui/icons-material/VisibilityOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"
import { useNavigate } from "react-router-dom"

function formatCount(n) {
  if (!n) return ""
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatDuration(sec) {
  if (!sec) return ""
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}:${String(s).padStart(2, "0")}`
}

export default function DownloadedListCard({ videos }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const navigate = useNavigate()

  if (!videos || videos.length === 0) return null

  const handleGenerate = async (id) => {
    try {
      const { data } = await http.post(`/api/downloaded/${id}/generate`)
      showSnackbar("Video generation started!", "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || err.message, "error")
    }
  }

  return (
    <Paper variant="outlined" sx={{ borderRadius: 2.5, overflow: "hidden", borderColor: "divider" }}>
      <Box sx={{ px: 2, py: 1.25, bgcolor: "action.hover", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography variant="subtitle2" sx={{ fontWeight: 700, fontSize: "0.85rem" }}>
            Downloaded Videos
          </Typography>
          <Chip label={`${videos.length}`} size="small" color="primary" variant="outlined"
            sx={{ fontSize: "0.65rem", height: 20 }} />
        </Stack>
        <Button size="small" variant="text" onClick={() => navigate("/videos")}
          endIcon={<OpenInNewIcon sx={{ fontSize: 14 }} />}>
          View all
        </Button>
      </Box>

      <Stack sx={{ maxHeight: 400, overflowY: "auto", p: 1, gap: 0.75 }}>
        {videos.map((v) => (
          <Paper key={v.id} variant="outlined" sx={{
            display: "flex", alignItems: "center", gap: 1.5, p: 1, borderRadius: 1.5,
            borderColor: "divider",
            "&:hover": { bgcolor: "action.hover", borderColor: "primary.light" },
            transition: "all 0.15s",
          }}>
            {/* Thumbnail */}
            {v.thumbnail_url ? (
              <Box
                component="img" src={v.thumbnail_url} alt=""
                sx={{ width: 72, height: 42, borderRadius: 1, objectFit: "cover", flexShrink: 0, bgcolor: "grey.200" }}
                onError={(e) => { e.target.style.display = "none" }}
              />
            ) : (
              <Box sx={{ width: 72, height: 42, borderRadius: 1, bgcolor: "grey.200", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
                <MovieCreationIcon sx={{ fontSize: 20, color: "text.disabled" }} />
              </Box>
            )}

            {/* Info */}
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="body2" sx={{
                fontWeight: 600, fontSize: "0.8rem", lineHeight: 1.3,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {v.title}
              </Typography>
              <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mt: 0.25 }}>
                {v.platform && (
                  <Chip label={v.platform} size="small" variant="outlined"
                    sx={{ fontSize: "0.55rem", height: 16, textTransform: "uppercase" }} />
                )}
                {v.views > 0 && (
                  <Typography variant="caption" sx={{ color: "text.secondary", fontSize: "0.68rem" }}>
                    <VisibilityIcon sx={{ fontSize: 11, verticalAlign: "middle", mr: 0.3 }} />{formatCount(v.views)}
                  </Typography>
                )}
                {v.duration_seconds > 0 && (
                  <Typography variant="caption" sx={{ color: "text.secondary", fontSize: "0.68rem" }}>
                    {formatDuration(v.duration_seconds)}
                  </Typography>
                )}
                {v.has_insights && (
                  <Chip icon={<LightbulbIcon sx={{ fontSize: 10 }} />} label="analyzed" size="small" color="success" variant="outlined"
                    sx={{ fontSize: "0.55rem", height: 16, "& .MuiChip-icon": { ml: 0.3 } }} />
                )}
              </Stack>
              {v.suggested_angle && (
                <Typography variant="caption" sx={{ color: "text.secondary", fontSize: "0.65rem", fontStyle: "italic", mt: 0.25, display: "block" }}>
                  Angle: {v.suggested_angle}
                </Typography>
              )}
            </Box>

            {/* Generate button */}
            <Tooltip title="Generate video from this" arrow>
              <IconButton size="small" onClick={() => handleGenerate(v.id)}
                sx={{ color: "primary.main", "&:hover": { bgcolor: "action.hover" } }}>
                <MovieCreationIcon sx={{ fontSize: "1.1rem" }} />
              </IconButton>
            </Tooltip>
          </Paper>
        ))}
      </Stack>
    </Paper>
  )
}
