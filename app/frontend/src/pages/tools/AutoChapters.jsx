import { useState } from "react"
import {
  Box, Typography, Stack, Slider, Alert, IconButton, Tooltip,
} from "@mui/material"
import { GlassPanel } from "../../utils/glassFx"
import FormatListNumberedIcon from "@mui/icons-material/FormatListNumbered"
import ContentCopyIcon from "@mui/icons-material/ContentCopy"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"
import useAppStore from "../../store/appStore"
import useJobOutput from "../../hooks/useJobOutput"

// Auto-chapters tool. Whisper-transcribe → AI clusters into chapters →
// emit a `MM:SS Title` text file. Inline preview shows the chapter list
// with a copy-to-clipboard button so users can paste straight into a
// YouTube description.

function formatTimestamp(seconds) {
  const s = Math.max(0, Math.floor(seconds))
  const mm = Math.floor(s / 60)
  const ss = s % 60
  if (mm >= 60) {
    const hh = Math.floor(mm / 60)
    return `${hh}:${String(mm % 60).padStart(2, "0")}:${String(ss).padStart(2, "0")}`
  }
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`
}

function chaptersToText(chapters) {
  return chapters.map((c) => `${formatTimestamp(c.start_seconds)} ${c.title || "Chương"}`).join("\n")
}

function ResultPreview({ jobId }) {
  // See useJobOutput: the store never carries a structured `output`, so this
  // chapter list could never render before — the fallback was all anyone saw.
  const { output, loading } = useJobOutput(jobId)
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const chapters = output?.chapters || []
  const copy = (s) => navigator.clipboard.writeText(s).then(
    () => showSnackbar?.("Đã sao chép", "success"),
    () => showSnackbar?.("Không thể sao chép", "warning"),
  )
  if (chapters.length === 0) {
    return (
      <Alert severity="info" sx={{ fontSize: "0.85rem" }}>
        {loading
          ? "Đang tải kết quả…"
          : <>Kết quả đã sẵn sàng — bấm <strong>Tải xuống</strong> bên dưới để lấy tệp văn bản.</>}
      </Alert>
    )
  }
  const plainText = chaptersToText(chapters)
  return (
    <GlassPanel sx={{ p: 1.5, borderRadius: 2 }}>
      <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5, fontSize: "0.65rem", flex: 1 }}>
          {chapters.length} chương — dán vào mô tả YouTube
        </Typography>
        <Tooltip title="Sao chép tất cả chương">
          <IconButton size="small" onClick={() => copy(plainText)}>
            <ContentCopyIcon sx={{ fontSize: "1rem" }} />
          </IconButton>
        </Tooltip>
      </Stack>
      <Box sx={{
        fontFamily: "monospace", fontSize: "0.82rem", lineHeight: 1.6,
        whiteSpace: "pre-wrap", color: "text.primary",
      }}>
        {plainText}
      </Box>
    </GlassPanel>
  )
}

export default function ToolAutoChapters() {
  useDocumentTitle("Tự tạo chương")
  const [targetCount, setTargetCount] = useState(0)

  return (
    <ToolRunner
      title="Tự tạo chương"
      description="AI tự tạo mốc chương YouTube từ video dài"
      icon={<FormatListNumberedIcon fontSize="large" />}
      endpoint="/api/tools/auto-chapters"
      processLabel="Tạo chương"
      downloadLabel="Tải .txt"
      fieldBuilder={() => ({ target_count: targetCount })}
      resultPreview={(jobId) => <ResultPreview jobId={jobId} />}
    >
      <Stack spacing={2}>
        <Box>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
            Target chapter count — {targetCount === 0 ? "Auto" : targetCount}
          </Typography>
          <Slider
            value={targetCount}
            min={0} max={20} step={1}
            marks={[
              { value: 0, label: "Tự động" },
              { value: 5, label: "5" },
              { value: 10, label: "10" },
              { value: 20, label: "20" },
            ]}
            onChange={(_, v) => setTargetCount(v)}
            valueLabelDisplay="auto"
            valueLabelFormat={(v) => v === 0 ? "Auto" : `${v}`}
            size="small"
          />
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            <strong>Tự động</strong> chọn khoảng 1 chương mỗi 2,5 phút (thường 5–12 chương cho video 15–30 phút).
            Best for long-form podcast episodes, lectures, and tutorials.
          </Typography>
        </Box>

        <Alert severity="info" sx={{ fontSize: "0.82rem" }}>
          The first chapter is forced to <strong>00:00</strong> — required by YouTube to detect the rest as chapters.
          Đầu ra là văn bản thuần theo định dạng <code>MM:SS Tiêu đề</code>, có thể dán thẳng vào mô tả video.
        </Alert>
      </Stack>
    </ToolRunner>
  )
}
