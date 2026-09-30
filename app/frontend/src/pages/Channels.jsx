import { useState, useEffect, useCallback } from "react"
import {
  Box, Typography, Tabs, Tab, Button, Chip, Stack, Paper, IconButton,
  CircularProgress, Alert, Avatar, Divider, Tooltip, FormControl, Select,
  MenuItem, TextField, Card, CardMedia, CardContent, TablePagination,
} from "@mui/material"
import YouTubeIcon from "@mui/icons-material/YouTube"
import LiveTvIcon from "@mui/icons-material/LiveTvOutlined"
import RefreshIcon from "@mui/icons-material/Refresh"
import PlayCircleOutlineIcon from "@mui/icons-material/PlayCircleOutline"
import OpenInNewIcon from "@mui/icons-material/OpenInNew"
import TravelExploreIcon from "@mui/icons-material/TravelExplore"
import DownloadIcon from "@mui/icons-material/Download"
import LinkIcon from "@mui/icons-material/Link"
import LinkOffIcon from "@mui/icons-material/LinkOff"
import SearchIcon from "@mui/icons-material/Search"
import CheckCircleIcon from "@mui/icons-material/CheckCircle"
import AddIcon from "@mui/icons-material/Add"
import ArrowBackIcon from "@mui/icons-material/ArrowBack"
import VisibilityIcon from "@mui/icons-material/VisibilityOutlined"
import ThumbUpIcon from "@mui/icons-material/ThumbUpOutlined"
import ChatBubbleIcon from "@mui/icons-material/ChatBubbleOutlineOutlined"
import http from "../api/http"
import useAppStore from "../store/appStore"
import useDocumentTitle from "../hooks/useDocumentTitle"
import {
  canonicalChannelVideoUrl,
  channelRequestConfig,
  reconcileSelectedChannel,
  channelsWithoutChannel,
  selectedChannelAfterDisconnect,
} from "../channelVideoIdentity"

function formatCount(n) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function timeAgo(dateStr) {
  if (!dateStr) return ""
  const d = typeof dateStr === "number" ? new Date(dateStr * 1000) : new Date(dateStr)
  const diff = Date.now() - d.getTime()
  const days = Math.floor(diff / 86400000)
  if (days < 1) return "Today"
  if (days === 1) return "1d ago"
  if (days < 7) return `${days}d ago`
  if (days < 30) return `${Math.floor(days / 7)}w ago`
  if (days < 365) return `${Math.floor(days / 30)}mo ago`
  return `${Math.floor(days / 365)}y ago`
}

function parseDuration(iso) {
  if (!iso) return ""
  const m = iso.match(/PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/)
  if (!m) return ""
  const h = parseInt(m[1] || 0)
  const min = parseInt(m[2] || 0)
  const s = parseInt(m[3] || 0)
  if (h > 0) return `${h}:${String(min).padStart(2, "0")}:${String(s).padStart(2, "0")}`
  return `${min}:${String(s).padStart(2, "0")}`
}

/* ── Connect Form (search + URL fallback) ──────────────────────── */

