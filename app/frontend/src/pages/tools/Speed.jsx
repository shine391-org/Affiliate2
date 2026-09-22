import { useState } from "react"
import {
  Box, Typography, Stack, Slider, FormControlLabel, Switch, Button,
} from "@mui/material"
import SpeedOutlinedIcon from "@mui/icons-material/SpeedOutlined"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"

// Speed up / slow down a video. The slider is mapped to a non-linear scale
// so the popular range (0.5×-2×) gets the most resolution; clamped to
// 0.25×-4× on either end. Quick-pick chips below for common values.

const QUICK_SPEEDS = [
  { v: 0.5, label: "0.5×" },
  { v: 0.75, label: "0.75×" },
  { v: 1.25, label: "1.25×" },
  { v: 1.5, label: "1.5×" },
  { v: 2.0, label: "2×" },
  { v: 3.0, label: "3×" },
]

export default function ToolSpeed() {
  useDocumentTitle("Speed up / Slow down")
  const [speed, setSpeed] = useState(1.5)
  const [keepPitch, setKeepPitch] = useState(true)

  // Reject the 1.0 no-op same way the backend does, so the user gets
  // immediate feedback instead of a submit failure.
  const isNoOp = Math.abs(speed - 1.0) < 0.01

  return (
    <ToolRunner
      title="Speed up / Slow down"
      description="Adjust video playback speed from 0.25× to 4× with optional pitch preservation"
      icon={<SpeedOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/speed"
      processLabel="Apply speed"
      canSubmit={!isNoOp}
      disabledReason="Pick a speed other than 1×"
      fieldBuilder={() => ({ speed, keep_pitch: keepPitch })}
    >
      <Stack spacing={2.5}>
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 1 }}>
            Speed multiplier — <strong>{speed.toFixed(2)}×</strong>
          </Typography>
          <Slider
            value={speed}
            min={0.25} max={4.0} step={0.05}
            marks={[
              { value: 0.25, label: "0.25×" },
              { value: 1.0, label: "1×" },
              { value: 2.0, label: "2×" },
              { value: 4.0, label: "4×" },
            ]}
            onChange={(_, v) => setSpeed(v)}
            valueLabelDisplay="auto"
            valueLabelFormat={(v) => `${v.toFixed(2)}×`}
            size="small"
          />
          {isNoOp && (
            <Typography variant="caption" sx={{ color: "warning.main", display: "block", mt: 0.5 }}>
              1× is a no-op — pick any other value.
            </Typography>
          )}
        </Box>

        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.75 }}>
            Quick pick
          </Typography>
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
            {QUICK_SPEEDS.map(({ v, label }) => (
              <Button
                key={v}
                size="small"
                variant={Math.abs(v - speed) < 0.01 ? "contained" : "outlined"}
                onClick={() => setSpeed(v)}
                sx={{ minWidth: 60, textTransform: "none", fontWeight: 700 }}
              >
                {label}
              </Button>
            ))}
          </Stack>
        </Box>

        <FormControlLabel
          control={<Switch checked={keepPitch} onChange={(e) => setKeepPitch(e.target.checked)} />}
          label={
            <Box>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                Keep audio pitch
              </Typography>
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                {keepPitch
                  ? "Voices stay natural even at higher speeds (recommended)"
                  : "Audio plays at the new speed — chipmunk effect at >1×, slowed-down deep voice at <1×"}
              </Typography>
            </Box>
          }
        />
      </Stack>
    </ToolRunner>
  )
}
