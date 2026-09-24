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
import {
  STUDIO_ASSET_PROVIDERS,
  STUDIO_READINESS,
  buildStudioSourceContext,
  studioAssetProviderHref,
} from "../studioReadinessModel"

function CreativeRemakeReserved({ source, context, navigate }) {
  const contract = STUDIO_READINESS.remake

  return (
    <Box sx={{ maxWidth: 1040, mx: "auto", p: { xs: 2, md: 3 } }}>
      <Stack spacing={2.25}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={2} alignItems={{ md: "center" }}>
          <MovieCreationOutlinedIcon sx={{ fontSize: 48, color: "text.disabled" }} />
          <Box sx={{ flex: 1 }}>
            <Typography variant="h5" sx={{ fontWeight: 800 }}>{contract.title}</Typography>
            <Typography variant="body2" color="text.secondary">{contract.promise}</Typography>
          </Box>
          <Chip label={`Phase ${contract.phase} readiness`} variant="outlined" />
        </Stack>

        <Alert severity="info" variant="outlined">
          This is a readiness surface only. No Creative Remake processing exists yet; Phase {contract.phase} must plug into this same Studio/source contract instead of creating another app.
        </Alert>

        {!source ? (
          <Alert severity="warning">Select a source from Library first. Phase {contract.phase} must reuse that persisted source ID rather than upload/import it again.</Alert>
        ) : (
          <>
            <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Shared source contract</Typography>
              <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                <Chip size="small" label={`source ${context?.source_id || source.id}`} />
                <Chip size="small" variant="outlined" label={context?.library_key || `dl:${source.id}`} />
                {context?.duration_ms != null && <Chip size="small" variant="outlined" label={`${(context.duration_ms / 1000).toFixed(2)}s timeline`} />}
                {source.platform && <Chip size="small" variant="outlined" label={source.platform} />}
              </Stack>
            </Paper>

            <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
              <Typography variant="subtitle2">Storyboard approval contract</Typography>
              <Typography variant="body2" sx={{ mt: 0.75 }}>
                Phase E must persist source → scene map → creative beats → asset map → storyboard, then stop for explicit human approval before composition/render.
              </Typography>
              <Button size="small" sx={{ mt: 1 }} disabled>Storyboard review · Phase E</Button>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.75 }}>
                The Review contract is reserved, but the queue stays disabled until a real Creative Remake engine registers live exceptions.
              </Typography>
            </Paper>

            <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
              <Typography variant="subtitle2">Contextual asset providers</Typography>
              <Typography variant="caption" color="text.secondary">Existing providers stay secondary capabilities; Creative Remake may call them from this project context.</Typography>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} sx={{ mt: 1.25 }}>
                {STUDIO_ASSET_PROVIDERS.map((provider) => (
                  <Button
                    key={provider.id}
                    variant="outlined"
                    onClick={() => navigate(studioAssetProviderHref(provider, source.id))}
                  >
                    {provider.label}
                  </Button>
                ))}
              </Stack>
            </Paper>

            <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
              <Typography variant="subtitle2">Reserved workflow</Typography>
              <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
                {contract.steps.map((step, index) => <Chip key={step} size="small" variant="outlined" label={`${index + 1}. ${step.replaceAll("-", " ")}`} />)}
              </Stack>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1.5 }}>
                Future Review categories
              </Typography>
              <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap sx={{ mt: 0.75 }}>
                {contract.review_categories.map((category) => <Chip key={category} size="small" label={category} />)}
              </Stack>
            </Paper>
          </>
        )}
      </Stack>
    </Box>
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
  const [sourceDetail, setSourceDetail] = useState(null)
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
  const sourceSummary = useMemo(() => sources.find((item) => item.id === sourceId) || null, [sources, sourceId])

  useEffect(() => {
    if (!sourceId) { setSourceDetail(null); return }
    let alive = true
    http.get(`/api/downloaded/${sourceId}`)
      .then(({ data }) => { if (alive) setSourceDetail(data) })
      .catch(() => { if (alive) setSourceDetail(null) })
    return () => { alive = false }
  }, [sourceId])

  const source = sourceDetail?.id === sourceId ? sourceDetail : sourceSummary
  const sourceContext = useMemo(() => buildStudioSourceContext(source), [source])

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

      <Box sx={{ flex: 1, minHeight: 0, overflow: (mode === "manual" || mode === "smart-cut") ? "hidden" : "auto" }}>
        {mode === "localize" && <StudioLocalize source={source} sourcesLoading={loading} />}
        {mode === "manual" && <ClipStudio key="manual" workflow="manual" />}
        {mode === "smart-cut" && <ClipStudio key="smart-cut" workflow="smart-cut" />}
        {mode === "remake" && <CreativeRemakeReserved source={source} context={sourceContext} navigate={navigate} />}
      </Box>
    </Box>
  )
}