function ConnectForm({ platform, onConnected }) {
  const [query, setQuery] = useState("")
  const [loading, setLoading] = useState(false)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState("")
  const [results, setResults] = useState(null)
  const [showUrlInput, setShowUrlInput] = useState(platform === "douyin")
  const [url, setUrl] = useState("")
  const isYT = platform === "youtube"
  const isDouyin = platform === "douyin"
  const label = isYT ? "YouTube" : isDouyin ? "Douyin" : "TikTok"

  const handleSearch = async () => {
    if (!query.trim()) return
    setSearching(true)
    setError("")
    setResults(null)
    try {
      const { data } = await http.get("/api/channels/search", {
        params: { q: query.trim(), platform },
      })
      setResults(data.results || [])
      if ((data.results || []).length === 0) {
        setError("Không tìm thấy kênh. Hãy thử tên khác hoặc dùng tùy chọn URL bên dưới.")
      }
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setSearching(false)
    }
  }

  const handleSelect = async (channel) => {
    setLoading(true)
    setError("")
    try {
      await http.post(
        "/api/channels/connect",
        { platform, url: channel.url },
        { timeout: 40_000 },
      )
      onConnected()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleConnectUrl = async () => {
    if (!url.trim()) return
    setLoading(true)
    setError("")
    try {
      await http.post(
        "/api/channels/connect",
        { platform, url: url.trim() },
        { timeout: 40_000 },
      )
      onConnected()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Paper elevation={0} sx={{
      p: 4, textAlign: "center", borderRadius: 3,
      border: 2, borderColor: "primary.main", borderStyle: "dashed",
      bgcolor: (theme) => theme.palette.mode === "dark" ? "rgba(13,159,110,0.06)" : "rgba(13,159,110,0.04)",
    }}>
      {isYT ? (
        <YouTubeIcon sx={{ fontSize: 52, color: "primary.main", mb: 1, opacity: 0.8 }} />
      ) : (
        <LiveTvIcon sx={{ fontSize: 52, color: "primary.main", mb: 1, opacity: 0.8 }} />
      )}
      <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.5 }}>
        Kết nối kênh {label}
      </Typography>
      <Typography variant="body2" sx={{ color: "text.secondary", mb: 2.5, maxWidth: 520, mx: "auto" }}>
        {isDouyin
          ? "Douyin dùng connector riêng qua phiên CDP đã đăng nhập. Dán URL hồ sơ creator; không giả lập tìm kiếm tên bằng TikTok hoặc nguồn khác."
          : `Tìm kênh ${label} theo tên${isYT ? "" : " hoặc @handle"} để xem phân tích và tải video`}
      </Typography>

      {!isDouyin && (
        <Stack direction="row" spacing={1} sx={{ maxWidth: 480, mx: "auto", mb: 2 }}>
          <TextField
            fullWidth size="small"
            placeholder={isYT ? "Tìm theo tên kênh..." : "Tìm theo @handle hoặc tên..."}
            value={query} onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            disabled={searching || loading}
          />
          <Button variant="contained" onClick={handleSearch} disabled={searching || !query.trim() || loading}
            startIcon={searching ? <CircularProgress size={16} /> : <SearchIcon />}
            sx={{ whiteSpace: "nowrap", minWidth: 100 }}>
            Tìm
          </Button>
        </Stack>
      )}

      {results && results.length > 0 && (
        <Stack spacing={1} sx={{ maxWidth: 520, mx: "auto", mb: 2 }}>
          {results.map((ch) => (
            <Paper key={ch.channel_id || ch.username} elevation={0}
              sx={{
                display: "flex", alignItems: "center", gap: 1.5, p: 1.5,
                border: 1, borderColor: "divider", borderRadius: 2, textAlign: "left",
                cursor: "pointer", "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" },
              }}
              onClick={() => handleSelect(ch)}>
              <Avatar src={ch.thumbnail_url} sx={{ width: 48, height: 48 }} />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="body2" sx={{ fontWeight: 600, fontSize: "0.9rem" }}>
                  {ch.title || ch.display_name}
                </Typography>
                <Stack direction="row" spacing={1} sx={{ mt: 0.25 }}>
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {formatCount(ch.subscriber_count || ch.follower_count)} {isYT ? "người đăng ký" : "người theo dõi"}
                  </Typography>
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {formatCount(ch.video_count)} video
                  </Typography>
                </Stack>
              </Box>
              {loading ? <CircularProgress size={20} /> : (
                <Tooltip title="Chọn kênh này">
                  <CheckCircleIcon sx={{ color: "primary.main", fontSize: 24 }} />
                </Tooltip>
              )}
            </Paper>
          ))}
        </Stack>
      )}

      {error && (
        <Typography variant="caption" sx={{ color: "error.main", mb: 1.5, display: "block" }}>{error}</Typography>
      )}

      {!isDouyin && (
        <Divider sx={{ my: 2, maxWidth: 480, mx: "auto" }}>
          <Chip label="hoặc" size="small" />
        </Divider>
      )}

      {!showUrlInput ? (
        <Button size="small" variant="text" onClick={() => setShowUrlInput(true)}
          startIcon={<LinkIcon />} sx={{ color: "text.secondary" }}>
          Kết nối bằng URL kênh
        </Button>
      ) : (
        <Stack direction="row" spacing={1} sx={{ maxWidth: 480, mx: "auto" }}>
          <TextField
            fullWidth size="small"
            placeholder={isYT ? "https://youtube.com/@yourchannel" : isDouyin ? "https://www.douyin.com/user/<sec_user_id>" : "https://tiktok.com/@yourprofile"}
            value={url} onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleConnectUrl()}
            disabled={loading}
          />
          <Button variant="outlined" onClick={handleConnectUrl} disabled={loading || !url.trim()}
            startIcon={loading ? <CircularProgress size={16} /> : <LinkIcon />}
            sx={{ whiteSpace: "nowrap", minWidth: 100 }}>
            Kết nối
          </Button>
        </Stack>
      )}
    </Paper>
  )
}

