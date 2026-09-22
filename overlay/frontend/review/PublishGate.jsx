
import { useCallback, useEffect, useState } from "react"
import {
  Alert, Box, Button, Card, CardContent, Chip, Divider, FormControl,
  InputLabel, LinearProgress, Link, MenuItem, Select, Stack, TextField, Typography,
} from "@mui/material"
import CloudUploadOutlinedIcon from "@mui/icons-material/CloudUploadOutlined"
import ContentCopyOutlinedIcon from "@mui/icons-material/ContentCopyOutlined"
import DownloadOutlinedIcon from "@mui/icons-material/DownloadOutlined"
import OpenInNewOutlinedIcon from "@mui/icons-material/OpenInNewOutlined"
import RefreshOutlinedIcon from "@mui/icons-material/RefreshOutlined"
import http from "../../api/http"

const terminal = (s) => !["pending", "running"].includes(s)

function PlatformHeader({ label, mode, available }) {
  return (
    <Stack direction="row" spacing={1} alignItems="center" useFlexGap flexWrap="wrap">
      <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>{label}</Typography>
      <Chip size="small" label={mode} variant="outlined" />
      <Chip size="small" label={available ? "READY" : "NOT READY"} color={available ? "success" : "default"} />
    </Stack>
  )
}

function JobProgress({ job }) {
  if (!job) return null
  const active = !terminal(job.status)
  const progress = Number(job.progress_pct || 0)
  const color = job.status === "success" ? "success" : job.status === "failed" ? "error" : "info"
  return (
    <Box sx={{ mt: 1 }}>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: active ? 0.75 : 0 }}>
        <Chip size="small" label={String(job.status || "").toUpperCase()} color={color} />
        <Typography variant="body2" color="text.secondary">{job.current_step || job.error_message || ""}</Typography>
      </Stack>
      {active && (
        <LinearProgress variant={progress > 0 ? "determinate" : "indeterminate"} value={Math.min(100, progress)} />
      )}
      {job.error_message && <Alert severity="error" sx={{ mt: 1 }}>{job.error_message}</Alert>}
    </Box>
  )
}

