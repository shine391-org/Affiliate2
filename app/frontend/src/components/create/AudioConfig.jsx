import { useState, useEffect } from "react"
import {
  Typography, Divider, FormControl, InputLabel, Select, MenuItem,
  FormControlLabel, Checkbox, Box, Stack, Paper, Collapse,
  IconButton, Tooltip, TextField, Button, CircularProgress,
} from "@mui/material"
import AddIcon from "@mui/icons-material/Add"
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome"
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

/* ── Caption Style Visual Previews ─────────────────────────────────── */

const CAPTION_PREVIEWS = {
  viral:   { bg: "#000", words: ["This", "changes", "everything"], highlight: 1, color: "#fff", hlColor: "#FFD700", font: "700", size: "0.85rem" },
  classic: { bg: "#1a1a1a", words: ["The secret to success is consistency"], highlight: -1, color: "#fff", hlColor: "#fff", font: "400", size: "0.72rem" },
  bold:    { bg: "#000", words: ["STOP", "scrolling"], highlight: 0, color: "#fff", hlColor: "#00FF00", font: "900", size: "0.95rem" },
  neon:    { bg: "#1a0025", words: ["Glow", "up", "now"], highlight: 0, color: "#FF69B4", hlColor: "#00FFFF", font: "700", size: "0.85rem" },
  minimal: { bg: "#f5f5f5", words: ["Less is more when you know what matters"], highlight: -1, color: "#333", hlColor: "#333", font: "400", size: "0.68rem" },
  karaoke: { bg: "#111", words: ["Watch", "this", "closely"], highlight: 1, color: "#666", hlColor: "#FFD700", font: "700", size: "0.82rem" },
  glow:    { bg: "#0a0a0a", words: ["Golden", "hour", "magic"], highlight: 0, color: "#fff", hlColor: "#FF8C00", font: "700", size: "0.85rem" },
}

/** Convert ASS BGR color (&HBBGGRR) to CSS hex (#RRGGBB) */
function assToHex(assColor) {
  if (!assColor || !assColor.startsWith("&H")) return "#ffffff"
  const hex = assColor.replace("&H00", "").replace("&H", "")
  if (hex.length < 6) return "#ffffff"
  return `#${hex.slice(4, 6)}${hex.slice(2, 4)}${hex.slice(0, 2)}`
}

function buildPreviewFromStyle(style) {
  return {
    bg: "#111",
    words: ["Sample", "text", "here"],
    highlight: 1,
    color: assToHex(style.primary_color),
    hlColor: assToHex(style.highlight_color),
    font: "700",
    size: "0.82rem",
  }
}

function CaptionPreviewCard({ styleKey, label, isSelected, onClick, preview, onDelete }) {
  if (!preview) return null

  return (
    <Paper
      variant="outlined"
      onClick={onClick}
      sx={{
        minWidth: 120, p: 0, cursor: "pointer", overflow: "hidden", position: "relative",
        borderColor: isSelected ? "primary.main" : "divider",
        borderWidth: isSelected ? 2 : 1,
        bgcolor: isSelected ? "action.selected" : "background.paper",
        transition: "all 0.15s",
        "&:hover": { borderColor: "primary.light", transform: "translateY(-1px)", boxShadow: 1 },
        "&:hover .delete-btn": { opacity: 1 },
        flexShrink: 0,
      }}
    >
      {onDelete && (
        <IconButton
          className="delete-btn"
          size="small"
          onClick={(e) => { e.stopPropagation(); onDelete() }}
          sx={{
            position: "absolute", top: 2, right: 2, zIndex: 2, p: 0.25,
            opacity: 0, transition: "opacity 0.15s",
            bgcolor: "rgba(0,0,0,0.5)", color: "#fff",
            "&:hover": { bgcolor: "error.main" },
          }}
        >
          <DeleteOutlineIcon sx={{ fontSize: 14 }} />
        </IconButton>
      )}
      {/* Visual preview area */}
      <Box sx={{
        bgcolor: preview.bg, px: 1.5, py: 1.25,
        display: "flex", alignItems: "center", justifyContent: "center",
        minHeight: 40,
      }}>
        <Typography sx={{
          fontWeight: preview.font, fontSize: preview.size,
          textAlign: "center", lineHeight: 1.3, letterSpacing: "0.01em",
        }}>
          {preview.words.map((word, i) => {
            const isHighlighted = i === preview.highlight
            return (
              <span key={i} style={{
                color: isHighlighted ? preview.hlColor : preview.color,
                marginRight: i < preview.words.length - 1 ? "0.3em" : 0,
              }}>
                {word}
              </span>
            )
          })}
        </Typography>
      </Box>
      {/* Label */}
      <Box sx={{ px: 1, py: 0.5, textAlign: "center" }}>
        <Typography variant="caption" sx={{
          fontWeight: isSelected ? 700 : 500,
          fontSize: "0.68rem",
          color: isSelected ? "primary.main" : "text.secondary",
        }}>
          {label}
        </Typography>
      </Box>
    </Paper>
  )
}

/* ── Main Component ────────────────────────────────────────────────── */