/* ── Video Card with inline play ──────────────────────────────── */

function VideoCard({ video, platform, onAnalyze, onDownload }) {
  const isYT = platform === "youtube"
  const isDouyin = platform === "douyin"
  const platformLabel = isYT ? "YouTube" : isDouyin ? "Douyin" : "TikTok"
  const duration = isYT
    ? parseDuration(video.duration)
    : video.duration > 0
      ? `${Math.floor(video.duration / 60)}:${String(video.duration % 60).padStart(2, "0")}`
      : ""
  const uploadDate = isYT ? video.published_at : video.created_at

  // Build watch URL for opening in browser
  const watchUrl = isYT
    ? `https://www.youtube.com/watch?v=${video.video_id}`
    : canonicalChannelVideoUrl(platform, video) || (
      isDouyin ? "" : `https://www.tiktok.com/video/${video.video_id}`
    )
  const thumbUrl = isYT ? video.thumbnail_url : video.cover_url

  return (
    <Card variant="outlined" sx={{
      position: "relative",
      borderRadius: 2,
      transition: "border-color 0.2s, box-shadow 0.2s",
      "&:hover": { borderColor: "primary.main", boxShadow: "0 0 0 1px rgba(201,100,66,0.2)" },
    }}>
      {/* Video area: thumbnail with play overlay — opens in browser */}
      <Box sx={{ position: "relative", width: "100%", height: 160, bgcolor: "black", cursor: "pointer", "&:hover .play-icon": { transform: "scale(1.15)", opacity: 1 } }}
        onClick={() => window.open(watchUrl, "_blank")}
      >
        {duration && (
          <Chip label={duration} size="small"
            sx={{ position: "absolute", top: 8, right: 8, zIndex: 2, height: 22, fontSize: "0.7rem", bgcolor: "rgba(0,0,0,0.75)", color: "#fff", "& .MuiChip-label": { px: 0.75 } }} />
        )}
        <CardMedia
          component="img" height={160}
          image={thumbUrl}
          alt={video.title}
          sx={{ cursor: "pointer" }}
          onError={(e) => { e.target.style.display = "none" }}
        />
        {/* Play button overlay */}
        <Box sx={{
          position: "absolute", top: 0, left: 0, width: "100%", height: "100%",
          display: "flex", alignItems: "center", justifyContent: "center",
          bgcolor: "transparent",
        }}>
          <PlayCircleOutlineIcon className="play-icon" sx={{
            fontSize: 52, color: "rgba(255,255,255,0.85)",
            filter: "drop-shadow(0 2px 4px rgba(0,0,0,0.5))",
            opacity: 0.8, transition: "transform 0.15s, opacity 0.15s",
          }} />
        </Box>
      </Box>

      <CardContent sx={{ p: 1.5, "&:last-child": { pb: 1.5 } }}>
        <Typography variant="body2" sx={{
          fontWeight: 500, mb: 0.5,
          overflow: "hidden", textOverflow: "ellipsis",
          display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
        }}>
          {video.title || "Chưa đặt tên"}
        </Typography>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, flexWrap: "wrap" }}>
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            <VisibilityIcon sx={{ fontSize: 12, verticalAlign: "middle", mr: 0.3 }} />{formatCount(video.view_count)} &middot; <ThumbUpIcon sx={{ fontSize: 12, verticalAlign: "middle", mr: 0.3 }} />{formatCount(video.like_count)} &middot; <ChatBubbleIcon sx={{ fontSize: 12, verticalAlign: "middle", mr: 0.3 }} />{formatCount(video.comment_count)}
            {!isYT && video.share_count > 0 && ` \u00B7 ${formatCount(video.share_count)} shares`}
          </Typography>
          {video.outlier_score >= 3 && (
            <Chip
              label={video.outlier_score >= 20 ? `🔥 ${video.outlier_score}x` : video.outlier_score >= 10 ? `⚡ ${video.outlier_score}x` : video.outlier_score >= 5 ? `🚀 ${video.outlier_score}x` : `${video.outlier_score}x`}
              size="small"
              color={video.outlier_score >= 10 ? "error" : video.outlier_score >= 5 ? "warning" : "info"}
              variant="outlined"
              sx={{ fontWeight: 700, fontSize: "0.6rem", height: 18 }}
            />
          )}
        </Box>
        <Typography variant="caption" sx={{ color: "text.disabled", fontSize: "0.7rem", display: "block", mt: 0.5 }}>
          {uploadDate ? timeAgo(uploadDate) : ""}
          {uploadDate && (() => {
            const d = typeof uploadDate === "number" ? new Date(uploadDate * 1000) : new Date(uploadDate)
            return isNaN(d.getTime()) ? "" : ` \u00B7 ${d.toLocaleDateString()}`
          })()}
        </Typography>
        <Stack direction="row" spacing={0.5} alignItems="center" sx={{ mt: 0.75 }}>
          <Button size="small" variant="contained" startIcon={<DownloadIcon />}
            onClick={(e) => { e.stopPropagation(); onDownload(video) }}>
            Download & Analyze
          </Button>
          <Box sx={{ flex: 1 }} />
          <Tooltip title="Phân tích video này" arrow>
            <IconButton size="small" onClick={(e) => { e.stopPropagation(); onAnalyze(video) }}
              sx={{ p: 0.5, color: "text.secondary", "&:hover": { color: "primary.main" } }}>
              <TravelExploreIcon sx={{ fontSize: "1.1rem" }} />
            </IconButton>
          </Tooltip>
          <Tooltip title={`Mở trên ${platformLabel}`} arrow>
            <IconButton size="small"
              onClick={(e) => {
                e.stopPropagation()
                if (watchUrl) window.open(watchUrl, "_blank", "noopener")
              }}
              sx={{ p: 0.5, color: "text.secondary", "&:hover": { color: "primary.main" } }}>
              <OpenInNewIcon sx={{ fontSize: "1.1rem" }} />
            </IconButton>
          </Tooltip>
        </Stack>
      </CardContent>
    </Card>
  )
}

