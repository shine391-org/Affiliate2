import { Box, Typography, Card, CardContent, Button, Stack, Chip } from "@mui/material"
import PlayCircleIcon from "@mui/icons-material/PlayCircleOutline"
import YouTubeIcon from "@mui/icons-material/YouTube"
import UploadIcon from "@mui/icons-material/UploadOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

export default function VideoPreviewCard({ video }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  if (!video) return null

  const handleUpload = async (platforms) => {
    try {
      await http.post(`/api/videos/${video.id}/upload`, { platforms })
      showSnackbar(`Upload started for ${platforms.join(", ")}`, "success")
    } catch (e) {
      showSnackbar(e.response?.data?.detail || e.message, "error")
    }
  }

  return (
    <Card elevation={0} sx={{
      maxWidth: 340, mb: 0.5,
      border: 1, borderColor: "divider",
      borderRadius: 3,
    }}>
      <Box sx={{
        position: "relative", height: 170, bgcolor: "action.hover",
        display: "flex", alignItems: "center", justifyContent: "center",
        cursor: "pointer", overflow: "hidden",
      }}
        onClick={() => window.location.href = "/videos"}
      >
        {video.thumbnail_path ? (
          <Box component="img" src={`/api/videos/${video.id}/thumbnail`} alt={video.title}
            sx={{ width: "100%", height: "100%", objectFit: "cover" }} />
        ) : (
          <PlayCircleIcon sx={{ fontSize: 48, color: "text.disabled" }} />
        )}
        <Box sx={{
          position: "absolute", bottom: 0, left: 0, right: 0,
          background: "linear-gradient(transparent, rgba(0,0,0,0.6))",
          p: 1.5,
        }}>
          <Typography variant="body2" sx={{ color: "#fff", fontWeight: 500 }}>
            {video.title || "Untitled Video"}
          </Typography>
        </Box>
      </Box>

      <CardContent sx={{ p: 1.5, "&:last-child": { pb: 1.5 } }}>
        <Stack direction="row" spacing={0.5} sx={{ mb: 1 }}>
          <Chip label={video.status || "ready"} size="small" color="success" sx={{ height: 20, fontSize: "0.65rem" }} />
          <Chip label={video.source_type === "clip_extraction" ? "Clip" : "Stock"} size="small" variant="outlined" sx={{ height: 20, fontSize: "0.65rem" }} />
          <Chip label={video.aspect_ratio || "9:16"} size="small" variant="outlined" sx={{ height: 20, fontSize: "0.65rem" }} />
        </Stack>

        <Stack direction="row" spacing={1}>
          <Button size="small" variant="contained" color="error" startIcon={<YouTubeIcon />}
            onClick={() => handleUpload(["youtube"])}>
            YouTube
          </Button>
          <Button size="small" variant="contained" color="info" startIcon={<UploadIcon />}
            onClick={() => handleUpload(["tiktok"])}>
            TikTok
          </Button>
          <Button size="small" variant="outlined"
            onClick={() => window.location.href = "/videos"}>
            View
          </Button>
        </Stack>
      </CardContent>
    </Card>
  )
}