export default function PublishGate({ detail }) {
  const [gate, setGate] = useState(null)
  const [title, setTitle] = useState("")
  const [caption, setCaption] = useState("")
  const [privacy, setPrivacy] = useState("private")
  const [audience, setAudience] = useState("")
  const [tiktokUrl, setTiktokUrl] = useState("")
  const [jobs, setJobs] = useState({})
  const [error, setError] = useState("")

  const loadGate = useCallback(async (preserve = true) => {
    if (!detail) return
    try {
      const url = "/api/publish/candidates/" + encodeURIComponent(detail.platform) + "/" + encodeURIComponent(detail.content_id)
      const { data } = await http.get(url)
      setGate(data)
      if (!preserve || (!title && !caption)) {
        setTitle(data.metadata?.title || "")
        setCaption(data.metadata?.caption || "")
      }
    } catch (e) {
      setError(e.response?.data?.detail || "Could not load Publish Gate.")
    }
  }, [detail, title, caption])

  useEffect(() => {
    setGate(null); setTitle(""); setCaption(""); setPrivacy("private"); setAudience("")
    setTikTokUrl(""); setJobs({}); setError("")
    if (detail) loadGate(false)
  }, [detail?.platform, detail?.content_id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onFocus = () => loadGate(true)
    window.addEventListener("focus", onFocus)
    return () => window.removeEventListener("focus", onFocus)
  }, [loadGate])

  const pollJob = (platform, jobId) => {
    const timer = setInterval(async () => {
      try {
        const { data } = await http.get("/api/jobs/" + encodeURIComponent(jobId))
        setJobs((prev) => ({ ...prev, [platform]: data }))
        if (terminal(data.status)) {
          clearInterval(timer)
          await loadGate(true)
        }
      } catch {
        clearInterval(timer)
      }
    }, 1800)
  }

  const active = Object.values(jobs).some((j) => j && !terminal(j.status))

  const connectYouTube = async () => {
    setError("")
    try {
      const { data } = await http.get("/api/publish/youtube/connect")
      window.open(data.authorization_url, "_blank", "noopener,noreferrer")
    } catch (e) {
      setError(e.response?.data?.detail || "Could not start YouTube authorization.")
    }
  }

  const publish = async (platform) => {
    if (!title.trim()) return setError("Title is required before publishing.")
    if (platform === "youtube" && !audience) return setError("Choose the YouTube audience setting first.")
    const target = platform === "youtube" ? "YouTube" : "Facebook Page"
    const extra = platform === "youtube" ? " (" + privacy + ")" : ""
    if (!window.confirm("Publish this approved final to " + target + extra + " now?")) return
    setError("")
    try {
      const url = "/api/publish/candidates/" + encodeURIComponent(detail.platform) + "/" + encodeURIComponent(detail.content_id)
      const { data } = await http.post(url, {
        platform,
        title: title.trim(),
        caption: caption.trim(),
        privacy: platform === "youtube" ? privacy : null,
        made_for_kids: audience === "made_for_kids",
      })
      const job = { id: data.job_id, status: "pending", current_step: "Queued", progress_pct: 0 }
      setJobs((prev) => ({ ...prev, [platform]: job }))
      pollJob(platform, data.job_id)
    } catch (e) {
      setError(e.response?.data?.detail || "Could not publish to " + platform + ".")
    }
  }

  const markTikTok = async () => {
    setError("")
    try {
      const url = "/api/publish/candidates/" + encodeURIComponent(detail.platform) + "/" + encodeURIComponent(detail.content_id) + "/tiktok-posted"
      await http.post(url, { title: title.trim(), caption: caption.trim(), external_url: tiktokUrl.trim() })
      setTikTokUrl("")
      await loadGate(true)
    } catch (e) {
      setError(e.response?.data?.detail || "Could not record TikTok post.")
    }
  }

  if (!detail) return null

  const fb = gate?.platforms?.facebook || {}
  const yt = gate?.platforms?.youtube || {}
  const finalUrl = "/api/review/media/final/" + encodeURIComponent(detail.platform) + "/" + encodeURIComponent(detail.content_id)

  return (
    <Card variant="outlined" sx={{ borderColor: "success.light" }}>
      <CardContent>
        <Stack spacing={2.25}>
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 800 }}>Publish Gate</Typography>
            <Typography variant="body2" color="text.secondary">
              Final is approved. Publishing is still a separate human action per platform.
            </Typography>
          </Box>

          {error && <Alert severity="error">{error}</Alert>}
          {!gate && <LinearProgress />}

          <TextField label="Title" value={title} onChange={(e) => setTitle(e.target.value)}
            inputProps={{ maxLength: 100 }} helperText={title.length + "/100"} fullWidth />
          <TextField label="Caption / description" value={caption} onChange={(e) => setCaption(e.target.value)}
            multiline minRows={4} inputProps={{ maxLength: 5000 }}
            helperText={caption.length + "/5000"} fullWidth />

          <Divider />

          <Box>
            <PlatformHeader label="TikTok" mode="Manual" available />
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75, mb: 1.25 }}>
              No TikTok API upload. Download the final, copy the caption, then post manually in TikTok.
            </Typography>
            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
              <Button component="a" href={finalUrl} download variant="contained" startIcon={<DownloadOutlinedIcon />}>
                Download final
              </Button>
              <Button variant="outlined" startIcon={<ContentCopyOutlinedIcon />}
                onClick={() => navigator.clipboard.writeText(caption).catch(() => setError("Clipboard access failed."))}>
                Copy caption
              </Button>
            </Stack>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1} sx={{ mt: 1.5 }}>
              <TextField size="small" label="TikTok post URL after manual publish" value={tiktokUrl}
                onChange={(e) => setTiktokUrl(e.target.value)} fullWidth />
              <Button variant="outlined" onClick={markTikTok} disabled={!tiktokUrl.trim()}>Mark posted</Button>
            </Stack>
          </Box>

          <Divider />

          <Box>
            <PlatformHeader label="Facebook Page Reel" mode="API" available={Boolean(fb.available)} />
            {!fb.available && <Alert severity="info" sx={{ mt: 1 }}>{fb.reason || "Facebook publishing is not ready."}</Alert>}
            {gate?.media?.duration && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
                Final: {gate.media.width}×{gate.media.height} · {Number(gate.media.duration).toFixed(1)}s
              </Typography>
            )}
            <Button sx={{ mt: 1.25 }} variant="contained" startIcon={<CloudUploadOutlinedIcon />}
              onClick={() => publish("facebook")} disabled={!fb.available || active}>
              Publish to Facebook
            </Button>
            <JobProgress job={jobs.facebook} />
          </Box>

          <Divider />

          <Box>
            <PlatformHeader label="YouTube" mode="API" available={Boolean(yt.available)} />
            {!yt.oauth_configured && <Alert severity="info" sx={{ mt: 1 }}>{yt.reason || "Configure YouTube OAuth first."}</Alert>}
            {yt.oauth_configured && !yt.connected && (
              <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                <Button variant="outlined" startIcon={<OpenInNewOutlinedIcon />} onClick={connectYouTube}>Connect YouTube</Button>
                <Button variant="text" startIcon={<RefreshOutlinedIcon />} onClick={() => loadGate(true)}>Refresh</Button>
              </Stack>
            )}
            {yt.connected && (
              <Stack spacing={1.25} sx={{ mt: 1.25 }}>
                <FormControl size="small" fullWidth>
                  <InputLabel id="youtube-privacy-label">Privacy</InputLabel>
                  <Select labelId="youtube-privacy-label" label="Privacy" value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
                    <MenuItem value="private">Private</MenuItem>
                    <MenuItem value="unlisted">Unlisted</MenuItem>
                    <MenuItem value="public">Public</MenuItem>
                  </Select>
                </FormControl>
                <FormControl size="small" fullWidth>
                  <InputLabel id="youtube-audience-label">Audience</InputLabel>
                  <Select labelId="youtube-audience-label" label="Audience" value={audience} onChange={(e) => setAudience(e.target.value)}>
                    <MenuItem value="not_made_for_kids">Not made for kids</MenuItem>
                    <MenuItem value="made_for_kids">Made for kids</MenuItem>
                  </Select>
                </FormControl>
                <Alert severity="info">
                  Unverified YouTube API projects can have API uploads restricted to private until Google completes an audit.
                </Alert>
                <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                  <Button variant="contained" startIcon={<CloudUploadOutlinedIcon />}
                    onClick={() => publish("youtube")} disabled={active || !audience}>
                    Publish to YouTube
                  </Button>
                  <Button variant="text" color="inherit" onClick={async () => {
                    await http.delete("/api/publish/youtube/disconnect")
                    await loadGate(true)
                  }}>Disconnect</Button>
                </Stack>
                <JobProgress job={jobs.youtube} />
              </Stack>
            )}
          </Box>

          <Divider />

          <Box>
            <Typography variant="subtitle2" sx={{ fontWeight: 800, mb: 1 }}>Publish history</Typography>
            <Stack spacing={0.75}>
              {(gate?.publications || []).slice(0, 6).map((p) => (
                <Stack key={p.id} direction="row" spacing={1} alignItems="center" useFlexGap flexWrap="wrap">
                  <Chip size="small" label={p.platform} variant="outlined" />
                  <Chip size="small" label={p.status} color={p.status === "PUBLISHED" ? "success" : p.status === "FAILED" ? "error" : "info"} />
                  {p.external_url && <Link href={p.external_url} target="_blank" rel="noreferrer" variant="caption">Open post</Link>}
                  {p.error_message && <Typography variant="caption" color="error">{p.error_message}</Typography>}
                </Stack>
              ))}
            </Stack>
          </Box>
        </Stack>
      </CardContent>
    </Card>
  )
}
