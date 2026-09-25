import { useCallback, useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import {
  Alert, Box, Button, Card, CardContent, Chip, LinearProgress,
  Stack, TextField, Typography,
} from "@mui/material"
import RefreshOutlinedIcon from "@mui/icons-material/RefreshOutlined"
import http from "../../api/http"
import useDocumentTitle from "../../hooks/useDocumentTitle"
import { formatMetric, performanceDecision } from "./performanceModel.js"

const fields = ["views", "likes", "comments", "shares"]

function SnapshotEditor({ post, reload }) {
  const [metrics, setMetrics] = useState({ views: "", likes: "", comments: "", shares: "" })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  useEffect(() => {
    setMetrics(Object.fromEntries(fields.map((field) => [field, post.latest_metrics?.[field] ?? ""])))
  }, [post.generated_video_id, post.platform, post.latest_metrics?.fetched_at])

  const save = async (sync = false) => {
    setBusy(true)
    setError("")
    try {
      const route = `/api/videos/${post.generated_video_id}/performance/${sync ? "sync" : "metrics"}`
      const payload = { platform: post.platform }
      if (!sync) {
        for (const field of fields) {
          const value = metrics[field]
          if (value !== "" && (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)))) {
            throw new Error(`${field} must be a non-negative integer`)
          }
          payload[field] = value === "" ? null : Number(value)
        }
        if (fields.every((field) => payload[field] === null)) throw new Error("Enter at least one metric")
      }
      await http.post(route, sync ? {} : payload)
      await reload()
    } catch (e) {
      setError(e.response?.data?.detail || e.message || "Không thể lưu snapshot")
    } finally {
      setBusy(false)
    }
  }

  return <Stack spacing={1}>
    {error && <Alert severity="error">{error}</Alert>}
    <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
      {fields.map((field) => <TextField key={field} size="small" type="number" label={field}
        value={metrics[field]} inputProps={{ min: 0, step: 1 }} fullWidth
        onChange={(event) => setMetrics((prev) => ({ ...prev, [field]: event.target.value }))} />)}
    </Stack>
    <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
      <Button variant="outlined" disabled={busy} onClick={() => save(false)}>Ghi snapshot thủ công</Button>
      {post.platform === "youtube" && post.platform_content_id &&
        <Button variant="outlined" disabled={busy} onClick={() => save(true)}>Đồng bộ YouTube</Button>}
    </Stack>
  </Stack>
}

export default function Performance() {
  useDocumentTitle("Performance")
  const [params] = useSearchParams()
  const selectedId = params.get("video")
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const load = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const { data } = await http.get("/api/videos/performance/overview")
      setSummary(data)
    } catch (e) {
      setError(e.response?.data?.detail || "Could not load native performance data")
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  const posts = summary?.posts || []
  const visible = selectedId ? posts.filter((post) => post.generated_video_id === selectedId) : posts
  return <Box sx={{ p: { xs: 2, md: 3 }, maxWidth: 1200, mx: "auto" }}>
    <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" gap={1} sx={{ mb: 2 }}>
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 800 }}>Hiệu suất</Typography>
        <Typography color="text.secondary">Kết quả đo từ đúng nội dung đã xuất bản trong Thư viện. Không tự động thay đổi nội dung.</Typography>
      </Box>
      <Button variant="outlined" startIcon={<RefreshOutlinedIcon />} disabled={loading} onClick={load}>Làm mới</Button>
    </Stack>
    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
    {loading && !summary && <LinearProgress />}
    {summary && <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ my: 2 }}>
      <Chip label={`Bài đã xuất bản ${summary.published_posts}`} />
      <Chip label={`Đã đo ${summary.posts_with_metrics}`} />
      {Object.entries(summary.by_platform || {}).map(([platform, counts]) =>
        <Chip key={platform} variant="outlined" label={`${platform}: ${counts.with_metrics}/${counts.published} đã đo · ${formatMetric(counts.views)} lượt xem`} />)}
    </Stack>}
    {selectedId && !visible.length && !loading && <Alert severity="warning">Video này chưa có nền tảng xuất bản. <a href={`/publish?video=${encodeURIComponent(selectedId)}`}>Mở Xuất bản</a></Alert>}
    {!selectedId && !posts.length && !loading && <Alert severity="info">Chưa có video nào được xuất bản. Hãy duyệt nội dung trong Xuất bản trước.</Alert>}
    {summary?.top_posts?.length > 0 && !selectedId && <Card variant="outlined" sx={{ mb: 2 }}><CardContent>
      <Typography fontWeight={700}>Các bài có lượt xem cao nhất</Typography>
      <Typography variant="body2" color="text.secondary">Lượt xem là số đo thô, không phải điểm xếp hạng chắc chắn. Hãy so sánh các bài cùng nền tảng và độ tuổi trước khi lặp lại định dạng.</Typography>
      {summary.top_posts.map((post) => <Typography key={`${post.generated_video_id}:${post.platform}`} variant="body2">
        {post.platform} · {post.title || post.generated_video_id} · {formatMetric(post.latest_metrics.views)} views
      </Typography>)}
    </CardContent></Card>}
    <Stack spacing={2}>
      {visible.map((post) => <Card key={`${post.generated_video_id}:${post.platform}`} variant="outlined"><CardContent>
        <Stack spacing={1.5}>
          <Stack direction="row" alignItems="center" spacing={1} useFlexGap flexWrap="wrap">
            {post.thumbnail_url && <img src={post.thumbnail_url} alt="" width="56" height="56" style={{ objectFit: "cover", borderRadius: 6 }} />}
            <Typography fontWeight={700}>{post.title || post.generated_video_id}</Typography>
            <Chip size="small" label={post.platform} />
            <Chip size="small" label={post.state.replaceAll("_", " ")} variant="outlined" />
          </Stack>
          <Typography variant="caption" color="text.secondary">
            {post.platform === "tiktok" ? "Publish transaction" : "Post ID"}: {post.platform_content_id || "unavailable"} · Published: {post.published_at || "timestamp unavailable"} · Last sync: {post.latest_metrics?.fetched_at || "never"}
          </Typography>
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
            {fields.map((field) => <Chip key={field} size="small" label={`${field}: ${formatMetric(post.latest_metrics?.[field])}`} />)}
            <Chip size="small" label={`Tương tác đã biết / lượt xem: ${post.latest_metrics?.engagement_rate == null ? "–" : (post.latest_metrics.engagement_rate * 100).toFixed(2) + "%"}`} />
          </Stack>
          <Typography variant="body2">{performanceDecision(post)}</Typography>
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
            <Button size="small" component="a" href={`/publish?video=${encodeURIComponent(post.generated_video_id)}`}>Xem nội dung đã xuất bản</Button>
            <Button size="small" component="a" href={`/library?open=${encodeURIComponent(`generated:${post.generated_video_id}`)}`}>Mở trong Thư viện</Button>
            {post.source_downloaded_video_id && <Button size="small" component="a" href={`/studio?source=${encodeURIComponent(post.source_downloaded_video_id)}`}>Xem nguồn trong Studio</Button>}
          </Stack>
          <SnapshotEditor post={post} reload={load} />
        </Stack>
      </CardContent></Card>)}
    </Stack>
  </Box>
}
