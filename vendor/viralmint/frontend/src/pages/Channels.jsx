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
  const [showUrlInput, setShowUrlInput] = useState(false)
  const [url, setUrl] = useState("")
  const isYT = platform === "youtube"
  const label = isYT ? "YouTube" : "TikTok"

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
        setError("No channels found. Try a different name or use the URL option below.")
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
      await http.post("/api/channels/connect", { platform, url: channel.url })
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
      await http.post("/api/channels/connect", { platform, url: url.trim() })
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
        Connect a {label} Channel
      </Typography>
      <Typography variant="body2" sx={{ color: "text.secondary", mb: 2.5, maxWidth: 480, mx: "auto" }}>
        Search for any {label} channel by name{isYT ? "" : " or @handle"} to view analytics and download videos
      </Typography>

      <Stack direction="row" spacing={1} sx={{ maxWidth: 480, mx: "auto", mb: 2 }}>
        <TextField
          fullWidth size="small"
          placeholder={isYT ? "Search by channel name..." : "Search by @handle or name..."}
          value={query} onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          disabled={searching || loading}
        />
        <Button variant="contained" onClick={handleSearch} disabled={searching || !query.trim() || loading}
          startIcon={searching ? <CircularProgress size={16} /> : <SearchIcon />}
          sx={{ whiteSpace: "nowrap", minWidth: 100 }}>
          Search
        </Button>
      </Stack>

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
                    {formatCount(ch.subscriber_count || ch.follower_count)} {isYT ? "subscribers" : "followers"}
                  </Typography>
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {formatCount(ch.video_count)} videos
                  </Typography>
                </Stack>
              </Box>
              {loading ? <CircularProgress size={20} /> : (
                <Tooltip title="Select this channel">
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

      <Divider sx={{ my: 2, maxWidth: 480, mx: "auto" }}>
        <Chip label="or" size="small" />
      </Divider>

      {!showUrlInput ? (
        <Button size="small" variant="text" onClick={() => setShowUrlInput(true)}
          startIcon={<LinkIcon />} sx={{ color: "text.secondary" }}>
          Connect with channel URL instead
        </Button>
      ) : (
        <Stack direction="row" spacing={1} sx={{ maxWidth: 480, mx: "auto" }}>
          <TextField
            fullWidth size="small"
            placeholder={isYT ? "https://youtube.com/@yourchannel" : "https://tiktok.com/@yourprofile"}
            value={url} onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleConnectUrl()}
            disabled={loading}
          />
          <Button variant="outlined" onClick={handleConnectUrl} disabled={loading || !url.trim()}
            startIcon={loading ? <CircularProgress size={16} /> : <LinkIcon />}
            sx={{ whiteSpace: "nowrap", minWidth: 100 }}>
            Connect
          </Button>
        </Stack>
      )}
    </Paper>
  )
}

/* ── Video Card with inline play ──────────────────────────────── */

