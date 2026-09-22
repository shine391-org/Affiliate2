import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  FormControl,
  InputLabel,
  LinearProgress,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material"
import PlayArrowOutlinedIcon from "@mui/icons-material/PlayArrowOutlined"
import ReplayOutlinedIcon from "@mui/icons-material/ReplayOutlined"

const VOICES = [
  { value: "vi-VN-NamMinhNeural", label: "NamMinh — male" },
  { value: "vi-VN-HoaiMyNeural", label: "HoaiMy — female" },
]

function jobColor(status) {
  if (status === "success") return "success"
  if (status === "failed") return "error"
  if (status === "running") return "info"
  return "default"
}

export default function ActionControls({
  rerender = false,
  previousAction = null,
  actionType,
  onActionType,
  voiceId,
  onVoiceId,
  targetDuration,
  onTargetDuration,
  translateMode,
  onTranslateMode,
  job,
  busy,
  onRun,
}) {
  const resolved = rerender ? (previousAction || "remake") : actionType
  const active = job && ["pending", "running"].includes(job.status)
  const progress = Number.isFinite(job?.progress_pct) ? job.progress_pct : 0

  return (
    <Card variant="outlined" sx={{ borderColor: rerender ? "warning.light" : "primary.light" }}>
      <CardContent>
        <Stack spacing={2}>
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
              {rerender ? "Rerender" : "Production action"}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {rerender
                ? "Repeat the last production mode using the human REWORK note."
                : "Choose how ViralMint should turn this approved candidate into a Vietnamese final."}
            </Typography>
          </Box>

          {rerender ? (
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="body2" color="text.secondary">Previous mode:</Typography>
              <Chip size="small" label={resolved === "translate" ? "Translate / Dub" : "Creative Remake"} />
            </Stack>
          ) : (
            <FormControl size="small" fullWidth>
              <InputLabel id="production-action-label">Action</InputLabel>
              <Select
                labelId="production-action-label"
                label="Action"
                value={actionType}
                onChange={(e) => onActionType(e.target.value)}
                disabled={active || busy}
              >
                <MenuItem value="remake">Creative Remake</MenuItem>
                <MenuItem value="translate">Translate / Dub</MenuItem>
              </Select>
            </FormControl>
          )}

          <FormControl size="small" fullWidth>
            <InputLabel id="voice-label">Vietnamese voice</InputLabel>
            <Select
              labelId="voice-label"
              label="Vietnamese voice"
              value={voiceId}
              onChange={(e) => onVoiceId(e.target.value)}
              disabled={active || busy || (resolved === "translate" && translateMode === "captions_only")}
            >
              {VOICES.map((v) => <MenuItem key={v.value} value={v.value}>{v.label}</MenuItem>)}
            </Select>
          </FormControl>

          {resolved === "remake" ? (
            <FormControl size="small" fullWidth>
              <InputLabel id="duration-label">Target duration</InputLabel>
              <Select
                labelId="duration-label"
                label="Target duration"
                value={targetDuration}
                onChange={(e) => onTargetDuration(e.target.value)}
                disabled={active || busy}
              >
                <MenuItem value="20-35s">20–35 seconds</MenuItem>
                <MenuItem value="35-60s">35–60 seconds</MenuItem>
              </Select>
            </FormControl>
          ) : (
            <FormControl size="small" fullWidth>
              <InputLabel id="translate-mode-label">Translate mode</InputLabel>
              <Select
                labelId="translate-mode-label"
                label="Translate mode"
                value={translateMode}
                onChange={(e) => onTranslateMode(e.target.value)}
                disabled={active || busy}
              >
                <MenuItem value="full_dub">Full dub + Vietnamese captions</MenuItem>
                <MenuItem value="captions_only">Vietnamese captions only</MenuItem>
              </Select>
            </FormControl>
          )}

          {job && (
            <Box>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.75 }}>
                <Chip size="small" color={jobColor(job.status)} label={String(job.status || "unknown").toUpperCase()} />
                <Typography variant="body2" color="text.secondary">
                  {job.current_step || job.resolved_action || job.requested_action || ""}
                </Typography>
              </Stack>
              {active && <LinearProgress variant={progress > 0 ? "determinate" : "indeterminate"} value={progress} />}
              {job.error_message && <Alert severity="error" sx={{ mt: 1 }}>{job.error_message}</Alert>}
            </Box>
          )}

          <Button
            variant="contained"
            color={rerender ? "warning" : "primary"}
            startIcon={rerender ? <ReplayOutlinedIcon /> : <PlayArrowOutlinedIcon />}
            onClick={onRun}
            disabled={active || busy}
          >
            {rerender ? "Rerender" : (resolved === "translate" ? "Run Translate / Dub" : "Run Creative Remake")}
          </Button>
        </Stack>
      </CardContent>
    </Card>
  )
}
