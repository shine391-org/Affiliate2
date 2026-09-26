import { Box, Typography, Button, Stack, Paper, TextField } from "@mui/material"
import AutoFixHighIcon from "@mui/icons-material/AutoFixHigh"
import EditNoteIcon from "@mui/icons-material/EditNote"
import SourcePanel from "./SourcePanel"

/**
 * Shared script editor panel used by all 3 studio pages.
 * Contains: source insights, AI generation input, script textarea, word count, polish button.
 */
export default function ScriptPanel({
  source,
  script, setScript,
  scriptInstructions, setScriptInstructions,
  onGenerateScript,
  onPolishScript,
  scriptLoading,
  scriptGenerated,
  sourceId,
  mode,
  children,
}) {
  const wordCount = script ? script.trim().split(/\s+/).filter(Boolean).length : 0
  const estSeconds = wordCount > 0 ? Math.ceil(wordCount / 2.5) : 0

  const placeholderInstruction = sourceId
    ? "Hướng dẫn AI viết kịch bản, ví dụ: 'viết bằng tiếng Trung', 'làm vui hơn', 'tập trung vào hook'..."
    : "Nhập chủ đề hoặc hướng dẫn, ví dụ: '5 mẹo tiết kiệm tiền', 'giải thích điện toán lượng tử đơn giản'..."

  const placeholderScript = sourceId
    ? "Bấm 'Tạo bằng AI' để tạo kịch bản từ insight đối thủ hoặc tự viết..."
    : "Viết kịch bản video tại đây. AI sẽ đọc nội dung này trên hình ảnh được tạo."

  return (
    <Stack spacing={2}>
      {/* Source video insights */}
      {source && <SourcePanel source={source} />}

      {/* Slot for page-specific content above script (e.g. image upload) */}
      {children}

      {/* Script editor */}
      <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600, color: "text.secondary" }}>
            <EditNoteIcon sx={{ fontSize: 18, verticalAlign: "text-bottom", mr: 0.5 }} />
            Script
          </Typography>
        </Stack>

        {/* AI script generation */}
        <Box sx={{ mb: 1.5 }}>
          <Stack direction="row" spacing={1} alignItems="flex-end">
            <TextField
              size="small"
              fullWidth
              value={scriptInstructions}
              onChange={e => setScriptInstructions(e.target.value)}
              placeholder={placeholderInstruction}
              onKeyDown={e => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault()
                  onGenerateScript()
                }
              }}
              sx={{ "& .MuiOutlinedInput-root": { fontSize: "0.85rem" } }}
            />
            <Button
              size="small" variant="contained"
              onClick={onGenerateScript}
              disabled={scriptLoading}
              startIcon={<AutoFixHighIcon />}
              sx={{ whiteSpace: "nowrap", minWidth: 140 }}
            >
              {scriptLoading ? "Đang tạo..." : scriptGenerated ? "Tạo lại" : "Tạo bằng AI"}
            </Button>
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
            {sourceId
              ? "Tùy chọn — để trống để AI tạo mặc định dựa trên insight đối thủ"
              : "Mô tả chủ đề video — AI sẽ viết kịch bản lời thoại hoàn chỉnh"
            }
          </Typography>
        </Box>

        <TextField
          multiline
          rows={12}
          fullWidth
          value={script}
          onChange={e => setScript(e.target.value)}
          placeholder={placeholderScript}
          sx={{ "& .MuiOutlinedInput-root": { fontSize: "0.9rem", lineHeight: 1.7 } }}
        />

        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mt: 0.5 }}>
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            {wordCount > 0
              ? `${wordCount} words \u2248 ${estSeconds}s`
              : "Đang trống — AI sẽ tự tạo nếu để trống"
            }
          </Typography>
          {script.trim() && (
            <Button
              size="small" onClick={onPolishScript}
              disabled={scriptLoading}
              startIcon={<AutoFixHighIcon />}
              sx={{ fontSize: "0.75rem", textTransform: "none" }}
            >
              Polish Script
            </Button>
          )}
        </Stack>
      </Paper>
    </Stack>
  )
}