export default function AudioConfig({
  ttsProvider, setTtsProvider, captionEnabled, setCaptionEnabled,
  captionStyle, setCaptionStyle, musicEnabled, setMusicEnabled, musicGenre, setMusicGenre,
  TTS_PROVIDERS, CAPTION_STYLES, MUSIC_GENRES,
}) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [customStyles, setCustomStyles] = useState([])
  const [aiPrompt, setAiPrompt] = useState("")
  const [aiLoading, setAiLoading] = useState(false)
  const [showAiInput, setShowAiInput] = useState(false)

  useEffect(() => {
    http.get("/api/captions/styles").then(res => {
      setCustomStyles(res.data.custom || [])
    }).catch(() => {})
  }, [])

  const handleAiGenerate = async () => {
    if (!aiPrompt.trim()) return
    setAiLoading(true)
    try {
      const { data } = await http.post("/api/captions/styles/generate", { description: aiPrompt.trim() })
      setCustomStyles(prev => [data, ...prev])
      setCaptionStyle(data.id)
      setAiPrompt("")
      setShowAiInput(false)
      showSnackbar(`Caption style "${data.name}" created!`, "success")
    } catch (e) {
      showSnackbar(e.response?.data?.detail || "AI generation failed", "error")
    } finally {
      setAiLoading(false)
    }
  }

  const handleDelete = async (styleId) => {
    try {
      await http.delete(`/api/captions/styles/${styleId}`)
      setCustomStyles(prev => prev.filter(s => s.id !== styleId))
      if (captionStyle === styleId) setCaptionStyle("viral")
      showSnackbar("Caption style deleted", "info")
    } catch (e) {
      showSnackbar("Failed to delete style", "error")
    }
  }

  return (
    <>
      <Divider sx={{ my: 1.5 }} />
      <Typography variant="caption" sx={{ fontWeight: 600, color: "text.secondary" }}>Voice</Typography>
      <FormControl fullWidth size="small" sx={{ mt: 0.5 }}>
        <InputLabel>Voice Provider</InputLabel>
        <Select value={ttsProvider} onChange={e => setTtsProvider(e.target.value)} label="Voice Provider">
          {TTS_PROVIDERS.map(p => (
            <MenuItem key={p.value} value={p.value}>
              {p.label}
              <Typography component="span" variant="caption" sx={{ ml: 1, color: "text.secondary" }}>({p.cost})</Typography>
            </MenuItem>
          ))}
        </Select>
      </FormControl>

      <Divider sx={{ my: 1.5 }} />
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <Typography variant="caption" sx={{ fontWeight: 600, color: "text.secondary" }}>Captions</Typography>
        <Stack direction="row" spacing={0.5}>
          <Tooltip title="Generate with AI" arrow>
            <IconButton size="small" onClick={() => setShowAiInput(!showAiInput)}
              sx={{ color: showAiInput ? "primary.main" : "text.secondary" }}>
              <AutoAwesomeIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Tooltip>
        </Stack>
      </Stack>
      <FormControlLabel label="Animated captions" sx={{ mt: 0.25 }}
        control={<Checkbox size="small" checked={captionEnabled} onChange={e => setCaptionEnabled(e.target.checked)} />} />

      {/* AI generate input */}
      <Collapse in={showAiInput && captionEnabled}>
        <Stack direction="row" spacing={1} sx={{ mt: 0.5, mb: 1 }}>
          <TextField
            size="small" fullWidth
            placeholder="Describe a style... (e.g. 'neon pink TikTok dance style')"
            value={aiPrompt}
            onChange={(e) => setAiPrompt(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleAiGenerate() }}
            disabled={aiLoading}
            sx={{ "& .MuiInputBase-input": { fontSize: "0.8rem" } }}
          />
          <Button
            variant="contained" size="small"
            onClick={handleAiGenerate}
            disabled={aiLoading || !aiPrompt.trim()}
            sx={{ minWidth: 80, fontSize: "0.75rem" }}
          >
            {aiLoading ? <CircularProgress size={16} /> : "Create"}
          </Button>
        </Stack>
      </Collapse>

      {/* Caption style visual preview cards */}
      <Collapse in={captionEnabled}>
        <Box sx={{
          display: "flex", gap: 1, overflowX: "auto", pb: 0.5, mt: 0.5,
          "&::-webkit-scrollbar": { height: 3 },
          "&::-webkit-scrollbar-thumb": { bgcolor: "divider", borderRadius: 2 },
        }}>
          {/* Built-in styles */}
          {CAPTION_STYLES.map(s => (
            <CaptionPreviewCard
              key={s.value}
              styleKey={s.value}
              label={s.label.split(" — ")[0]}
              isSelected={captionStyle === s.value}
              onClick={() => setCaptionStyle(s.value)}
              preview={CAPTION_PREVIEWS[s.value]}
            />
          ))}
          {/* Custom styles */}
          {customStyles.map(s => (
            <CaptionPreviewCard
              key={s.id}
              styleKey={s.id}
              label={s.name}
              isSelected={captionStyle === s.id}
              onClick={() => setCaptionStyle(s.id)}
              preview={buildPreviewFromStyle(s)}
              onDelete={() => handleDelete(s.id)}
            />
          ))}
        </Box>
      </Collapse>

      <Divider sx={{ my: 1.5 }} />
      <Typography variant="caption" sx={{ fontWeight: 600, color: "text.secondary" }}>Music</Typography>
      <FormControlLabel label="Background music"
        control={<Checkbox size="small" checked={musicEnabled} onChange={e => setMusicEnabled(e.target.checked)} />} />
      {musicEnabled && (
        <FormControl fullWidth size="small">
          <InputLabel>Genre</InputLabel>
          <Select value={musicGenre} onChange={e => setMusicGenre(e.target.value)} label="Genre">
            {MUSIC_GENRES.map(g => <MenuItem key={g.value} value={g.value}>{g.label}</MenuItem>)}
          </Select>
        </FormControl>
      )}
    </>
  )
}
