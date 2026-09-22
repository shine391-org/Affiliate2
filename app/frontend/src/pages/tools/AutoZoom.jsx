import { useState } from "react"
import { Box, Typography, Stack, Slider, Alert } from "@mui/material"
import ZoomInMapOutlinedIcon from "@mui/icons-material/ZoomInMapOutlined"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"

// Auto-Zoom / Punch-in — subtle zoom pulses on spoken words for energy.
// Whisper word timings drive the pulses; the shared apply_auto_zoom FFmpeg
// helper does the work. Works standalone; pairs well right after Captions.

const INTENSITY = [
  { v: 1.08, label: "Subtle" },
  { v: 1.15, label: "Default" },
  { v: 1.25, label: "Punchy" },
  { v: 1.4, label: "Max" },
]

export default function ToolAutoZoom() {
  useDocumentTitle("Auto-Zoom")
  const [zoom, setZoom] = useState(1.15)
  const [wordsPerGroup, setWordsPerGroup] = useState(3)

  return (
    <ToolRunner
      title="Auto-Zoom"
      description="Add subtle zoom punch-ins on spoken words for extra energy"
      icon={<ZoomInMapOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/auto-zoom"
      processLabel="Add zoom pulses"
      fieldBuilder={() => ({ zoom_factor: zoom, words_per_group: wordsPerGroup })}
    >
      <Stack spacing={2.5}>
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
            Zoom intensity — <strong>{Math.round((zoom - 1) * 100)}%</strong>
          </Typography>
          <Slider
            value={zoom}
            min={1.05} max={1.4} step={0.01}
            marks={INTENSITY.map((i) => ({ value: i.v, label: i.label }))}
            onChange={(_, v) => setZoom(v)}
            valueLabelDisplay="auto"
            valueLabelFormat={(v) => `${Math.round((v - 1) * 100)}%`}
            size="small"
          />
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            How far each pulse zooms in. 15% is the sweet spot — keep it subtle so it reads as energy, not motion sickness.
          </Typography>
        </Box>

        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
            Words per pulse — <strong>{wordsPerGroup}</strong>
          </Typography>
          <Slider
            value={wordsPerGroup}
            min={1} max={6} step={1}
            marks={[{ value: 1, label: "1" }, { value: 3, label: "3" }, { value: 6, label: "6" }]}
            onChange={(_, v) => setWordsPerGroup(v)}
            valueLabelDisplay="auto"
            size="small"
          />
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            Pulse once per N spoken words. Match this to your caption grouping for a synced feel.
          </Typography>
        </Box>

        <Alert severity="info" sx={{ fontSize: "0.82rem" }}>
          Needs clear speech (word timings drive the pulses). A clip with no
          detectable speech is returned unchanged. Run this{" "}
          <strong>after</strong> Add Captions for a synced caption + zoom combo.
        </Alert>
      </Stack>
    </ToolRunner>
  )
}
