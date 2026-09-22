import { Box, Typography, IconButton } from "@mui/material"
import OpenInNewIcon from "@mui/icons-material/OpenInNew"
import PlayArrowIcon from "@mui/icons-material/PlayArrow"

export default function VideoEmbed({ platform, videoId, embedUrl, videoUrl }) {
  if (platform === "youtube" && videoId) {
    const watchUrl = `https://www.youtube.com/watch?v=${videoId}`
    const thumbUrl = `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`

    return (
      <Box
        sx={{
          position: "relative",
          width: "100%",
          height: 200,
          borderRadius: 1.5,
          overflow: "hidden",
          cursor: "pointer",
          bgcolor: "black",
          "&:hover .play-overlay": { opacity: 1 },
        }}
        onClick={() => window.open(watchUrl, "_blank")}
      >
        <img
          src={thumbUrl}
          alt="Video thumbnail"
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
        <Box
          className="play-overlay"
          sx={{
            position: "absolute", inset: 0,
            display: "flex", alignItems: "center", justifyContent: "center",
            bgcolor: "rgba(0,0,0,0.4)", opacity: 0, transition: "opacity 0.2s",
          }}
        >
          <PlayArrowIcon sx={{ fontSize: 56, color: "#fff" }} />
        </Box>
        <IconButton
          size="small"
          sx={{
            position: "absolute", top: 6, right: 6,
            bgcolor: "rgba(0,0,0,0.6)", color: "#fff",
            "&:hover": { bgcolor: "rgba(0,0,0,0.8)" },
          }}
          onClick={(e) => { e.stopPropagation(); window.open(watchUrl, "_blank") }}
        >
          <OpenInNewIcon sx={{ fontSize: 16 }} />
        </IconButton>
      </Box>
    )
  }

  if (platform === "tiktok" && videoId) {
    const watchUrl = videoUrl || `https://www.tiktok.com/video/${videoId}`

    return (
      <Box
        sx={{
          position: "relative",
          width: "100%",
          height: 200,
          borderRadius: 1.5,
          overflow: "hidden",
          cursor: "pointer",
          bgcolor: "#000",
          display: "flex", alignItems: "center", justifyContent: "center",
          "&:hover .play-overlay": { opacity: 1 },
        }}
        onClick={() => window.open(watchUrl, "_blank")}
      >
        <Typography variant="caption" sx={{ color: "grey.500" }}>TikTok Video</Typography>
        <Box
          className="play-overlay"
          sx={{
            position: "absolute", inset: 0,
            display: "flex", alignItems: "center", justifyContent: "center",
            bgcolor: "rgba(0,0,0,0.4)", opacity: 0, transition: "opacity 0.2s",
          }}
        >
          <PlayArrowIcon sx={{ fontSize: 56, color: "#fff" }} />
        </Box>
        <IconButton
          size="small"
          sx={{
            position: "absolute", top: 6, right: 6,
            bgcolor: "rgba(0,0,0,0.6)", color: "#fff",
            "&:hover": { bgcolor: "rgba(0,0,0,0.8)" },
          }}
          onClick={(e) => { e.stopPropagation(); window.open(watchUrl, "_blank") }}
        >
          <OpenInNewIcon sx={{ fontSize: 16 }} />
        </IconButton>
      </Box>
    )
  }

  return null
}
