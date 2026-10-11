import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Alert, Box, Button, Chip, CircularProgress, FormControl, InputLabel, MenuItem, Paper, Select, Stack, TextField, Typography } from "@mui/material"
import MovieCreationOutlinedIcon from "@mui/icons-material/MovieCreationOutlined"
import FolderOpenOutlinedIcon from "@mui/icons-material/FolderOpenOutlined"
import { useLocation, useNavigate, useSearchParams } from "react-router-dom"
import http from "../api/http"
import useAppStore from "../store/appStore"
import useDocumentTitle from "../hooks/useDocumentTitle"
import ClipStudio from "./ClipStudio"
import StudioLocalize from "./StudioLocalize"
import { studioModeForPath } from "../studioModel"
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
          <Chip label={`Mức sẵn sàng Phase ${contract.phase}`} variant="outlined" />
        </Stack>

        <Alert severity="info" variant="outlined">
          This is a readiness surface only. No Creative Remake processing exists yet; Phase {contract.phase} must plug into this same Studio/source contract instead of creating another app.
        </Alert>

        {!source ? (
          <Alert severity="warning">Hãy chọn nguồn từ Thư viện trước.</Alert>
        ) : (
          <>
            <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Liên kết nguồn dùng chung</Typography>
              <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                <Chip size="small" label={`source ${context?.source_id || source.id}`} />
                <Chip size="small" variant="outlined" label={context?.library_key || `dl:${source.id}`} />
                {context?.duration_ms != null && <Chip size="small" variant="outlined" label={`${(context.duration_ms / 1000).toFixed(2)}s timeline`} />}
                {source.platform && <Chip size="small" variant="outlined" label={source.platform} />}
              </Stack>
            </Paper>

            <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
              <Typography variant="subtitle2">Quy trình duyệt storyboard</Typography>
              <Typography variant="body2" sx={{ mt: 0.75 }}>
                Phase E must persist source → scene map → creative beats → asset map → storyboard, then stop for explicit human approval before composition/render.
              </Typography>
              <Button size="small" sx={{ mt: 1 }} disabled>Duyệt storyboard · Phase E</Button>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.75 }}>
                The Review contract is reserved, but the queue stays disabled until a real Creative Remake engine registers live exceptions.
              </Typography>
            </Paper>

            <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
              <Typography variant="subtitle2">Nguồn tư liệu theo ngữ cảnh</Typography>
              <Typography variant="caption" color="text.secondary">Các nguồn hiện có vẫn là khả năng phụ; Làm lại sáng tạo có thể gọi chúng từ ngữ cảnh dự án này.</Typography>
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
              <Typography variant="subtitle2">Quy trình dự kiến</Typography>
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
  useDocumentTitle("Studio · Bản địa hóa")
  const location = useLocation()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const openActivity = useAppStore((s) => s.openActivity)
  const startJob = useAppStore((s) => s.startJob)
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [sources, setSources] = useState([])
  const [sourcesError, setSourcesError] = useState("")
  const sourcesRequestRef = useRef(0)
  const [loading, setLoading] = useState(true)
  const [sourceDetail, setSourceDetail] = useState(null)
  const [sourceUrl, setSourceUrl] = useState("")
  const [sourceDownloading, setSourceDownloading] = useState(false)
  const [sourceDownloadJobId, setSourceDownloadJobId] = useState("")
  const mode = studioModeForPath(location.pathname)
  const sourceId = params.get("source") || ""
  const localizationJobId = params.get("job") || ""

  const loadSources = useCallback(async () => {
    const requestId = ++sourcesRequestRef.current
    setLoading(true)
    setSourcesError("")
    try {
      const { data } = await http.get("/api/downloaded", { params: { limit: 200 } })
      if (requestId === sourcesRequestRef.current) {
        setSources((data?.videos || []).filter((item) => item.file_exists && item.video_path))
      }
    } catch (error) {
      if (requestId === sourcesRequestRef.current) {
        setSourcesError(error.response?.data?.detail || "Không thể tải danh sách nguồn video.")
      }
    } finally {
      if (requestId === sourcesRequestRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadSources()
    return () => { sourcesRequestRef.current += 1 }
  }, [loadSources])

  // A review-paused localization job may be opened from Activity using only
  // its job id. Recover the original downloaded source from the persisted run
  // so the Studio never opens as a dead-end "Không có nguồn" screen.
  useEffect(() => {
    if (mode !== "localize" || !localizationJobId) return undefined
    let alive = true
    http.get(`/api/localization/jobs/${localizationJobId}`)
      .then(({ data }) => {
        if (!alive) return
        const recovered = data?.manifest?.request?.downloaded_video_id
        if (recovered === sourceId) return
        setParams((previous) => {
          const next = new URLSearchParams(previous)
          // The persisted job is canonical. A stale/mismatched source query
          // would otherwise show one video while edits/reruns mutate another.
          // Legacy/direct-upload jobs have no DownloadedVideo id at all; clear
          // any stray source= so their persisted snapshot is the only preview.
          if (recovered) next.set("source", recovered)
          else next.delete("source")
          return next
        }, { replace: true })
      })
      .catch(() => {})
    return () => { alive = false }
  }, [localizationJobId, mode, setParams, sourceId])

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

  const downloadSourceUrl = async () => {
    const url = sourceUrl.trim()
    if (!/^https?:\/\/\S+$/i.test(url) || sourceDownloading) {
      if (url && !/^https?:\/\/\S+$/i.test(url)) {
        showSnackbar("Hãy dán một liên kết http(s) đầy đủ.", "warning")
      }
      return
    }
    setSourceDownloading(true)
    try {
      const { data } = await http.post("/api/downloaded/batch-download", {
        urls: [{ url }],
        options: { quality: "best", container: "mp4", metadata: true },
        analyze: false,
      })
      setSourceDownloadJobId(data.job_id)
      startJob(data.job_id, "download", "Đang tải nguồn vào Thư viện…")
      showSnackbar("Đang tải video vào Thư viện. Studio sẽ tự chọn nguồn khi hoàn tất.", "info")
    } catch (error) {
      setSourceDownloading(false)
      showSnackbar(error.response?.data?.detail || "Không thể bắt đầu tải nguồn", "error")
    }
  }

  useEffect(() => {
    if (!sourceDownloadJobId) return undefined
    let alive = true
    const poll = async () => {
      try {
        const { data } = await http.get(`/api/jobs/${sourceDownloadJobId}`)
        if (!alive) return
        if (data?.status === "success") {
          let output = data.output_json ?? data.output
          if (typeof output === "string") {
            try { output = JSON.parse(output) } catch { output = null }
          }
          const downloadedId = output?.downloaded_ids?.[0] || output?.videos?.[0]?.id
          if (!downloadedId) {
            setSourceDownloading(false)
            setSourceDownloadJobId("")
            showSnackbar("Video đã tải xong nhưng không tìm thấy ID asset mới.", "warning")
            return
          }
          await loadSources()
          chooseSource(downloadedId)
          setSourceUrl("")
          setSourceDownloading(false)
          setSourceDownloadJobId("")
          showSnackbar("Đã thêm vào Thư viện và chọn làm nguồn Studio.", "success")
        } else if (["failed", "cancelled"].includes(data?.status)) {
          setSourceDownloading(false)
          setSourceDownloadJobId("")
          showSnackbar(data?.error_message || "Tải nguồn thất bại", "error")
        }
      } catch {
        // Keep bounded poll alive
      }
    }
    poll()
    const timer = setInterval(poll, 1500)
    return () => { alive = false; clearInterval(timer) }
  }, [sourceDownloadJobId, loadSources, showSnackbar])

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <Box sx={{ px: { xs: 2, md: 3 }, py: 1.5, borderBottom: 1, borderColor: "divider", bgcolor: "background.paper", flexShrink: 0 }}>
        <Stack direction={{ xs: "column", lg: "row" }} spacing={1.5} alignItems={{ lg: "center" }}>
          <Box sx={{ minWidth: 180 }}>
            <Typography variant="h5" sx={{ fontWeight: 800 }}>Studio</Typography>
            <Typography variant="caption" color="text.secondary">Bản địa hóa AI chuẩn ngữ cảnh</Typography>
          </Box>
          <Box sx={{ flex: 1 }} />
          <Button size="small" variant="outlined" onClick={openActivity}>Hoạt động</Button>
        </Stack>
        <Stack direction={{ xs: "column", xl: "row" }} spacing={1.25} alignItems={{ xl: "center" }} sx={{ mt: 1.5 }}>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} sx={{ flex: 1, minWidth: { xl: 520 } }}>
            <TextField
              size="small"
              fullWidth
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") downloadSourceUrl() }}
              placeholder="Dán link TikTok / Douyin / YouTube…"
              disabled={sourceDownloading}
              label="Tải nguồn mới vào Thư viện"
            />
            <Button
              variant="contained"
              onClick={downloadSourceUrl}
              disabled={!sourceUrl.trim() || sourceDownloading}
              startIcon={sourceDownloading ? <CircularProgress size={16} color="inherit" /> : null}
              sx={{ whiteSpace: "nowrap", minWidth: 150 }}
            >
              {sourceDownloading ? "Đang tải…" : "Tải & dùng"}
            </Button>
          </Stack>
          <FormControl size="small" sx={{ minWidth: { xs: "100%", xl: 320 } }}>
            <InputLabel>Nguồn từ Thư viện</InputLabel>
            <Select value={sourceId} label="Nguồn từ Thư viện" onChange={(event) => chooseSource(event.target.value)} disabled={loading}>
              <MenuItem value=""><em>Chọn một nguồn đã tải</em></MenuItem>
              {sources.map((item) => <MenuItem key={item.id} value={item.id}>{item.title || item.id}</MenuItem>)}
            </Select>
          </FormControl>
          {source ? (
            <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
              <Chip label={source.platform || "cục bộ"} size="small" />
              {source.duration_seconds > 0 && <Chip label={`${source.duration_seconds}s`} size="small" variant="outlined" />}
              {source.media_metadata?.width && <Chip label={`${source.media_metadata.width}×${source.media_metadata.height}`} size="small" variant="outlined" />}
              {source.source_url && <Button size="small" href={source.source_url} target="_blank" rel="noopener">Mở nguồn</Button>}
            </Stack>
          ) : (
            <Button size="small" startIcon={<FolderOpenOutlinedIcon />} onClick={() => navigate("/library")}>Chọn từ Thư viện</Button>
          )}
        </Stack>
        {sourcesError && <Alert severity="warning" sx={{ mt: 1 }}
          action={<Button size="small" onClick={loadSources}>Thử lại</Button>}>
          {sourcesError}
        </Alert>}
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
