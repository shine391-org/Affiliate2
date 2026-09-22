import { useState } from "react"
import {
  Typography, ToggleButton, ToggleButtonGroup, Alert,
} from "@mui/material"
import ClosedCaptionOutlinedIcon from "@mui/icons-material/ClosedCaptionOutlined"
import RecordVoiceOverOutlinedIcon from "@mui/icons-material/RecordVoiceOverOutlined"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"

// Subtitle Export — Whisper transcribes the video and emits a downloadable
// subtitle file (.srt / .vtt) or plain transcript (.txt). No burn-in — this
// is the file you upload to YouTube or hand to an editor. To burn captions
// INTO the video instead, use Add Captions.

const FORMATS = [
  { value: "srt", label: "SRT", description: "Subtitles — universal" },
  { value: "vtt", label: "VTT", description: "WebVTT — web players" },
  { value: "txt", label: "TXT", description: "Plain transcript" },
]

export default function ToolSubtitles() {
  useDocumentTitle("Export Subtitles")
  const [format, setFormat] = useState("srt")

  return (
    <ToolRunner
      title="Export Subtitles"
      description="Transcribe a video to a downloadable .srt / .vtt subtitle file or a plain transcript"
      icon={<ClosedCaptionOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/subtitles"
      processLabel="Transcribe"
      downloadLabel={`Download .${format}`}
      fieldBuilder={() => ({ format })}
    >
      <Alert
        severity="info"
        icon={<RecordVoiceOverOutlinedIcon fontSize="small" />}
        sx={{ mb: 2, py: 0.5, "& .MuiAlert-message": { fontSize: "0.82rem" } }}
      >
        Works on videos with clear spoken audio. Music-only or silent clips
        return no subtitles. To burn captions onto the video instead, use{" "}
        <strong>Add Captions</strong>.
      </Alert>

      <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 1 }}>
        Output format
      </Typography>
      <ToggleButtonGroup
        value={format}
        exclusive
        onChange={(_, v) => v && setFormat(v)}
        size="small"
        sx={{ flexWrap: "wrap" }}
      >
        {FORMATS.map((f) => (
          <ToggleButton key={f.value} value={f.value} sx={{ textTransform: "none", flexDirection: "column", py: 1, px: 2 }}>
            <Typography variant="body2" sx={{ fontWeight: 700 }}>{f.label}</Typography>
            <Typography variant="caption" sx={{ color: "text.secondary" }}>{f.description}</Typography>
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
    </ToolRunner>
  )
}
