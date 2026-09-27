import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import {
  Alert, Box, Button, Checkbox, Chip, CircularProgress, Divider, FormControl,
  FormControlLabel, InputLabel, LinearProgress, MenuItem, Paper, Select, Stack,
  TextField, Typography,
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
const STATUS_LABELS = { ready: "Sẵn sàng", uploaded: "Đã tải lên", pending: "Đang chờ", running: "Đang chạy", success: "Hoàn tất", failed: "Thất bại", cancelled: "Đã hủy" }
const SOURCE_LABELS = { generated: "Đã tạo", smart_video: "Video thông minh", clip_extraction: "Clip", smart_cut: "Cắt thông minh", motion_graphics: "Đồ họa chuyển động" }

function PlatformCard({ name, connected, reconnect = false, connectLabel, onConnect, onPublish, busy, published, hint, children }) {
  return (
    <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
      <Stack direction="row" spacing={1} alignItems="center" useFlexGap flexWrap="wrap">
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>{name}</Typography>
        <Chip size="small" label={connected ? "ĐÃ KẾT NỐI" : "CHƯA KẾT NỐI"} color={connected ? "success" : "default"} />
        {published && <Chip size="small" label="ĐÃ XUẤT BẢN" color="success" variant="outlined" />}
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>{hint}</Typography>
      {children}
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mt: 1.25 }}>
        {(!connected || reconnect) && onConnect && (
          <Button variant="outlined" startIcon={<OpenInNewOutlinedIcon />} onClick={onConnect}>
            {connectLabel || "Kết nối"}
          </Button>
        )}
        <Button variant="contained" startIcon={<CloudUploadOutlinedIcon />}
          disabled={!connected || busy || published} onClick={onPublish}>
          {published ? "Đã xuất bản" : "Xuất bản"}
        </Button>
      </Stack>
    </Paper>
  )
}

