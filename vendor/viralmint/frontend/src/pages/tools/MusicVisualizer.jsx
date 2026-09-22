import { useState } from "react"
import { Box, Typography, ToggleButtonGroup, ToggleButton, Stack } from "@mui/material"
import GraphicEqIcon from "@mui/icons-material/GraphicEq"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"

const STYLES = [
  { v: "waves", label: "Waveform" },
  { v: "bars", label: "Musical bars" },
  { v: "spectrum", label: "Spectrum" },
]
const PALETTES = [
  { v: "sunset", label: "Sunset", c: "#FFD93D", bg: "#1A0E2E" },
  { v: "ocean", label: "Ocean", c: "#4FC3F7", bg: "#081428" },
  { v: "neon", label: "Neon", c: "#FF2ED1", bg: "#0A0A1E" },
  { v: "mono", label: "Mono", c: "#FFFFFF", bg: "#111111" },
]
const ASPECTS = ["9:16", "1:1", "16:9"]

export default function ToolMusicVisualizer() {
  useDocumentTitle("Music Visualizer")
  const [style, setStyle] = useState("waves")
  const [palette, setPalette] = useState("sunset")
  const [aspect, setAspect] = useState("9:16")

  return (
    <ToolRunner
      title="Music Visualizer"
      description="Turn an audio file into an animated visualizer video synced to the sound"
      icon={<GraphicEqIcon fontSize="large" />}
      endpoint="/api/tools/music-visualizer"
      acceptExts=".mp3,.wav,.m4a,.aac,.ogg,.flac"
      fieldBuilder={() => ({ style, palette, aspect })}
    >
      <Stack spacing={2}>
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 1 }}>Style</Typography>
          <ToggleButtonGroup exclusive size="small" value={style} onChange={(_e, v) => v && setStyle(v)}>
            {STYLES.map((s) => <ToggleButton key={s.v} value={s.v} sx={{ textTransform: "none" }}>{s.label}</ToggleButton>)}
          </ToggleButtonGroup>
        </Box>

        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 1 }}>
            Palette {style !== "waves" && <Typography component="span" variant="caption" sx={{ color: "text.secondary" }}>(applies to Waveform)</Typography>}
          </Typography>
          <ToggleButtonGroup exclusive size="small" value={palette} onChange={(_e, v) => v && setPalette(v)}>
            {PALETTES.map((p) => (
              <ToggleButton key={p.v} value={p.v} sx={{ gap: 0.75, textTransform: "none" }}>
                <Box sx={{ width: 12, height: 12, borderRadius: "50%", background: `linear-gradient(135deg, ${p.c}, ${p.bg})`, border: "1px solid rgba(255,255,255,0.2)" }} />
                {p.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Box>

        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 1 }}>Aspect ratio</Typography>
          <ToggleButtonGroup exclusive size="small" value={aspect} onChange={(_e, v) => v && setAspect(v)}>
            {ASPECTS.map((a) => <ToggleButton key={a} value={a}>{a}</ToggleButton>)}
          </ToggleButtonGroup>
        </Box>
      </Stack>
    </ToolRunner>
  )
}
