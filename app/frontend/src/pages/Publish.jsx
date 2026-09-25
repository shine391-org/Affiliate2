import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import {
  Alert, Box, Button, Chip, CircularProgress, Divider, LinearProgress,
  Paper, Stack, TextField, Typography,
} from "@mui/material"
import CloudUploadOutlinedIcon from "@mui/icons-material/CloudUploadOutlined"
import DownloadOutlinedIcon from "@mui/icons-material/DownloadOutlined"
import OpenInNewOutlinedIcon from "@mui/icons-material/OpenInNewOutlined"
import RefreshOutlinedIcon from "@mui/icons-material/RefreshOutlined"
import PageHero from "../components/PageHero"
import http from "../api/http"
import useDocumentTitle from "../hooks/useDocumentTitle"
import useAppStore from "../store/appStore"

const terminal = (status) => ["success", "failed", "cancelled"].includes(status)

function PlatformCard({ name, connected, connectLabel, onConnect, onPublish, busy, published, hint }) {
  return (
    <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
      <Stack direction="row" spacing={1} alignItems="center" useFlexGap flexWrap="wrap">
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>{name}</Typography>
        <Chip size="small" label={connected ? "ĐÃ KẾT NỐI" : "CHƯA KẾT NỐI"} color={connected ? "success" : "default"} />
        {published && <Chip size="small" label="ĐÃ XUẤT BẢN" color="success" variant="outlined" />}
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>{hint}</Typography>
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mt: 1.25 }}>
        {!connected && onConnect && (
          <Button variant="outlined" startIcon={<OpenInNewOutlinedIcon />} onClick={onConnect}>
            {connectLabel || "Connect"}
          </Button>
        )}
        <Button variant="contained" startIcon={<CloudUploadOutlinedIcon />}
          disabled={!connected || busy || published} onClick={onPublish}>
          {published ? "Published" : "Publish"}
        </Button>
      </Stack>
    </Paper>
  )
}

