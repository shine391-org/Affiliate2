import { Box, Typography, Button, Stack, Paper, IconButton } from "@mui/material"
import AddIcon from "@mui/icons-material/Add"
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline"
import SplitscreenIcon from "@mui/icons-material/Splitscreen"

/**
 * Shared scene storyboard grid used by all 3 studio pages.
 * Uses render-prop pattern: each page provides mode-specific card content via `renderCard`.
 */
export default function SceneStoryboard({
  scenes,
  setScenes,
  renderCard,
  onSplitScript,
  splitLoading,
  maxScenes = 12,
  showSplitButton = true,
  emptyMessage = "No scenes yet. Click 'Split into Scenes' to auto-split your script, or add scenes manually.",
}) {
  const updateScene = (idx, updates) => {
    setScenes(prev => prev.map((s, i) => i === idx ? { ...s, ...updates } : s))
  }

  const removeScene = (idx) => {
    setScenes(prev => prev.filter((_, i) => i !== idx))
  }

  const addScene = () => {
    if (scenes.length >= maxScenes) return
    setScenes(prev => [...prev, { text: "" }])
  }

  return (
    <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
      {/* Header */}
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1.5 }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 600, color: "text.secondary" }}>
          <SplitscreenIcon sx={{ fontSize: 16, verticalAlign: "text-bottom", mr: 0.5 }} />
          Scenes ({scenes.length || "none"})
        </Typography>
        {showSplitButton && (
          <Button
            size="small"
            onClick={onSplitScript}
            disabled={splitLoading}
            startIcon={<SplitscreenIcon sx={{ fontSize: 14 }} />}
            sx={{ fontSize: "0.75rem", textTransform: "none" }}
          >
            {splitLoading ? "Splitting..." : "Split into Scenes"}
          </Button>
        )}
      </Stack>

      {/* Empty state */}
      {scenes.length === 0 && (
        <Box sx={{
          py: 4, px: 2, textAlign: "center",
          border: 1, borderStyle: "dashed", borderColor: "divider", borderRadius: 2,
        }}>
          <Typography variant="body2" sx={{ color: "text.secondary", fontSize: "0.85rem" }}>
            {emptyMessage}
          </Typography>
        </Box>
      )}

      {/* Scene list (vertical) */}
      {scenes.length > 0 && (
        <Stack spacing={1.5}>
          {scenes.map((scene, idx) => (
            <Paper
              key={idx}
              variant="outlined"
              sx={{
                p: 1.5, borderRadius: 2, position: "relative",
                transition: "border-color 0.15s",
                "&:hover": { borderColor: "primary.light" },
              }}
            >
              {/* Scene number + delete */}
              <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 0.75 }}>
                <Typography variant="caption" sx={{ fontWeight: 700, color: "primary.main" }}>
                  Scene {idx + 1}
                </Typography>
                {scenes.length > 1 && (
                  <IconButton size="small" onClick={() => removeScene(idx)} sx={{ p: 0.25 }}>
                    <DeleteOutlineIcon sx={{ fontSize: 16, color: "text.secondary" }} />
                  </IconButton>
                )}
              </Stack>

              {/* Mode-specific card content via render prop */}
              {renderCard(scene, idx, (updates) => updateScene(idx, updates))}
            </Paper>
          ))}
        </Stack>
      )}

      {/* Add scene button */}
      <Button
        size="small"
        onClick={addScene}
        disabled={scenes.length >= maxScenes}
        startIcon={<AddIcon sx={{ fontSize: 14 }} />}
        sx={{ mt: 1.5, fontSize: "0.75rem", textTransform: "none" }}
      >
        Add scene {scenes.length >= maxScenes && `(max ${maxScenes})`}
      </Button>
    </Paper>
  )
}
