import { useCallback, useEffect, useMemo, useState } from "react"
import { Alert, Box, Button, Chip, FormControl, InputLabel, MenuItem, Paper, Select, Stack, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material"
import MovieCreationOutlinedIcon from "@mui/icons-material/MovieCreationOutlined"
import FolderOpenOutlinedIcon from "@mui/icons-material/FolderOpenOutlined"
import { useLocation, useNavigate, useSearchParams } from "react-router-dom"
import http from "../api/http"
import useAppStore from "../store/appStore"
import useDocumentTitle from "../hooks/useDocumentTitle"
import ClipStudio from "./ClipStudio"
import StudioLocalize from "./StudioLocalize"
import { STUDIO_MODES, buildStudioModeHref, studioModeForPath } from "../studioModel"

function ReservedMode({ mode, source }) {
  const smart = mode === "smart-cut"
  return (
    <Stack alignItems="center" justifyContent="center" spacing={2} sx={{ minHeight: 360, p: 3, textAlign: "center" }}>
      <MovieCreationOutlinedIcon sx={{ fontSize: 52, color: "text.disabled" }} />
      <Typography variant="h5" sx={{ fontWeight: 700 }}>{smart ? "Smart Cut" : "Creative Remake"}</Typography>
      <Alert severity="info" variant="outlined" sx={{ maxWidth: 620 }}>
        {smart
          ? "Planned for Phase D. It will reuse this source, transcript and canonical timeline. No Smart Cut processing is implemented in this phase."
          : "Planned for Phase E. It will reuse this project and require storyboard approval before expensive rendering. No remake generation is implemented in this phase."}
      </Alert>
      {source && <Chip label={`Reserved source: ${source.title || source.id}`} variant="outlined" />}
    </Stack>
  )
}

export default function Studio() {
  useDocumentTitle("Studio")
  const location = useLocation()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const openActivity = useAppStore((s) => s.openActivity)
  const [sources, setSources] = useState([])
  const [loading, setLoading] = useState(true)
  const mode = studioModeForPath(location.pathname)
  const sourceId = params.get("source") || ""

  const loadSources = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await http.get("/api/downloaded", { params: { limit: 200 } })
      setSources((data?.videos || []).filter((item) => item.file_exists && item.video_path))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadSources() }, [loadSources])
  const source = useMemo(() => sources.find((item) => item.id === sourceId) || null, [sources, sourceId])

  const chooseSource = (id) => {
    setParams((previous) => {
      const next = new URLSearchParams(previous)
      if (id) next.set("source", id); else next.delete("source")
      next.delete("job")
      next.delete("step")
      return next
    })
  }

  const changeMode = (_, nextMode) => {
    if (!nextMode) return
    navigate(buildStudioModeHref(nextMode, location.search))
  }

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <Box sx={{ px: { xs: 2, md: 3 }, py: 1.5, borderBottom: 1, borderColor: "divider", bgcolor: "background.paper", flexShrink: 0 }}>
        <Stack direction={{ xs: "column", lg: "row" }} spacing={1.5} alignItems={{ lg: "center" }}>
          <Box sx={{ minWidth: 180 }}>
            <Typography variant="h5" sx={{ fontWeight: 800 }}>Studio</Typography>
            <Typography variant="caption" color="text.secondary">One source, one project context, multiple workflows</Typography>
          </Box>
          <ToggleButtonGroup
            exclusive value={mode} onChange={changeMode} size="small"
            sx={{ overflowX: "auto", maxWidth: "100%", "& .MuiToggleButton-root": { textTransform: "none", whiteSpace: "nowrap", px: 1.5 } }}
          >
            {STUDIO_MODES.map((item) => (
              <ToggleButton key={item.value} value={item.value}>
                {item.label}{!item.active && <Chip label={`Phase ${item.phase}`} size="small" sx={{ ml: 0.75, height: 18, fontSize: "0.62rem" }} />}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          <Box sx={{ flex: 1 }} />
          <Button size="small" variant="outlined" onClick={openActivity}>Activity</Button>
        </Stack>
        <Stack direction={{ xs: "column", md: "row" }} spacing={1.25} alignItems={{ md: "center" }} sx={{ mt: 1.5 }}>
          <FormControl size="small" sx={{ minWidth: { xs: "100%", md: 360 } }}>
            <InputLabel>Source from Library</InputLabel>
            <Select value={sourceId} label="Source from Library" onChange={(event) => chooseSource(event.target.value)} disabled={loading}>
              <MenuItem value=""><em>Select a downloaded source</em></MenuItem>
              {sources.map((item) => <MenuItem key={item.id} value={item.id}>{item.title || item.id}</MenuItem>)}
            </Select>
          </FormControl>
          {source ? (
            <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
              <Chip label={source.platform || "local"} size="small" />
              {source.duration_seconds > 0 && <Chip label={`${source.duration_seconds}s`} size="small" variant="outlined" />}
              {source.media_metadata?.width && <Chip label={`${source.media_metadata.width}×${source.media_metadata.height}`} size="small" variant="outlined" />}
              {source.source_url && <Button size="small" href={source.source_url} target="_blank" rel="noopener">Open source</Button>}
            </Stack>
          ) : (
            <Button size="small" startIcon={<FolderOpenOutlinedIcon />} onClick={() => navigate("/library")}>Choose from Library</Button>
          )}
        </Stack>
      </Box>

      <Box sx={{ flex: 1, minHeight: 0, overflow: mode === "manual" ? "hidden" : "auto" }}>
        {mode === "localize" && <StudioLocalize source={source} sourcesLoading={loading} />}
        {mode === "manual" && <ClipStudio />}
        {(mode === "smart-cut" || mode === "remake") && <ReservedMode mode={mode} source={source} />}
      </Box>
    </Box>
  )
}