/* ── Channel Detail View (videos grid) ─────────────────────────── */

function ChannelDetail({ channel, onBack }) {
  const [channelInfo, setChannelInfo] = useState(null)
  const [videos, setVideos] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [sortBy, setSortBy] = useState("recent")
  // Videos open in browser (no embedded iframe — avoids YouTube bot detection)
  const [page, setPage] = useState(0)
  const [rowsPerPage, setRowsPerPage] = useState(18)
  const [pageTokens, setPageTokens] = useState({ 0: null })
  const [hasMore, setHasMore] = useState(false)
  const [nextPageToken, setNextPageToken] = useState(null)
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const isYT = channel.platform === "youtube"
  const isDouyin = channel.platform === "douyin"

  const fetchData = useCallback(async (
    refresh = false,
    targetPage = 0,
    token = null,
    requestedLimit = rowsPerPage,
  ) => {
    setLoading(true)
    setError(null)
    try {
      const params = { limit: requestedLimit }
      if (refresh) params.refresh = true
      if (isDouyin && token) params.page_token = token
      const { data: resp } = await http.get(`/api/channels/videos/${channel.id}`, {
        ...channelRequestConfig,
        params,
      })
      setVideos(resp.videos || [])
      setChannelInfo(resp.channel || resp.user || null)
      if (isDouyin) {
        const more = Boolean(resp.has_more && resp.next_page_token)
        setHasMore(more)
        setNextPageToken(resp.next_page_token || null)
        setPageTokens((previous) => ({
          ...previous,
          [targetPage]: token || null,
          ...(more ? { [targetPage + 1]: resp.next_page_token } : {}),
        }))
      }
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }, [channel.id, isDouyin, rowsPerPage])

  useEffect(() => {
    setPage(0)
    setPageTokens({ 0: null })
    fetchData(false, 0, null, rowsPerPage)
  }, [fetchData, rowsPerPage])

  const handleRefresh = () => {
    setVideos([])
    setPage(0)
    setPageTokens({ 0: null })
    setHasMore(false)
    setNextPageToken(null)
    fetchData(true, 0, null, rowsPerPage)
  }

  const handlePageChange = (_, targetPage) => {
    if (!isDouyin) {
      setPage(targetPage)
      return
    }
    const token = pageTokens[targetPage]
    if (targetPage > page && !token) return
    setPage(targetPage)
    fetchData(false, targetPage, token || null, rowsPerPage)
  }

  const handleRowsPerPageChange = (event) => {
    const nextSize = parseInt(event.target.value, 10)
    setRowsPerPage(nextSize)
    setPage(0)
    setPageTokens({ 0: null })
    setHasMore(false)
    setNextPageToken(null)
  }

  const handleAnalyze = async (video) => {
    const sourceUrl = canonicalChannelVideoUrl(channel.platform, video)
    if (!sourceUrl) {
      showSnackbar("Không thể xác minh danh tính video đã chọn.", "error")
      return
    }
    try {
      await http.post("/api/channels/analyze", { video_url: sourceUrl, title: video.title })
      showSnackbar(`Đang phân tích: ${video.title || "video"}`, "info")
    } catch (e) {
      showSnackbar(`Không thể bắt đầu phân tích: ${e.response?.data?.detail || e.message}`, "error")
    }
  }

  const handleDownload = async (video) => {
    const sourceUrl = canonicalChannelVideoUrl(channel.platform, video)
    if (!sourceUrl) {
      showSnackbar("Không thể xác minh danh tính video đã chọn.", "error")
      return
    }
    try {
      await http.post("/api/downloaded/batch-download", {
        urls: [{ url: sourceUrl, title: video.title || "" }],
      })
      showSnackbar(`Đang tải xuống: ${video.title || "video"}`, "info", { label: "Xem trong Thư viện", href: "/library" })
    } catch (e) {
      showSnackbar(`Tải xuống thất bại: ${e.response?.data?.detail || e.message}`, "error")
    }
  }

  const sorted = [...videos].sort((a, b) => {
    if (sortBy === "views") return b.view_count - a.view_count
    if (sortBy === "likes") return b.like_count - a.like_count
    return 0
  })

  const paged = isDouyin
    ? sorted
    : sorted.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage)
  const knownVideoCount = Number(channelInfo?.video_count || channel.video_count || 0)
  const douyinPaginationCount = knownVideoCount > 0
    ? knownVideoCount
    : (page * rowsPerPage) + sorted.length + (hasMore ? 1 : 0)

  return (
    <Box>
      {/* Header */}
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
        <IconButton size="small" onClick={onBack}>
          <ArrowBackIcon />
        </IconButton>
        <Avatar src={channel.thumbnail_url || channelInfo?.thumbnail_url} sx={{ width: 44, height: 44 }} />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="h6" sx={{ fontWeight: 600, fontSize: "1rem" }}>
            {channel.channel_name || channelInfo?.title || channelInfo?.display_name || "Kênh"}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
            <Chip size="small" variant="outlined"
              label={`${formatCount(channel.subscriber_count || channelInfo?.subscriber_count || channelInfo?.follower_count || 0)} ${isYT ? "người đăng ký" : "người theo dõi"}`} />
            <Chip size="small" variant="outlined"
              label={`${formatCount(channel.video_count || channelInfo?.video_count || 0)} video`} />
            {channelInfo?.median_views > 0 && (
              <Chip size="small" variant="outlined" color="info"
                label={`Trung vị: ${formatCount(channelInfo.median_views)} lượt xem`} />
            )}
          </Stack>
        </Box>
        <Tooltip title="Làm mới">
          <IconButton size="small" onClick={handleRefresh}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Stack>

      <Divider sx={{ mb: 2 }} />

      {/* Performance Analytics Summary */}
      {!loading && videos.length > 0 && (() => {
        const views = videos.map(v => v.view_count || 0).filter(v => v > 0)
        const likes = videos.map(v => v.like_count || 0).filter(l => l > 0)
        const totalViews = views.reduce((a, b) => a + b, 0)
        const avgViews = views.length ? Math.round(totalViews / views.length) : 0
        const sortedViews = [...views].sort((a, b) => a - b)
        const medianViews = sortedViews.length ? sortedViews[Math.floor(sortedViews.length / 2)] : 0
        const topPerformer = videos.reduce((best, v) => (v.view_count || 0) > (best.view_count || 0) ? v : best, videos[0])
        const totalLikes = likes.reduce((a, b) => a + b, 0)
        const engagementRate = totalViews > 0 ? ((totalLikes / totalViews) * 100).toFixed(2) : 0
        const recentVideos = videos.filter(v => {
          const raw = v.published_at || v.upload_date || v.created_at
          if (!raw) return false
          const d = typeof raw === "number" ? new Date(raw * 1000) : new Date(raw)
          return !isNaN(d.getTime()) && (Date.now() - d.getTime()) < 30 * 86400000
        })

        const stats = [
          { label: "Tổng lượt xem", value: formatCount(totalViews) },
          { label: "Lượt xem TB", value: formatCount(avgViews) },
          { label: "Trung vị lượt xem", value: formatCount(medianViews) },
          { label: "Engagement", value: `${engagementRate}%` },
          { label: "30 ngày qua", value: `${recentVideos.length} video` },
          { label: "Video nổi bật", value: formatCount(topPerformer?.view_count || 0) + " lượt xem" },
        ]

        return (
          <Box sx={{
            display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
            gap: 1.5, mb: 2,
          }}>
            {stats.map(s => (
              <Paper key={s.label} variant="outlined" sx={{ p: 1.5, borderRadius: 2, textAlign: "center" }}>
                <Typography variant="h6" sx={{ fontWeight: 700, fontSize: "1.1rem", lineHeight: 1.2 }}>
                  {s.value}
                </Typography>
                <Typography variant="caption" sx={{ color: "text.secondary", fontSize: "0.68rem" }}>
                  {s.label}
                </Typography>
              </Paper>
            ))}
          </Box>
        )
      })()}

      {loading ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress size={32} />
        </Box>
      ) : error ? (
        <Alert severity="error" sx={{ borderRadius: 2 }}>{error}</Alert>
      ) : (
        <>
          {/* Sort control */}
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
            <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5, fontSize: "0.7rem" }}>
              {videos.length} video
            </Typography>
            <FormControl size="small" sx={{ minWidth: 130 }}>
              <Select value={sortBy} onChange={(e) => { setSortBy(e.target.value); setPage(0) }}
                sx={{ fontSize: "0.82rem", "& .MuiSelect-select": { py: 0.5 } }}>
                <MenuItem value="recent">Gần đây</MenuItem>
                <MenuItem value="views">Nhiều lượt xem nhất</MenuItem>
                <MenuItem value="likes">Nhiều lượt thích nhất</MenuItem>
              </Select>
            </FormControl>
          </Stack>

          {/* Video cards grid */}
          {sorted.length === 0 ? (
            <Paper elevation={0} sx={{ p: 3, textAlign: "center", border: 1, borderColor: "divider", borderRadius: 2 }}>
              <Typography variant="body2" sx={{ color: "text.secondary" }}>Không tìm thấy video.</Typography>
            </Paper>
          ) : (
            <>
              <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 2 }}>
                {paged.map((v) => (
                  <VideoCard key={v.video_id} video={v} platform={channel.platform} onAnalyze={handleAnalyze} onDownload={handleDownload} />
                ))}
              </Box>
              <TablePagination
                component="div"
                count={isDouyin ? douyinPaginationCount : sorted.length}
                page={page}
                onPageChange={handlePageChange}
                rowsPerPage={rowsPerPage}
                onRowsPerPageChange={handleRowsPerPageChange}
                rowsPerPageOptions={[18, 36, 50]}
                nextIconButtonProps={
                  isDouyin
                    ? { disabled: !hasMore || !nextPageToken }
                    : undefined
                }
                sx={{ borderTop: 1, borderColor: "divider", mt: 1 }}
              />
            </>
          )}
        </>
      )}
    </Box>
  )
}

