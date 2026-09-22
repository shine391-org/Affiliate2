import { useState } from "react"
import { TextField, Box, Button, CircularProgress, Tooltip } from "@mui/material"
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesomeOutlined"
import UndoIcon from "@mui/icons-material/UndoOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

// Shared multiline text input for the long-text fields across the tool
// pages (AI Image / AI Video Clip prompts, Music lyrics, Metadata script,
// Voice-over script). Centralizes two things every long-text field wants
// that MUI's TextField doesn't give by default:
//
//   1. A manual vertical drag-resize handle (`resize: vertical` on the
//      underlying <textarea>).
//   2. A high default `maxRows` so long prompts grow far before the inner
//      scrollbar kicks in.
//
// Opt-in AI assist (when `enhanceKind` is passed): a floating "✨ Enhance"
// button rewrites the current text via POST /api/tools/enhance-prompt and
// writes the result back through the caller's onChange (so the caller's own
// char-limit/slice still applies). One Undo restores the pre-enhance text.
// Pages that don't pass `enhanceKind` get the plain field, unchanged — every
// existing call site keeps working with no edits.
//
// Cmd/Ctrl+Enter submit is handled centrally by ToolRunner (a keydown
// listener on its root), so it isn't wired here.
export default function PromptField({
  minRows = 3,
  maxRows = 18,
  sx,
  enhanceKind,          // "image" | "video" | "music" | "voiceover" | "metadata" | "script"
  enhanceContext = "",  // optional extra context sent to the enhancer
  value,
  onChange,
  ...rest
}) {
  const [loading, setLoading] = useState(false)
  const [prevValue, setPrevValue] = useState(null) // enables one-step Undo
  const showSnackbar = useAppStore((s) => s.showSnackbar)

  const emit = (text) => {
    // Reuse the caller's onChange so its slice()/limit logic still runs.
    onChange?.({ target: { value: text } })
  }

  const enhance = async () => {
    if (loading) return
    setLoading(true)
    try {
      const form = new FormData()
      form.append("draft", value || "")
      form.append("kind", enhanceKind)
      if (enhanceContext) form.append("context", enhanceContext)
      const { data } = await http.post("/api/tools/enhance-prompt", form, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 45000,
      })
      if (data?.enhanced) {
        setPrevValue(value ?? "")
        emit(data.enhanced)
      }
    } catch (e) {
      const msg = e?.response?.data?.detail || e.message || "Enhance failed"
      showSnackbar?.(msg, "error")
    } finally {
      setLoading(false)
    }
  }

  const undo = () => {
    if (prevValue === null) return
    emit(prevValue)
    setPrevValue(null)
  }

  return (
    <Box sx={{ position: "relative" }}>
      <TextField
        multiline
        fullWidth
        minRows={minRows}
        maxRows={maxRows}
        value={value}
        onChange={onChange}
        sx={[{ "& textarea": { resize: "vertical" } }, ...(Array.isArray(sx) ? sx : [sx])]}
        {...rest}
      />
      {enhanceKind && (
        // Floating top-right — the upper-right corner of a 3-row textarea is
        // empty whitespace, so the button never sits on top of typed text.
        <Box
          sx={{
            position: "absolute",
            top: 6,
            right: 8,
            display: "flex",
            gap: 0.5,
            zIndex: 1,
          }}
        >
          {prevValue !== null && (
            <Tooltip title="Undo enhance">
              <Button
                size="small"
                onClick={undo}
                startIcon={<UndoIcon sx={{ fontSize: "16px !important" }} />}
                sx={{
                  minWidth: 0, px: 1, py: 0.25, fontSize: "0.72rem",
                  color: "text.secondary",
                  bgcolor: (t) => t.palette.mode === "dark" ? "rgba(0,0,0,0.35)" : "rgba(255,255,255,0.75)",
                  backdropFilter: "blur(4px)",
                }}
              >
                Undo
              </Button>
            </Tooltip>
          )}
          <Tooltip title={value?.trim() ? "Rewrite this into a stronger prompt" : "Generate a starting prompt"}>
            <span>
              <Button
                size="small"
                onClick={enhance}
                disabled={loading}
                startIcon={loading
                  ? <CircularProgress size={13} color="inherit" />
                  : <AutoAwesomeIcon sx={{ fontSize: "16px !important" }} />}
                sx={{
                  minWidth: 0, px: 1, py: 0.25, fontSize: "0.72rem", fontWeight: 700,
                  color: "primary.main",
                  bgcolor: (t) => t.palette.mode === "dark" ? "rgba(0,0,0,0.35)" : "rgba(255,255,255,0.75)",
                  backdropFilter: "blur(4px)",
                  "&:hover": { bgcolor: (t) => t.palette.mode === "dark" ? "rgba(0,0,0,0.55)" : "rgba(255,255,255,0.92)" },
                }}
              >
                {loading ? "Enhancing…" : "Enhance"}
              </Button>
            </span>
          </Tooltip>
        </Box>
      )}
    </Box>
  )
}
