import { useCallback, useEffect, useState } from "react"
import {
  Alert, Box, Button, Card, CardContent, Chip, Divider,
  LinearProgress, Link, Stack, TextField, Typography,
} from "@mui/material"
import RefreshOutlinedIcon from "@mui/icons-material/RefreshOutlined"
import SyncOutlinedIcon from "@mui/icons-material/SyncOutlined"
import OpenInNewOutlinedIcon from "@mui/icons-material/OpenInNewOutlined"
import http from "../../api/http"
import useDocumentTitle from "../../hooks/useDocumentTitle"

function fmt(value) {
  if (value === null || value === undefined) return "-"
  return Number(value).toLocaleString()
}

function pct(value) {
  if (value === null || value === undefined) return "-"
  return (Number(value) * 100).toFixed(2) + "%"
}

function MetricEditor({ publication, onSaved }) {
  const latest = publication.latest_metrics || {}
  const [views, setViews] = useState("")
  const [likes, setLikes] = useState("")
  const [comments, setComments] = useState("")
  const [shares, setShares] = useState("")
  const [note, setNote] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    setViews(latest.views ?? "")
    setLikes(latest.likes ?? "")
    setComments(latest.comments ?? "")
    setShares(latest.shares ?? "")
    setNote("")
  }, [publication.id, latest.id])

  const metric = (value) => {
    if (value === "") return null
    const parsed = Number(value)
    return Number.isFinite(parsed) ? Math.trunc(parsed) : null
  }

  const save = async () => {
    setBusy(true)
    setError("")
    try {
      await http.post("/api/performance/publications/" + publication.id + "/metrics", {
        views: metric(views),
        likes: metric(likes),
        comments: metric(comments),
        shares: metric(shares),
        note: note.trim() || null,
      })
      await onSaved()
    } catch (e) {
      setError(e.response?.data?.detail || "Could not save metrics.")
    } finally {
      setBusy(false)
    }
  }

  const syncYouTube = async () => {
    setBusy(true)
    setError("")
    try {
      await http.post("/api/performance/publications/" + publication.id + "/sync-youtube")
      await onSaved()
    } catch (e) {
      setError(e.response?.data?.detail || "Could not sync YouTube metrics.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Stack spacing={1.25}>
      {error && <Alert severity="error">{error}</Alert>}
      <Stack direction={{ xs: "column", md: "row" }} spacing={1}>
        <TextField size="small" type="number" label="Views" value={views} onChange={(e) => setViews(e.target.value)} fullWidth />
        <TextField size="small" type="number" label="Likes" value={likes} onChange={(e) => setLikes(e.target.value)} fullWidth />
        <TextField size="small" type="number" label="Comments" value={comments} onChange={(e) => setComments(e.target.value)} fullWidth />
        <TextField size="small" type="number" label="Shares" value={shares} onChange={(e) => setShares(e.target.value)} fullWidth />
      </Stack>
      <TextField size="small" label="Snapshot note (optional)" value={note} onChange={(e) => setNote(e.target.value)} fullWidth />
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
        <Button variant="contained" onClick={save} disabled={busy}>Save snapshot</Button>
        {publication.platform === "youtube" && (
          <Button variant="outlined" startIcon={<SyncOutlinedIcon />} onClick={syncYouTube} disabled={busy}>
            Sync YouTube
          </Button>
        )}
      </Stack>
    </Stack>
  )
}

export default function Performance() {
  useDocumentTitle("Performance")
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")


  const load = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const { data } = await http.get("/api/performance/summary")
      setSummary(data)
    } catch (e) {
      setError(e.response?.data?.detail || "Could not load performance feedback.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <Box sx={{ p: { xs: 2, md: 3 }, maxWidth: 1500, mx: "auto" }}>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" gap={2} sx={{ mb: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 800 }}>Performance Feedback</Typography>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Published outcomes become evidence for future content decisions. Radar weights stay human-controlled.
          </Typography>
        </Box>
        <Button variant="outlined" startIcon={<RefreshOutlinedIcon />} onClick={load} disabled={loading}>Refresh</Button>
      </Stack>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {loading && !summary && <LinearProgress sx={{ mb: 2 }} />}

      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mb: 2 }}>
        <Chip label={"Published " + (summary?.published_posts ?? 0)} />
        <Chip label={"With metrics " + (summary?.posts_with_metrics ?? 0)} />
        {Object.entries(summary?.by_platform || {}).map(([platform, counts]) => (
          <Chip key={platform} variant="outlined" label={platform + ": " + counts.with_metrics + "/" + counts.published + " measured"} />
        ))}
      </Stack>

      <Card variant="outlined" sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="subtitle1" sx={{ fontWeight: 800, mb: 1 }}>Observed patterns</Typography>
          {(summary?.patterns || []).length ? (
            <Stack spacing={0.75}>
              {summary.patterns.slice(0, 12).map((p) => (
                <Stack key={p.platform + ":" + p.dimension + ":" + p.value}
                  direction={{ xs: "column", md: "row" }} spacing={1} alignItems={{ xs: "flex-start", md: "center" }}>
                  <Chip size="small" label={p.platform} variant="outlined" />
                  <Chip size="small" label={p.dimension + ": " + p.value} />
                  <Typography variant="body2" color="text.secondary">
                    {"n=" + p.sample_count + " · avg views " + fmt(p.avg_views) + " · avg ER " + pct(p.avg_engagement_rate)}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          ) : (
            <Alert severity="info">No pattern evidence yet. Add metrics from published posts first.</Alert>
          )}
          <Alert severity="info" sx={{ mt: 1.5 }}>
            Descriptive evidence only. The system does not auto-change Radar weights from a small sample.
          </Alert>
        </CardContent>
      </Card>

      <Stack spacing={1.5}>
        {(summary?.posts || []).map((post) => (
          <Card key={post.id} variant="outlined">
            <CardContent>
              <Stack spacing={1.5}>
                <Stack direction={{ xs: "column", md: "row" }} spacing={1} alignItems={{ xs: "flex-start", md: "center" }}>
                  <Chip size="small" label={post.platform} color="primary" />
                  <Typography sx={{ fontWeight: 700 }}>{post.title || (post.source_platform + ":" + post.content_id)}</Typography>
                  {post.external_url && (
                    <Button size="small" component="a" href={post.external_url} target="_blank" rel="noreferrer"
                      endIcon={<OpenInNewOutlinedIcon fontSize="small" />}>Open post</Button>
                  )}
                </Stack>

                <Typography variant="caption" color="text.secondary">
                  {"Source " + post.source_platform + ":" + post.content_id}
                </Typography>

                <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                  <Chip size="small" label={"Views " + fmt(post.latest_metrics?.views)} />
                  <Chip size="small" label={"Likes " + fmt(post.latest_metrics?.likes)} />
                  <Chip size="small" label={"Comments " + fmt(post.latest_metrics?.comments)} />
                  <Chip size="small" label={"Shares " + fmt(post.latest_metrics?.shares)} />
                  <Chip size="small" label={"ER " + pct(post.derived?.engagement_rate)} />
                  <Chip size="small" label={"Velocity/h " + fmt(post.derived?.view_velocity_per_hour)} />
                  <Chip size="small" variant="outlined" label={"Snapshots " + (post.derived?.snapshot_count || 0)} />
                </Stack>

                <Divider />
                <MetricEditor publication={post} onSaved={load} />
              </Stack>
            </CardContent>
          </Card>
        ))}

        {!loading && !(summary?.posts || []).length && (
          <Alert severity="info">No published posts yet. Publish or mark a TikTok post in Review Queue first.</Alert>
        )}
      </Stack>
    </Box>
  )
}