/* ── Platform Tab (channel list + add) ─────────────────────────── */

function PlatformTab({ platform }) {
  const [channels, setChannels] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [selectedChannel, setSelectedChannel] = useState(null)
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const isYT = platform === "youtube"

  const fetchChannels = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await http.get("/api/channels/list", { params: { platform } })
      const list = data.channels || []
      setChannels(list)
      setSelectedChannel((current) => (
        reconcileSelectedChannel(current, list, platform)
      ))
    } catch (e) {
      showSnackbar(`Không thể tải kênh: ${e.message}`, "error")
    } finally {
      setLoading(false)
    }
  }, [platform, showSnackbar]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetchChannels() }, [fetchChannels])

  useEffect(() => {
    setSelectedChannel(null)
    setShowAdd(false)
  }, [platform])

  const handleDisconnect = async (ch) => {
    try {
      await http.post("/api/channels/disconnect", { id: ch.id })
      setChannels((prev) => channelsWithoutChannel(prev, ch.id))
      setSelectedChannel((current) => selectedChannelAfterDisconnect(current, ch.id))
      showSnackbar(`${ch.channel_name || "Kênh"} đã ngắt kết nối`, "info")
    } catch (e) {
      showSnackbar(`Ngắt kết nối thất bại: ${e.message}`, "error")
    }
  }

  const handleConnected = () => {
    setShowAdd(false)
    fetchChannels()
  }

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress size={32} />
      </Box>
    )
  }

  // Show channel detail view
  if (selectedChannel) {
    return (
      <ChannelDetail
        channel={selectedChannel}
        onBack={() => setSelectedChannel(null)}
      />
    )
  }

  // Show add form
  if (showAdd || channels.length === 0) {
    return (
      <Box>
        {channels.length > 0 && (
          <Button size="small" startIcon={<ArrowBackIcon />} onClick={() => setShowAdd(false)}
            sx={{ mb: 2 }}>
            Back to channels
          </Button>
        )}
        <ConnectForm platform={platform} onConnected={handleConnected} />
      </Box>
    )
  }

  // Channel list
  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="body2" sx={{ color: "text.secondary", fontWeight: 600 }}>
          {channels.length} kênh đã kết nối
        </Typography>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => setShowAdd(true)}>
          Thêm kênh
        </Button>
      </Stack>

      <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 2 }}>
        {channels.map((ch) => (
          <Card key={ch.id} variant="outlined" sx={{
            borderRadius: 2, cursor: "pointer",
            "&:hover": { borderColor: "primary.main", boxShadow: 1 },
            transition: "all 0.15s",
          }}>
            <CardContent
              sx={{ display: "flex", alignItems: "center", gap: 1.5, p: 2, "&:last-child": { pb: 2 } }}
              onClick={() => setSelectedChannel(ch)}
            >
              <Avatar src={ch.thumbnail_url} sx={{ width: 52, height: 52 }}>
                {isYT ? <YouTubeIcon /> : <LiveTvIcon />}
              </Avatar>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Tooltip title={ch.channel_name || ch.channel_url} enterDelay={500}>
                  <Typography variant="body2" sx={{ fontWeight: 600, fontSize: "0.92rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {ch.channel_name || ch.channel_url}
                  </Typography>
                </Tooltip>
                <Stack direction="row" spacing={1} sx={{ mt: 0.25 }}>
                  {ch.subscriber_count > 0 && (
                    <Typography variant="caption" sx={{ color: "text.secondary" }}>
                      {formatCount(ch.subscriber_count)} {isYT ? "người đăng ký" : "người theo dõi"}
                    </Typography>
                  )}
                  {ch.video_count > 0 && (
                    <Typography variant="caption" sx={{ color: "text.secondary" }}>
                      {formatCount(ch.video_count)} video
                    </Typography>
                  )}
                </Stack>
              </Box>
              <Stack alignItems="center" spacing={0.5}>
                <Button size="small" variant="text" sx={{ minWidth: 0 }}>
                  View videos
                </Button>
                <Tooltip title="Ngắt kết nối">
                  <IconButton size="small" onClick={(e) => { e.stopPropagation(); handleDisconnect(ch) }}
                    sx={{ color: "text.secondary" }}>
                    <LinkOffIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Stack>
            </CardContent>
          </Card>
        ))}
      </Box>
    </Box>
  )
}

