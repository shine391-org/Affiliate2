import { Box, Typography } from "@mui/material"
import RadarIcon from "@mui/icons-material/Radar"
import DownloadIcon from "@mui/icons-material/Download"
import MovieIcon from "@mui/icons-material/Movie"
import UploadIcon from "@mui/icons-material/Upload"

const actionConfig = {
  start_scout: { icon: <RadarIcon fontSize="small" />, label: "Scouting" },
  start_download: { icon: <DownloadIcon fontSize="small" />, label: "Downloading" },
  start_generate: { icon: <MovieIcon fontSize="small" />, label: "Generating" },
  start_upload: { icon: <UploadIcon fontSize="small" />, label: "Uploading" },
}

export default function ActionBlock({ action }) {
  if (!action || !action.type) return null
  const config = actionConfig[action.type] || { icon: <RadarIcon fontSize="small" />, label: action.type }

  return (
    <Box sx={{
      display: "flex", alignItems: "center", gap: 1,
      px: 1.5, py: 0.75,
      bgcolor: "rgba(201,100,66,0.06)",
      border: 1, borderColor: "rgba(201,100,66,0.2)",
      borderRadius: 1.5, mt: 0.5,
      color: "primary.main", fontSize: "0.85rem",
    }}>
      {config.icon}
      <Typography variant="body2" sx={{ color: "primary.main", fontWeight: 500 }}>{config.label}</Typography>
      {action.niche && <Typography variant="body2" sx={{ color: "text.secondary" }}>-- {action.niche}</Typography>}
    </Box>
  )
}