export default function Publish() {
  useDocumentTitle("Publish")
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [params, setParams] = useSearchParams()
  const [videos, setVideos] = useState([])
  const [settings, setSettings] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [job, setJob] = useState(null)
  const pollRef = useRef(null)

  const selectedId = params.get("video") || ""
  const requestedTarget = params.get("target") || ""
  const selected = useMemo(
    () => selectedId
      ? (videos.find((video) => video.id === selectedId) || null)
      : (videos[0] || null),
    [videos, selectedId],
  )

  const [title, setTitle] = useState("")
  const [caption, setCaption] = useState("")

  const load = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const [videoRes, settingsRes] = await Promise.all([
        http.get("/api/videos", { params: { limit: 100 } }),
        http.get("/api/settings"),
      ])
      let eligible = (videoRes.data?.videos || []).filter((video) =>
        ["ready", "uploaded"].includes(video.status) && video.video_path
      )
      setSettings(settingsRes.data || {})
      const requested = selectedId

      // Library can deep-link any GeneratedVideo, including one older than the
      // first 100 rows. Never substitute another asset for an explicit ID on an
      // irreversible Publish screen; resolve that exact row or show an error.
      if (requested && !eligible.some((video) => video.id === requested)) {
        try {
          const { data: requestedVideo } = await http.get(`/api/videos/${requested}`)
          const publishable = ["ready", "uploaded"].includes(requestedVideo?.status) && requestedVideo?.video_path
          if (!publishable) {
            setVideos(eligible)
            setError("That video is not a finished publishable output.")
            return
          }
          eligible = [requestedVideo, ...eligible]
        } catch (e) {
          setVideos(eligible)
          setError(e.response?.status === 404
            ? "That requested video no longer exists."
            : (e.response?.data?.detail || "Could not load the requested video."))
          return
        }
      }

      setVideos(eligible)
      if (!requested && eligible[0]) {
        setParams((prev) => {
          const next = new URLSearchParams(prev)
          next.set("video", eligible[0].id)
          return next
        }, { replace: true })
      }
    } catch (e) {
      setError(e.response?.data?.detail || "Could not load publish workspace.")
    } finally {
      setLoading(false)
    }
  }, [selectedId, setParams])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const onFocus = () => load()
    window.addEventListener("focus", onFocus)
    return () => window.removeEventListener("focus", onFocus)
  }, [load])
  useEffect(() => () => { if (pollRef.current) clearTimeout(pollRef.current) }, [])

  useEffect(() => {
    if (!selected) {
      setTitle("")
      setCaption("")
      return
    }
    const tiktokFirst = requestedTarget === "tiktok"
    setTitle(
      tiktokFirst
        ? (selected.tiktok_title || selected.youtube_title || selected.title || "")
        : (selected.youtube_title || selected.tiktok_title || selected.title || "")
    )
    setCaption(
      tiktokFirst
        ? (selected.tiktok_description || selected.youtube_description || "")
        : (selected.youtube_description || selected.tiktok_description || "")
    )
  }, [selected?.id, requestedTarget]) // eslint-disable-line react-hooks/exhaustive-deps

  const choose = (id) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set("video", id)
      return next
    })
    setJob(null)
    setError("")
  }

  const approve = async (approved) => {
    if (!selected) return
    setError("")
    try {
      await http.post(`/api/videos/${selected.id}/publish-approval`, { approved })
      await load()
      showSnackbar(approved ? "Approved for publishing" : "Publish approval revoked", approved ? "success" : "info")
    } catch (e) {
      setError(e.response?.data?.detail || "Could not update publish approval.")
    }
  }

  const connect = (route) => {
    setError("")
    // These native auth endpoints intentionally return HTTP redirects to the
    // provider. Let the browser follow them in a popup; fetching them as JSON
    // would turn the provider redirect into a CORS/error path.
    window.open(route, "_blank", "noopener,noreferrer")
  }

  const pollJob = (jobId) => {
    if (pollRef.current) clearTimeout(pollRef.current)
    const tick = async () => {
      try {
        const { data } = await http.get(`/api/jobs/${jobId}`)
        setJob(data)
        if (terminal(data.status)) {
          pollRef.current = null
          await load()
          if (data.status === "success") showSnackbar("Publish job completed", "success")
          return
        }
      } catch {
        pollRef.current = null
        return
      }
      pollRef.current = setTimeout(tick, 1800)
    }
    pollRef.current = setTimeout(tick, 800)
  }

  const publish = async (platform) => {
    if (!selected) return
    if (!selected.publish_approved) {
      setError("Approve this exact video before publishing.")
      return
    }
    if (!title.trim()) {
      setError("Title is required before publishing.")
      return
    }
    if (!window.confirm(`Publish this approved video to ${platform === "youtube" ? "YouTube" : "TikTok"} now?`)) return

    setError("")
    try {
      const patch = platform === "youtube"
        ? { youtube_title: title.trim(), youtube_description: caption.trim() }
        : { tiktok_title: title.trim(), tiktok_description: caption.trim() }
      await http.patch(`/api/videos/${selected.id}`, patch)
      const { data } = await http.post(`/api/videos/${selected.id}/upload`, { platforms: [platform] })
      setJob({ id: data.job_id, status: "pending", current_step: "Queued", progress_pct: 0 })
      pollJob(data.job_id)
    } catch (e) {
      setError(e.response?.data?.detail || `Could not publish to ${platform}.`)
    }
  }

  const active = job && !terminal(job.status)
  const uploaded = new Set(selected?.uploaded_platforms || [])

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <PageHero
        icon={<CloudUploadOutlinedIcon sx={{ fontSize: 22 }} />}
        title="Xuất bản"
        subtitle="One native owner for human-approved outputs and platform delivery"
        actions={<Button size="small" variant="outlined" startIcon={<RefreshOutlinedIcon />} onClick={load} disabled={loading}>Làm mới</Button>}
      />

      {error && <Alert severity="error" sx={{ mx: { xs: 1.5, md: 2.5 }, mt: 1 }}>{error}</Alert>}

      <Box sx={{
        flex: 1, minHeight: 0, display: "grid",
        gridTemplateColumns: { xs: "1fr", md: "320px minmax(0, 1fr)" },
        gridTemplateRows: { xs: "minmax(180px, 36%) minmax(0, 1fr)", md: "minmax(0, 1fr)" },
      }}>
        <Box sx={{ overflow: "auto", p: 2, borderRight: { md: 1 }, borderBottom: { xs: 1, md: 0 }, borderColor: "divider" }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 800, mb: 1 }}>Đầu ra đã hoàn tất</Typography>
          {loading && !videos.length ? (
            <Stack alignItems="center" sx={{ py: 5 }}><CircularProgress size={28} /></Stack>
          ) : videos.length === 0 ? (
            <Alert severity="info">Chưa có đầu ra đã kết xuất nào sẵn sàng để xuất bản.</Alert>
          ) : (
            <Stack spacing={1}>
              {videos.map((video) => (
                <Paper key={video.id} variant="outlined" onClick={() => choose(video.id)}
                  sx={{
                    p: 1.25, borderRadius: 2, cursor: "pointer",
                    borderColor: selected?.id === video.id ? "primary.main" : "divider",
                    bgcolor: selected?.id === video.id ? "action.selected" : "background.paper",
                  }}>
                  <Stack direction="row" spacing={0.75} alignItems="center">
                    <Typography variant="body2" sx={{ fontWeight: 800, flex: 1 }} noWrap>
                      {video.title || "Chưa đặt tên"}
                    </Typography>
                    {video.publish_approved && <Chip size="small" color="success" label="ĐÃ DUYỆT" />}
                  </Stack>
                  <Typography variant="caption" color="text.secondary">
                    {video.source_type || video.gen_tier || "generated"} · {video.aspect_ratio || "—"}
                  </Typography>
                  {!!video.uploaded_platforms?.length && (
                    <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap" sx={{ mt: 0.75 }}>
                      {video.uploaded_platforms.map((platform) => <Chip key={platform} size="small" variant="outlined" label={platform} />)}
                    </Stack>
                  )}
                </Paper>
              ))}
            </Stack>
          )}
        </Box>

        <Box sx={{ overflow: "auto", p: { xs: 1.5, md: 2.5 } }}>
          {!selected ? (
            <Alert severity="info">Chọn một đầu ra đã hoàn tất.</Alert>
          ) : (
            <Stack spacing={2}>
              <Stack direction={{ xs: "column", lg: "row" }} spacing={2}>
                <Box sx={{ width: { xs: "100%", lg: 280 }, flexShrink: 0 }}>
                  <Box sx={{
                    width: "100%", aspectRatio: selected.aspect_ratio === "16:9" ? "16 / 9" : "9 / 16",
                    maxHeight: 440, bgcolor: "black", borderRadius: 2, overflow: "hidden",
                  }}>
                    <video controls preload="metadata" src={`/api/videos/${selected.id}/stream`}
                      style={{ width: "100%", height: "100%", objectFit: "contain" }} />
                  </Box>
                  <Button component="a" href={`/api/videos/${selected.id}/stream`} download
                    fullWidth sx={{ mt: 1 }} variant="outlined" startIcon={<DownloadOutlinedIcon />}>
                    Download
                  </Button>
                </Box>

                <Stack spacing={1.25} sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="h6" sx={{ fontWeight: 800 }}>{selected.title || "Chưa đặt tên"}</Typography>
                  <Stack direction="row" spacing={0.75} useFlexGap flexWrap="wrap">
                    <Chip size="small" label={selected.status} />
                    <Chip size="small" variant="outlined" label={selected.source_type || "generated"} />
                    {selected.publish_approved
                      ? <Chip size="small" color="success" label="Đã được người dùng duyệt" />
                      : <Chip size="small" color="warning" label="Cần duyệt" />}
                  </Stack>

                  <Alert severity={selected.publish_approved ? "success" : "warning"} variant="outlined">
                    {selected.publish_approved
                      ? "Approval is recorded. Publishing still requires a separate platform button and confirmation."
                      : "Publishing is blocked until you explicitly approve this exact rendered file."}
                  </Alert>

                  <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                    <Button variant={selected.publish_approved ? "outlined" : "contained"}
                      color={selected.publish_approved ? "inherit" : "success"}
                      onClick={() => approve(!selected.publish_approved)}>
                      {selected.publish_approved ? "Revoke approval" : "Approve for publishing"}
                    </Button>
                  </Stack>

                  <TextField label="Tiêu đề xuất bản" value={title} onChange={(e) => setTitle(e.target.value)}
                    inputProps={{ maxLength: 150 }} helperText={`${title.length}/150`} fullWidth />
                  <TextField label="Caption / mô tả" value={caption} onChange={(e) => setCaption(e.target.value)}
                    multiline minRows={4} inputProps={{ maxLength: 5000 }} helperText={`${caption.length}/5000`} fullWidth />
                </Stack>
              </Stack>

              {job && (
                <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2 }}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Chip size="small" label={String(job.status || "").toUpperCase()}
                      color={job.status === "success" ? "success" : job.status === "failed" ? "error" : "info"} />
                    <Typography variant="body2" color="text.secondary">
                      {job.current_step || job.error_message || "Working…"}
                    </Typography>
                  </Stack>
                  {active && <LinearProgress variant={job.progress_pct ? "determinate" : "indeterminate"}
                    value={Math.min(100, Number(job.progress_pct || 0))} sx={{ mt: 1 }} />}
                  {job.error_message && <Alert severity="error" sx={{ mt: 1 }}>{job.error_message}</Alert>}
                </Paper>
              )}

              <Divider />

              <Typography variant="subtitle2" sx={{ fontWeight: 800 }}>Nền tảng đích</Typography>
              <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "1fr 1fr" }, gap: 1.5 }}>
                <PlatformCard
                  name="TikTok"
                  connected={Boolean(settings?.tiktok_upload_connected)}
                  connectLabel="Connect TikTok"
                  onConnect={() => connect("/api/settings/tiktok-upload-auth")}
                  onPublish={() => publish("tiktok")}
                  busy={Boolean(active) || !selected.publish_approved}
                  published={uploaded.has("tiktok")}
                  hint="Uses the canonical UploadAgent. OAuth is preferred; any legacy cookie fallback remains upload-only and does not own discovery."
                />
                <PlatformCard
                  name="YouTube"
                  connected={Boolean(settings?.youtube_connected)}
                  connectLabel="Connect YouTube"
                  onConnect={() => connect("/api/settings/youtube-auth")}
                  onPublish={() => publish("youtube")}
                  busy={Boolean(active) || !selected.publish_approved}
                  published={uploaded.has("youtube")}
                  hint="Uses the existing YouTube uploader and the encrypted account connection in Settings."
                />
              </Box>

              <Alert severity="info" variant="outlined">
                Facebook/Instagram and legacy overlay publication history are not being faked into this page. They stay transitional until their native adapters are migrated in Phase F.
              </Alert>
            </Stack>
          )}
        </Box>
      </Box>
    </Box>
  )
}