/* ── Main Page ─────────────────────────────────────────────────── */

export default function Channels() {
  useDocumentTitle("Khám phá / Kênh")
  const [tab, setTab] = useState(0)
  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* ── Header ── */}
      <Box sx={{
        px: 3, py: 2, flexShrink: 0,
        borderBottom: 1, borderColor: "divider",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        background: (t) => t.palette.mode === "dark"
          ? "linear-gradient(135deg, rgba(230,126,34,0.10) 0%, rgba(30,28,26,1) 100%)"
          : "linear-gradient(135deg, rgba(230,126,34,0.07) 0%, rgba(255,255,255,1) 100%)",
      }}>
        <Stack direction="row" spacing={1.5} alignItems="center">
          <LiveTvIcon sx={{ color: "warning.main", fontSize: 26 }} />
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 700, letterSpacing: -0.3 }}>
              Discover / Channels
            </Typography>
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              Monitor source channels, compare outliers, and acquire videos into Library
            </Typography>
          </Box>
        </Stack>
      </Box>

      {/* ── Tabs ── */}
      <Box sx={{ px: 3, flexShrink: 0, borderBottom: 1, borderColor: "divider" }}>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ minHeight: 42 }}>
          <Tab icon={<YouTubeIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="YouTube" sx={{ textTransform: "none", minHeight: 42, fontSize: "0.9rem" }} />
          <Tab icon={<LiveTvIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="TikTok" sx={{ textTransform: "none", minHeight: 42, fontSize: "0.9rem" }} />
          <Tab icon={<LiveTvIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="Douyin" sx={{ textTransform: "none", minHeight: 42, fontSize: "0.9rem" }} />
        </Tabs>
      </Box>

      {/* ── Scrollable content ── */}
      <Box sx={{ flex: 1, overflow: "auto", p: { xs: 2, md: 3 } }}>
        {tab === 0 && <PlatformTab platform="youtube" />}
        {tab === 1 && <PlatformTab platform="tiktok" />}
        {tab === 2 && <PlatformTab platform="douyin" />}
      </Box>
    </Box>
  )
}
