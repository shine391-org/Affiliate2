import { useState } from "react"
import {
  Box, Typography, Stack, Slider, TextField,
} from "@mui/material"
import GifOutlinedIcon from "@mui/icons-material/GifOutlined"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"

// Animated GIF export. Two-pass palette generation is server-side; the
// UI only exposes the four knobs that actually move file size + quality:
// fps, width, and optional trim window.

export default function ToolGif() {
  useDocumentTitle("Video → GIF")
  const [fps, setFps] = useState(15)
  const [width, setWidth] = useState(540)
  const [startSeconds, setStartSeconds] = useState(0)
  const [durationSeconds, setDurationSeconds] = useState(0)

  return (
    <ToolRunner
      title="Video → GIF"
      description="Convert any video clip into an animated GIF with optimal palette"
      icon={<GifOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/gif"
      processLabel="Make GIF"
      downloadLabel="Download GIF"
      fieldBuilder={() => ({
        fps, width,
        start_seconds: startSeconds,
        duration_seconds: durationSeconds,
      })}
    >
      <Stack spacing={2}>
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
            Frames per second — {fps}
          </Typography>
          <Slider
            value={fps}
            min={5} max={30} step={1}
            marks={[
              { value: 5, label: "5" }, { value: 15, label: "15" }, { value: 30, label: "30" },
            ]}
            onChange={(_, v) => setFps(v)}
            valueLabelDisplay="auto"
            size="small"
          />
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            Higher fps = smoother motion + much bigger files. 15 is the sweet spot for most clips.
          </Typography>
        </Box>

        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
            Width — {width}px
          </Typography>
          <Slider
            value={width}
            min={240} max={1080} step={20}
            marks={[
              { value: 240, label: "240" }, { value: 540, label: "540" }, { value: 1080, label: "1080" },
            ]}
            onChange={(_, v) => setWidth(v)}
            valueLabelDisplay="auto"
            size="small"
          />
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            Height scales automatically to preserve aspect ratio.
          </Typography>
        </Box>

        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
            Trim (optional — 0 means whole video, max 60s)
          </Typography>
          <Stack direction="row" spacing={2}>
            <TextField
              label="Start (sec)" type="number" size="small" sx={{ width: 130 }}
              value={startSeconds}
              onChange={(e) => setStartSeconds(Math.max(0, parseFloat(e.target.value) || 0))}
              slotProps={{ htmlInput: { min: 0, step: 0.1 } }}
            />
            <TextField
              label="Duration (sec)" type="number" size="small" sx={{ width: 150 }}
              value={durationSeconds}
              onChange={(e) => setDurationSeconds(Math.max(0, Math.min(60, parseFloat(e.target.value) || 0)))}
              slotProps={{ htmlInput: { min: 0, max: 60, step: 0.5 } }}
            />
          </Stack>
        </Box>
      </Stack>
    </ToolRunner>
  )
}