export default function Publish() {
  useDocumentTitle("Xuất bản")
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
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
  const [tiktokCreator, setTikTokCreator] = useState(null)
  const [tiktokCreatorLoading, setTikTokCreatorLoading] = useState(false)
  const [tiktokCreatorError, setTikTokCreatorError] = useState("")
  const [tiktokPrivacy, setTikTokPrivacy] = useState("")
  const [tiktokDisableComment, setTikTokDisableComment] = useState(false)
  const [tiktokDisableDuet, setTikTokDisableDuet] = useState(false)
  const [tiktokDisableStitch, setTikTokDisableStitch] = useState(false)
  const [tiktokCreatorRefreshKey, setTikTokCreatorRefreshKey] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const [videoRes, settingsRes] = await Promise.all([
        http.get("/api/videos", { params: { limit: 100 } }),
        http.get("/api/settings"),
      ])
      let eligible = (videoRes.data?.videos || []).filter((video) =>
        ["ready", "uploaded"].includes(video.status) && video.video_path && !video.artifact_missing
      )
      setSettings(settingsRes.data || {})
      const requested = selectedId

      // Library can deep-link any GeneratedVideo, including one older than the
      // first 100 rows. Never substitute another asset for an explicit ID on an
      // irreversible Publish screen; resolve that exact row or show an error.
      if (requested && !eligible.some((video) => video.id === requested)) {
        try {
          const { data: requestedVideo } = await http.get(`/api/videos/${requested}`)
          const publishable = ["ready", "uploaded"].includes(requestedVideo?.status) && requestedVideo?.video_path && !requestedVideo?.artifact_missing
          if (!publishable) {
            setVideos(eligible)
            setError("Video này chưa phải đầu ra hoàn tất có thể xuất bản.")
            return
          }
          eligible = [requestedVideo, ...eligible]
        } catch (e) {
          setVideos(eligible)
          setError(e.response?.status === 404
            ? "Video được yêu cầu không còn tồn tại."
            : (e.response?.data?.detail || "Không thể tải video được yêu cầu."))
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
      setError(e.response?.data?.detail || "Không thể tải không gian Xuất bản.")
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
    let alive = true
    if (!settings?.tiktok_upload_connected) {
      setTikTokCreator(null)
      setTikTokCreatorError("")
      setTikTokPrivacy("")
      return undefined
    }

    setTikTokCreatorLoading(true)
    setTikTokCreatorError("")
    http.get("/api/settings/tiktok-creator-info")
      .then(({ data }) => {
        if (!alive) return
        const options = data?.privacy_level_options || []
        const preferred = settings?.tiktok_default_privacy || ""
        setTikTokCreator(data || null)
        setTikTokPrivacy(options.includes(preferred) ? preferred : (options[0] || ""))
        setTikTokDisableComment(Boolean(data?.comment_disabled))
        setTikTokDisableDuet(Boolean(data?.duet_disabled))
        setTikTokDisableStitch(Boolean(data?.stitch_disabled))
      })
      .catch((e) => {
        if (!alive) return
        setTikTokCreator(null)
        setTikTokCreatorError(
          e.response?.data?.detail || "Không thể đọc quyền Direct Post của tài khoản TikTok."
        )
      })
      .finally(() => { if (alive) setTikTokCreatorLoading(false) })

    return () => { alive = false }
  }, [settings?.tiktok_upload_connected, settings?.tiktok_default_privacy, tiktokCreatorRefreshKey])

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
      showSnackbar(approved ? "Đã duyệt để xuất bản" : "Đã thu hồi duyệt xuất bản", approved ? "success" : "info")
    } catch (e) {
      setError(e.response?.data?.detail || "Không thể cập nhật trạng thái duyệt xuất bản.")
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
          if (data.status === "success") showSnackbar("Tác vụ xuất bản đã hoàn tất", "success")
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

  const deleteVideo = async () => {
    if (!selected) return
    if (!window.confirm(`Bạn có chắc chắn muốn xóa video "${selected.title || selected.id}" không? Thao tác này không thể hoàn tác.`)) return
    try {
      await http.delete(`/api/videos/${selected.id}`)
      showSnackbar("Đã xóa video thành công.", "success")
      setParams((prev) => {
        const next = new URLSearchParams(prev)
        next.delete("video")
        return next
      })
      await load()
    } catch (e) {
      showSnackbar(e.response?.data?.detail || "Không thể xóa video", "error")
    }
  }

  const publish = async (platform) => {
    if (!selected) return
    if (!selected.publish_approved) {
      setError("Hãy duyệt đúng video này trước khi xuất bản.")
      return
    }
    if (!title.trim()) {
      setError("Cần có tiêu đề trước khi xuất bản.")
      return
    }
    if (platform === "tiktok" && (!tiktokCreator || !tiktokPrivacy)) {
      setError("Chưa đọc được quyền Direct Post/privacy của TikTok. Làm mới hoặc kết nối lại TikTok.")
      return
    }
    const targetLabel = platform === "youtube"
      ? "YouTube"
      : platform === "tiktok"
        ? `TikTok (${tiktokPrivacy})`
        : platform === "instagram"
          ? "Instagram Reels"
          : "Facebook Page Reels"
    if (!window.confirm(`Xuất bản video đã duyệt này lên ${targetLabel} ngay bây giờ?`)) return

    setError("")
    try {
      if (platform === "youtube") {
        await http.patch(`/api/videos/${selected.id}`, {
          youtube_title: title.trim(),
          youtube_description: caption.trim(),
        })
      } else if (platform === "tiktok") {
        await http.patch(`/api/videos/${selected.id}`, {
          tiktok_title: title.trim(),
          tiktok_description: caption.trim(),
        })
      }

      const publishOptions = platform === "tiktok"
        ? {
            tiktok: {
              privacy_level: tiktokPrivacy,
              disable_comment: Boolean(tiktokDisableComment || tiktokCreator?.comment_disabled),
              disable_duet: Boolean(tiktokDisableDuet || tiktokCreator?.duet_disabled),
              disable_stitch: Boolean(tiktokDisableStitch || tiktokCreator?.stitch_disabled),
            },
          }
        : ["facebook", "instagram"].includes(platform)
          ? { [platform]: { title: title.trim(), caption: caption.trim() } }
          : {}
      const { data } = await http.post(`/api/videos/${selected.id}/upload`, {
        platforms: [platform],
        publish_options: publishOptions,
      })
      setJob({ id: data.job_id, status: "pending", current_step: "Đã xếp hàng", progress_pct: 0 })
      pollJob(data.job_id)
    } catch (e) {
      setError(e.response?.data?.detail || `Không thể xuất bản lên ${platform}.`)
    }
  }

  const active = job && !terminal(job.status)
  const uploaded = new Set(selected?.uploaded_platforms || [])

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <PageHero
        icon={<CloudUploadOutlinedIcon sx={{ fontSize: 22 }} />}
        title="Xuất bản"
        subtitle="Một luồng chuẩn cho đầu ra đã được người dùng duyệt và phân phối lên nền tảng"
        actions={<Button
          size="small"
          variant="outlined"
          startIcon={<RefreshOutlinedIcon />}
          onClick={() => {
            load()
            setTikTokCreatorRefreshKey((value) => value + 1)
          }}
          disabled={loading}
        >Làm mới</Button>}
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
                    Tải về
                  </Button>
                </Box>

                <Stack spacing={1.25} sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="h6" sx={{ fontWeight: 800 }}>{selected.title || "Chưa đặt tên"}</Typography>
                  <Stack direction="row" spacing={0.75} useFlexGap flexWrap="wrap">
                    <Chip size="small" label={STATUS_LABELS[selected.status] || selected.status} />
                    <Chip size="small" variant="outlined" label={SOURCE_LABELS[selected.source_type] || selected.source_type || "Đã tạo"} />
                    {selected.publish_approved
                      ? <Chip size="small" color="success" label="Đã được người dùng duyệt" />
                      : <Chip size="small" color="warning" label="Cần duyệt" />}
                  </Stack>

                  <Alert severity={selected.publish_approved ? "success" : "warning"} variant="outlined">
                    {selected.publish_approved
                      ? "Đã ghi nhận phê duyệt. Việc xuất bản vẫn cần bấm nút nền tảng riêng và xác nhận."
                      : "Xuất bản bị khóa cho đến khi bạn duyệt rõ ràng đúng tệp đã kết xuất này."}
                  </Alert>

                  <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                    <Button variant={selected.publish_approved ? "outlined" : "contained"}
                      color={selected.publish_approved ? "inherit" : "success"}
                      onClick={() => approve(!selected.publish_approved)}>
                      {selected.publish_approved ? "Thu hồi duyệt" : "Duyệt để xuất bản"}
                    </Button>
                    <Button variant="outlined" color="error" onClick={deleteVideo}>
                      Xóa video
                    </Button>
                  </Stack>

                  <TextField label="Tiêu đề xuất bản" value={title} onChange={(e) => setTitle(e.target.value)}
                    inputProps={{ maxLength: 150 }} helperText={`${title.length}/150`} fullWidth />
                  <TextField label="Nội dung mô tả" value={caption} onChange={(e) => setCaption(e.target.value)}
                    multiline minRows={4} inputProps={{ maxLength: 5000 }} helperText={`${caption.length}/5000`} fullWidth />
                </Stack>
              </Stack>

              {job && (
                <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2 }}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Chip size="small" label={STATUS_LABELS[job.status] || String(job.status || "").toUpperCase()}
                      color={job.status === "success" ? "success" : job.status === "failed" ? "error" : "info"} />
                    <Typography variant="body2" color="text.secondary">
                      {job.current_step || job.error_message || "Đang xử lý…"}
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
                  reconnect={Boolean(tiktokCreatorError)}
                  connectLabel={tiktokCreatorError ? "Kết nối lại TikTok OAuth" : "Kết nối TikTok OAuth"}
                  onConnect={() => connect("/api/settings/tiktok-upload-auth")}
                  onPublish={() => publish("tiktok")}
                  busy={Boolean(active) || !selected.publish_approved || tiktokCreatorLoading || !tiktokCreator || !tiktokPrivacy}
                  published={uploaded.has("tiktok")}
                  hint="Direct Post chính thức qua TikTok OAuth. Affiliate2 đọc capability mới nhất của creator trước khi đăng; cookie legacy không được coi là kết nối xuất bản."
                >
                  <Stack spacing={1} sx={{ mt: 1.25 }}>
                    {settings?.tiktok_legacy_cookie_present && !settings?.tiktok_upload_connected && (
                      <Alert severity="warning" variant="outlined">
                        Có cookie TikTok cũ nhưng canonical Publish không dùng nó vì không thể xác minh kết quả đăng. Hãy kết nối lại bằng OAuth.
                      </Alert>
                    )}
                    {tiktokCreatorLoading && (
                      <Stack direction="row" spacing={1} alignItems="center">
                        <CircularProgress size={16} />
                        <Typography variant="caption" color="text.secondary">Đang đọc quyền Direct Post…</Typography>
                      </Stack>
                    )}
                    {tiktokCreatorError && (
                      <Alert
                        severity="warning"
                        variant="outlined"
                        action={
                          <Button
                            color="inherit"
                            size="small"
                            onClick={() => setTikTokCreatorRefreshKey((value) => value + 1)}
                          >
                            Thử lại
                          </Button>
                        }
                      >
                        {tiktokCreatorError}
                      </Alert>
                    )}
                    {tiktokCreator && (
                      <>
                        <Typography variant="caption" color="text.secondary">
                          Tài khoản: <strong>{tiktokCreator.creator_nickname || tiktokCreator.creator_username || "TikTok creator"}</strong>
                          {tiktokCreator.creator_username ? ` (@${tiktokCreator.creator_username})` : ""}
                        </Typography>
                        <FormControl size="small" fullWidth>
                          <InputLabel>Quyền riêng tư TikTok</InputLabel>
                          <Select
                            value={tiktokPrivacy}
                            label="Quyền riêng tư TikTok"
                            onChange={(e) => setTikTokPrivacy(e.target.value)}
                          >
                            {(tiktokCreator.privacy_level_options || []).map((option) => (
                              <MenuItem key={option} value={option}>{option}</MenuItem>
                            ))}
                          </Select>
                        </FormControl>
                        <Stack direction={{ xs: "column", sm: "row" }} spacing={0.5} useFlexGap flexWrap="wrap">
                          <FormControlLabel
                            control={<Checkbox size="small" checked={Boolean(tiktokDisableComment || tiktokCreator.comment_disabled)} onChange={(e) => setTikTokDisableComment(e.target.checked)} disabled={Boolean(tiktokCreator.comment_disabled)} />}
                            label="Tắt bình luận"
                          />
                          <FormControlLabel
                            control={<Checkbox size="small" checked={Boolean(tiktokDisableDuet || tiktokCreator.duet_disabled)} onChange={(e) => setTikTokDisableDuet(e.target.checked)} disabled={Boolean(tiktokCreator.duet_disabled)} />}
                            label="Tắt Duet"
                          />
                          <FormControlLabel
                            control={<Checkbox size="small" checked={Boolean(tiktokDisableStitch || tiktokCreator.stitch_disabled)} onChange={(e) => setTikTokDisableStitch(e.target.checked)} disabled={Boolean(tiktokCreator.stitch_disabled)} />}
                            label="Tắt Stitch"
                          />
                        </Stack>
                        {tiktokCreator.max_video_post_duration_sec && (
                          <Typography variant="caption" color="text.secondary">
                            Giới hạn video của creator: {tiktokCreator.max_video_post_duration_sec}s.
                          </Typography>
                        )}
                      </>
                    )}
                  </Stack>
                </PlatformCard>
                <PlatformCard
                  name="YouTube"
                  connected={Boolean(settings?.youtube_connected)}
                  connectLabel="Kết nối YouTube"
                  onConnect={() => connect("/api/settings/youtube-auth")}
                  onPublish={() => publish("youtube")}
                  busy={Boolean(active) || !selected.publish_approved}
                  published={uploaded.has("youtube")}
                  hint="Dùng uploader YouTube hiện có và kết nối tài khoản đã mã hóa trong Cài đặt."
                />
                <PlatformCard
                  name="Facebook Page"
                  connected={Boolean(settings?.facebook_page_connected)}
                  connectLabel="Mở Cài đặt Meta"
                  onConnect={() => navigate("/settings?section=services")}
                  onPublish={() => publish("facebook")}
                  busy={Boolean(active) || !selected.publish_approved}
                  published={uploaded.has("facebook")}
                  hint="Đăng Reel trực tiếp lên Facebook Page đã xác minh. Không hỗ trợ profile cá nhân."
                >
                  {settings?.meta_page_name && (
                    <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
                      Page: <strong>{settings.meta_page_name}</strong>
                    </Typography>
                  )}
                </PlatformCard>
                <PlatformCard
                  name="Instagram"
                  connected={Boolean(settings?.instagram_connected)}
                  connectLabel="Mở Cài đặt Meta"
                  onConnect={() => navigate("/settings?section=services")}
                  onPublish={() => publish("instagram")}
                  busy={Boolean(active) || !selected.publish_approved}
                  published={uploaded.has("instagram")}
                  hint="Đăng Reel lên Instagram Professional (Business/Creator) liên kết với Facebook Page."
                />
              </Box>

              <Alert severity="info" variant="outlined">
                Facebook chỉ hỗ trợ Page; Instagram chỉ hỗ trợ tài khoản Professional liên kết Page.
                Cả hai dùng Meta Page access token đã xác minh trong Cài đặt và upload file trực tiếp lên Meta.
              </Alert>
            </Stack>
          )}
        </Box>
      </Box>
    </Box>
  )
}