function VideoCard({ video, platform, onAnalyze, onDownload }) {
  const isYT = platform === "youtube"
  const duration = isYT
    ? parseDuration(video.duration)
    : video.duration > 0
      ? `${Math.floor(video.duration / 60)}:${String(video.duration % 60).padStart(2, "0")}`
      : ""
  const uploadDate = isYT ? video.published_at : video.created_at

  // Build watch URL for opening in browser
  const watchUrl = isYT
    ? `https://www.youtube.com/watch?v=${video.video_id}`
    : video.url || `https://www.tiktok.com/video/${video.video_id}`
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
          {video.title || "Untitled"}
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
          <Tooltip title="Analyze this video" arrow>
            <IconButton size="small" onClick={(e) => { e.stopPropagation(); onAnalyze(video) }}
              sx={{ p: 0.5, color: "text.secondary", "&:hover": { color: "primary.main" } }}>
              <TravelExploreIcon sx={{ fontSize: "1.1rem" }} />
            </IconButton>
          </Tooltip>
          <Tooltip title={`Open on ${isYT ? "YouTube" : "TikTok"}`} arrow>
            <IconButton size="small"
              onClick={(e) => { e.stopPropagation(); window.open(video.url, "_blank", "noopener") }}
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
  const [rowsPerPage, setRowsPerPage] = useState(20)
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const isYT = channel.platform === "youtube"

  const fetchData = useCallback(async (refresh = false) => {
    setLoading(true)
    setError(null)
    try {
      const params = {}
      if (refresh) params.refresh = true
      const { data: resp } = await http.get(`/api/channels/videos/${channel.id}`, { params })
      setVideos(resp.videos || [])
      setChannelInfo(resp.channel || resp.user || null)
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }, [channel.id])

  useEffect(() => { fetchData() }, [fetchData])

  const handleRefresh = () => {
    setVideos([])
    setPage(0)
    fetchData(true)
  }

  const handleAnalyze = async (video) => {
    try {
      await http.post("/api/channels/analyze", { video_url: video.url, title: video.title })
      showSnackbar(`Analyzing: ${video.title || "video"}`, "info")
    } catch (e) {
      showSnackbar(`Failed to start analysis: ${e.response?.data?.detail || e.message}`, "error")
    }
  }

  const handleDownload = async (video) => {
    try {
      await http.post("/api/downloaded/batch-download", {
        urls: [{ url: video.url, title: video.title || "" }],
      })
      showSnackbar(`Downloading: ${video.title || "video"}`, "info", { label: "View in Library", href: "/videos" })
    } catch (e) {
      showSnackbar(`Download failed: ${e.response?.data?.detail || e.message}`, "error")
    }
  }

  const sorted = [...videos].sort((a, b) => {
    if (sortBy === "views") return b.view_count - a.view_count
    if (sortBy === "likes") return b.like_count - a.like_count
    return 0
  })

  const paged = sorted.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage)

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
            {channel.channel_name || channelInfo?.title || channelInfo?.display_name || "Channel"}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
            <Chip size="small" variant="outlined"
              label={`${formatCount(channel.subscriber_count || channelInfo?.subscriber_count || channelInfo?.follower_count || 0)} ${isYT ? "subscribers" : "followers"}`} />
            <Chip size="small" variant="outlined"
              label={`${formatCount(channel.video_count || channelInfo?.video_count || 0)} videos`} />
            {channelInfo?.median_views > 0 && (
              <Chip size="small" variant="outlined" color="info"
                label={`Median: ${formatCount(channelInfo.median_views)} views`} />
            )}
          </Stack>
        </Box>
        <Tooltip title="Refresh">
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
          { label: "Total Views", value: formatCount(totalViews) },
          { label: "Avg Views", value: formatCount(avgViews) },
          { label: "Median Views", value: formatCount(medianViews) },
          { label: "Engagement", value: `${engagementRate}%` },
          { label: "Last 30d", value: `${recentVideos.length} videos` },
          { label: "Top Video", value: formatCount(topPerformer?.view_count || 0) + " views" },
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
              {videos.length} video{videos.length !== 1 ? "s" : ""}
            </Typography>
            <FormControl size="small" sx={{ minWidth: 130 }}>
              <Select value={sortBy} onChange={(e) => { setSortBy(e.target.value); setPage(0) }}
                sx={{ fontSize: "0.82rem", "& .MuiSelect-select": { py: 0.5 } }}>
                <MenuItem value="recent">Recent</MenuItem>
                <MenuItem value="views">Most Views</MenuItem>
                <MenuItem value="likes">Most Likes</MenuItem>
              </Select>
            </FormControl>
          </Stack>

          {/* Video cards grid */}
          {sorted.length === 0 ? (
            <Paper elevation={0} sx={{ p: 3, textAlign: "center", border: 1, borderColor: "divider", borderRadius: 2 }}>
              <Typography variant="body2" sx={{ color: "text.secondary" }}>No videos found.</Typography>
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
                count={sorted.length}
                page={page}
                onPageChange={(_, p) => { setPage(p); setPlayingId(null) }}
                rowsPerPage={rowsPerPage}
                onRowsPerPageChange={(e) => { setRowsPerPage(parseInt(e.target.value, 10)); setPage(0) }}
                rowsPerPageOptions={[20, 50, 100]}
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
      // Auto-select if only one channel
      if (list.length === 1 && !selectedChannel) {
        setSelectedChannel(list[0])
      }
    } catch (e) {
      showSnackbar(`Failed to load channels: ${e.message}`, "error")
    } finally {
      setLoading(false)
    }
  }, [platform, showSnackbar]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetchChannels() }, [fetchChannels])

  const handleDisconnect = async (ch) => {
    try {
      await http.post("/api/channels/disconnect", { id: ch.id })
      setChannels((prev) => prev.filter((c) => c.id !== ch.id))
      showSnackbar(`${ch.channel_name || "Channel"} disconnected`, "info")
    } catch (e) {
      showSnackbar(`Failed to disconnect: ${e.message}`, "error")
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
          {channels.length} channel{channels.length !== 1 ? "s" : ""} connected
        </Typography>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => setShowAdd(true)}>
          Add Channel
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
                      {formatCount(ch.subscriber_count)} {isYT ? "subs" : "followers"}
                    </Typography>
                  )}
                  {ch.video_count > 0 && (
                    <Typography variant="caption" sx={{ color: "text.secondary" }}>
                      {formatCount(ch.video_count)} videos
                    </Typography>
                  )}
                </Stack>
              </Box>
              <Stack alignItems="center" spacing={0.5}>
                <Button size="small" variant="text" sx={{ minWidth: 0 }}>
                  View videos
                </Button>
                <Tooltip title="Disconnect">
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
              My Channels
            </Typography>
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              Connect YouTube and TikTok channels to view content and analyze videos
            </Typography>
          </Box>
        </Stack>
      </Box>

      {/* ── Tabs ── */}
      <Box sx={{ px: 3, flexShrink: 0, borderBottom: 1, borderColor: "divider" }}>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ minHeight: 42 }}>
          <Tab icon={<YouTubeIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="YouTube" sx={{ textTransform: "none", minHeight: 42, fontSize: "0.9rem" }} />
          <Tab icon={<LiveTvIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="TikTok" sx={{ textTransform: "none", minHeight: 42, fontSize: "0.9rem" }} />
        </Tabs>
      </Box>

      {/* ── Scrollable content ── */}
      <Box sx={{ flex: 1, overflow: "auto", p: { xs: 2, md: 3 } }}>
        {tab === 0 && <PlatformTab platform="youtube" />}
        {tab === 1 && <PlatformTab platform="tiktok" />}
      </Box>
    </Box>
  )
}
