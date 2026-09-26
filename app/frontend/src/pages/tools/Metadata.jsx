import { useState } from "react"
import {
  Box, Typography, Stack, TextField, Alert, ToggleButton, ToggleButtonGroup,
  IconButton, Tooltip, Chip,
} from "@mui/material"
import { GlassPanel } from "../../utils/glassFx"
import LabelOutlinedIcon from "@mui/icons-material/LabelOutlined"
import ContentCopyIcon from "@mui/icons-material/ContentCopy"
import ToolRunner from "../../components/tools/ToolRunner"
import PromptField from "../../components/tools/PromptField"
import useDocumentTitle from "../../hooks/useDocumentTitle"
import useAppStore from "../../store/appStore"
import useJobOutput from "../../hooks/useJobOutput"

// Metadata generator — three input modes (paste text / pick a topic /
// upload a video). The result is parsed JSON returned in the job's
// extra_output, so we can render it inline without a separate fetch.

// Blank-page topic starters — click to fill, then generate (or "✨ Enhance"
// the script field first when in paste mode).
const TOPIC_STARTERS = [
  "5 beginner mistakes in [your niche]",
  "How I grew my channel to 10k in 90 days",
  "The truth about [trending topic] nobody tells you",
  "3 tools that 10x'd my workflow",
]

const MODES = [
  { value: "text", label: "Dán kịch bản", description: "Nhanh nhất — không cần chép lời" },
  { value: "topic", label: "Từ chủ đề", description: "Tạo từ một chủ đề ngắn" },
  { value: "file", label: "Tải video lên", description: "Whisper sẽ chép lời trước" },
]

function ResultPreview({ jobId }) {
  // The job's own output_json, via the shared hook: the store never carries a
  // structured `output`, so reading it here meant this preview could never
  // render — the "click Download instead" fallback was all anyone ever saw.
  // Going through the hook also makes it survive a reload.
  const { output, loading } = useJobOutput(jobId)
  const metadata = output?.metadata
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const copy = (s) => {
    if (!s) return
    navigator.clipboard.writeText(s).then(
      () => showSnackbar?.("Đã sao chép", "success"),
      () => showSnackbar?.("Không thể sao chép", "warning"),
    )
  }
  if (!metadata) {
    return (
      <Alert severity="info" sx={{ fontSize: "0.85rem" }}>
        {loading
          ? "Đang tải kết quả…"
          : <>Kết quả đã sẵn sàng — bấm <strong>Tải xuống</strong> bên dưới để lấy tệp JSON.</>}
      </Alert>
    )
  }
  const tags = metadata.youtube_tags || []
  return (
    <Stack spacing={1.5}>
      <Field label="Tiêu đề YouTube" value={metadata.youtube_title} onCopy={copy} />
      <Field label="Mô tả YouTube" value={metadata.youtube_description} onCopy={copy} multiline />
      {tags.length > 0 && (
        <Field
          label="Thẻ YouTube"
          value={tags.join(", ")}
          onCopy={copy}
        />
      )}
      <Field label="Chú thích TikTok" value={metadata.tiktok_title} onCopy={copy} multiline />
    </Stack>
  )
}

function Field({ label, value, onCopy, multiline }) {
  if (!value) return null
  return (
    <GlassPanel sx={{ p: 1.25, borderRadius: 1.5, display: "flex", alignItems: "flex-start", gap: 1 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5, fontSize: "0.65rem" }}>
          {label}
        </Typography>
        <Typography variant="body2" sx={{
          mt: 0.25, fontSize: "0.88rem", lineHeight: 1.45, wordBreak: "break-word",
          whiteSpace: multiline ? "pre-wrap" : "normal",
        }}>
          {value}
        </Typography>
      </Box>
      <Tooltip title="Sao chép">
        <IconButton size="small" onClick={() => onCopy(value)} sx={{ flexShrink: 0 }}>
          <ContentCopyIcon sx={{ fontSize: "0.95rem" }} />
        </IconButton>
      </Tooltip>
    </GlassPanel>
  )
}

export default function ToolMetadata() {
  useDocumentTitle("Tạo metadata")
  const [mode, setMode] = useState("text")
  const [text, setText] = useState("")
  const [topic, setTopic] = useState("")

  const canSubmit = mode === "text" ? !!text.trim()
    : mode === "topic" ? !!topic.trim()
    : true  // file mode — handled by ToolRunner's file presence check

  return (
    <ToolRunner
      title="Tiêu đề / Mô tả / Thẻ"
      description="Tạo metadata SEO cho video, bản chép lời hoặc chủ đề"
      icon={<LabelOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/metadata"
      processLabel="Generate"
      downloadLabel="Tải JSON"
      hideFileInput={mode !== "file"}
      canSubmit={canSubmit}
      disabledReason={mode === "topic" ? "Enter a topic" : "Paste your script or transcript"}
      fieldBuilder={() => ({
        text: mode === "text" ? text : "",
        topic: mode === "topic" ? topic : "",
      })}
      resultPreview={(jobId) => <ResultPreview jobId={jobId} />}
    >
      <Stack spacing={2}>
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 1 }}>
            Input mode
          </Typography>
          <ToggleButtonGroup
            value={mode}
            exclusive
            onChange={(_, v) => v && setMode(v)}
            size="small"
            sx={{ flexWrap: "wrap" }}
          >
            {MODES.map((m) => (
              <ToggleButton key={m.value} value={m.value} sx={{ textTransform: "none", flexDirection: "column", py: 1, px: 2 }}>
                <Typography variant="body2" sx={{ fontWeight: 700 }}>{m.label}</Typography>
                <Typography variant="caption" sx={{ color: "text.secondary" }}>{m.description}</Typography>
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Box>

        {mode === "text" && (
          <PromptField
            minRows={6} maxRows={18}
            autoFocus
            enhanceKind="metadata"
            label="Kịch bản / bản chép lời"
            placeholder="Dán kịch bản, bản chép lời hoặc dàn ý. AI sẽ chuyển thành metadata cho nền tảng."
            value={text}
            onChange={(e) => setText(e.target.value)}
            slotProps={{ htmlInput: { maxLength: 20000 } }}
            helperText={`${text.length} / 20,000 chars`}
          />
        )}

        {mode === "topic" && (
          <Stack spacing={1}>
            {!topic.trim() && (
              <Stack direction="row" useFlexGap flexWrap="wrap" sx={{ gap: 0.75 }}>
                {TOPIC_STARTERS.map((t) => (
                  <Chip
                    key={t}
                    label={t}
                    size="small"
                    onClick={() => setTopic(t)}
                    sx={{ fontSize: "0.74rem", height: 26 }}
                  />
                ))}
              </Stack>
            )}
            <TextField
              fullWidth
              autoFocus
              label="Chủ đề"
              placeholder='ví dụ: "Ý tưởng startup AI cho người sáng lập solo năm 2026"'
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              slotProps={{ htmlInput: { maxLength: 500 } }}
            />
          </Stack>
        )}

        {mode === "file" && (
          <Alert severity="info" sx={{ fontSize: "0.82rem" }}>
            Upload a video below. We'll transcribe it with Whisper, then generate metadata from the transcript.
          </Alert>
        )}
      </Stack>
    </ToolRunner>
  )
}
