import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { Box, Typography, Stack, CardActionArea, Divider, TextField, InputAdornment } from "@mui/material"
import SearchOutlinedIcon from "@mui/icons-material/SearchOutlined"
import useDocumentTitle from "../hooks/useDocumentTitle"
import PageHero from "../components/PageHero"
import { alpha } from "@mui/material/styles"
import { GlassCard } from "../utils/glassFx"
import { WORKFLOW_OWNED_UTILITY_IDS } from "../utilitiesModel"
import BuildOutlinedIcon from "@mui/icons-material/BuildOutlined"
import SubtitlesOutlinedIcon from "@mui/icons-material/SubtitlesOutlined"
import AspectRatioOutlinedIcon from "@mui/icons-material/AspectRatioOutlined"
import GraphicEqOutlinedIcon from "@mui/icons-material/GraphicEqOutlined"
import BrandingWatermarkOutlinedIcon from "@mui/icons-material/BrandingWatermarkOutlined"
import FastRewindOutlinedIcon from "@mui/icons-material/FastRewindOutlined"
import RecordVoiceOverOutlinedIcon from "@mui/icons-material/RecordVoiceOverOutlined"
import GraphicEqIcon from "@mui/icons-material/GraphicEq"
import TransformIcon from "@mui/icons-material/Transform"
import LightbulbOutlinedIcon from "@mui/icons-material/LightbulbOutlined"
import TranslateOutlinedIcon from "@mui/icons-material/TranslateOutlined"
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined"
import PlaylistAddCheckOutlinedIcon from "@mui/icons-material/PlaylistAddCheckOutlined"
import MovieFilterOutlinedIcon from "@mui/icons-material/MovieFilterOutlined"
import VideocamOutlinedIcon from "@mui/icons-material/VideocamOutlined"
import HeadphonesOutlinedIcon from "@mui/icons-material/HeadphonesOutlined"
import ClosedCaptionOutlinedIcon from "@mui/icons-material/ClosedCaptionOutlined"
import GifOutlinedIcon from "@mui/icons-material/GifOutlined"
import SpeedOutlinedIcon from "@mui/icons-material/SpeedOutlined"
import LabelOutlinedIcon from "@mui/icons-material/LabelOutlined"
import FormatListNumberedIcon from "@mui/icons-material/FormatListNumbered"
import ContentCutOutlinedIcon from "@mui/icons-material/ContentCutOutlined"
import ZoomInMapOutlinedIcon from "@mui/icons-material/ZoomInMapOutlined"
import CompressOutlinedIcon from "@mui/icons-material/CompressOutlined"
import CropOutlinedIcon from "@mui/icons-material/CropOutlined"
import VolumeOffOutlinedIcon from "@mui/icons-material/VolumeOffOutlined"
import DownloadForOfflineOutlinedIcon from "@mui/icons-material/DownloadForOfflineOutlined"

// ── Catalog ────────────────────────────────────────────────────────────────
//
// Each tool has a `category` that maps into the CATEGORIES array below.
// The open-source build ships the local FFmpeg / Whisper / Edge-TTS tools;
// media-generation tools (AI image / music / video / SFX / thumbnail) are
// not part of this build since they require external media-gen providers.

const TOOLS = [
  // Video — visual / structural edits
  {
    id: "video-download",
    title: "Tải video",
    description: "Dán liên kết để tải video — chọn chất lượng, giữ riêng hoặc nhúng phụ đề.",
    icon: <DownloadForOfflineOutlinedIcon />,
    category: "video",
  },
  {
    id: "merge-clips",
    title: "Ghép clip",
    description: "Ghép nhiều clip thành một video. Tự cắt theo tỷ lệ khung hình mong muốn.",
    icon: <MovieFilterOutlinedIcon />,
    category: "video",
  },
  {
    id: "transform",
    title: "Biến đổi",
    description: "Lật, xoay, lặp hoặc thay đổi âm lượng clip.",
    icon: <TransformIcon />,
    category: "video",
  },
  {
    // One runner, one job type, one history — but its own card, because
    // "remove audio" is what people search for, not "transform".
    id: "remove-audio",
    title: "Xóa âm thanh",
    description: "Loại bỏ âm thanh khỏi video, giữ nguyên hình ảnh.",
    icon: <VolumeOffOutlinedIcon />,
    category: "audio",
    route: "/utilities/transform?op=mute",
  },
  {
    id: "compress",
    title: "Nén video",
    description: "Giảm dung lượng để gửi email, chat hoặc đáp ứng giới hạn tải lên. Chọn độ phân giải và mức nén.",
    icon: <CompressOutlinedIcon />,
    category: "video",
  },
  {
    id: "crop",
    title: "Cắt khung video",
    description: "Kéo vùng chọn trên khung hình và chỉ giữ phần đó. Tự do hoặc theo tỷ lệ cố định.",
    icon: <CropOutlinedIcon />,
    category: "video",
  },
  {
    id: "reframe",
    title: "Đổi khung dọc",
    description: "Đổi 16:9 sang 9:16 với bám khuôn mặt cho Shorts/TikTok.",
    icon: <AspectRatioOutlinedIcon />,
    category: "video",
  },
  {
    id: "watermark",
    title: "Thêm watermark",
    description: "Gắn logo lên mọi bản xuất. Chỉnh vị trí, độ mờ và kích thước.",
    icon: <BrandingWatermarkOutlinedIcon />,
    category: "video",
  },
  {
    id: "gif",
    title: "Video → GIF",
    description: "Chuyển clip thành GIF động với bảng màu hai lượt cho màu sắc sạch hơn.",
    icon: <GifOutlinedIcon />,
    category: "video",
  },
  {
    id: "speed",
    title: "Tăng / giảm tốc",
    description: "Điều chỉnh tốc độ video từ 0.25× đến 4×, có thể giữ cao độ giọng.",
    icon: <SpeedOutlinedIcon />,
    category: "video",
  },
  {
    id: "trim",
    title: "Cắt đoạn",
    description: "Chỉ giữ đoạn cần dùng — đặt điểm bắt đầu và kết thúc, bỏ phần còn lại.",
    icon: <ContentCutOutlinedIcon />,
    category: "video",
  },
  {
    id: "auto-zoom",
    title: "Tự động zoom",
    description: "Tự zoom nhẹ theo lời nói để video có nhịp hơn.",
    icon: <ZoomInMapOutlinedIcon />,
    category: "video",
  },

  // Audio — cleanup / processing / voice
  {
    id: "voiceover",
    title: "Tạo lồng tiếng",
    description: "Biến kịch bản thành giọng đọc TTS tự nhiên.",
    icon: <RecordVoiceOverOutlinedIcon />,
    category: "audio",
  },
  {
    id: "audio-enhance",
    title: "Cải thiện âm thanh",
    description: "Khử nhiễu, cân bằng âm lượng và làm sạch giọng nói.",
    icon: <GraphicEqOutlinedIcon />,
    category: "audio",
  },
  {
    id: "remove-silence",
    title: "Cắt khoảng lặng",
    description: "Tự cắt khoảng nghỉ, từ đệm và đoạn im lặng.",
    icon: <FastRewindOutlinedIcon />,
    category: "audio",
  },
  {
    id: "music-visualizer",
    title: "Trực quan hóa nhạc",
    description: "Biến âm thanh thành video waveform / cột / phổ chuyển động đồng bộ nhạc.",
    icon: <GraphicEqIcon />,
    category: "audio",
  },

  // Subtitles & Insights — text overlays + analysis
  {
    id: "captions",
    title: "Thêm phụ đề",
    description: "Chèn phụ đề từng từ với các kiểu Viral, cổ điển hoặc đậm.",
    icon: <SubtitlesOutlinedIcon />,
    category: "subtitles",
  },
  {
    id: "subtitles",
    title: "Xuất phụ đề",
    description: "Chép lời thành tệp .srt / .vtt hoặc văn bản thuần để tải xuống.",
    icon: <ClosedCaptionOutlinedIcon />,
    category: "subtitles",
  },
  {
    id: "translate",
    title: "Dịch + lồng tiếng",
    description: "Dịch phụ đề sang hơn 19 ngôn ngữ hoặc lồng tiếng toàn bộ.",
    icon: <TranslateOutlinedIcon />,
    category: "subtitles",
  },
  {
    id: "localization-batch",
    title: "Bản địa hóa hàng loạt",
    description: "Chạy một hồ sơ bản địa hóa HQ cho tối đa 20 video, có thể tiếp tục từ trạng thái đã lưu.",
    icon: <PlaylistAddCheckOutlinedIcon />,
    category: "subtitles",
  },
  {
    id: "localization-review",
    title: "Duyệt bản địa hóa",
    description: "Mở lỗi QC, sửa bản dịch/nhịp/giọng, tạo lại từng đoạn và chạy lại các bước phía sau.",
    icon: <FactCheckOutlinedIcon />,
    category: "subtitles",
  },
  {
    id: "hook-analysis",
    title: "Phân tích hook",
    description: "Chấm 10 giây đầu video và gợi ý cách mở đầu mạnh hơn.",
    icon: <LightbulbOutlinedIcon />,
    category: "subtitles",
  },
  {
    id: "metadata",
    title: "Tiêu đề / Thẻ / Mô tả",
    description: "Tạo metadata SEO cho video, bản chép lời hoặc chủ đề — dùng ngay cho YouTube + TikTok.",
    icon: <LabelOutlinedIcon />,
    category: "subtitles",
  },
  {
    id: "auto-chapters",
    title: "Tự tạo chương",
    description: "AI tự tạo mốc chương YouTube từ video dài.",
    icon: <FormatListNumberedIcon />,
    category: "subtitles",
  },
]

// ── Category metadata ──────────────────────────────────────────────────────
//
// `accent` keys map to MUI theme palette slots — used for the section
// header's icon background tint and the colored left-border on each card,
// so each section reads at a glance even when scrolling fast.

const CATEGORIES = [
  {
    id: "video",
    label: "Video",
    description: "Chỉnh hình ảnh và cấu trúc cho video hoàn chỉnh.",
    icon: <VideocamOutlinedIcon />,
    accent: "info",
  },
  {
    id: "audio",
    label: "Âm thanh",
    description: "Giọng đọc, làm sạch và hoàn thiện âm thanh.",
    icon: <HeadphonesOutlinedIcon />,
    accent: "success",
  },
  {
    id: "subtitles",
    label: "Phụ đề & Phân tích",
    description: "Phụ đề, dịch thuật và lớp phân tích.",
    icon: <ClosedCaptionOutlinedIcon />,
    accent: "warning",
  },
]

// ── Components ─────────────────────────────────────────────────────────────

function ToolCard({ tool, onClick }) {
  return (
    <GlassCard sx={{ borderRadius: 2 }}>
      <CardActionArea
        onClick={onClick}
        sx={{
          p: 1.25,
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1.25} sx={{ mb: 0.5, width: "100%" }}>
          <Box sx={{ color: "primary.main", display: "flex", "& svg": { fontSize: 22 } }}>
            {tool.icon}
          </Box>
          <Typography variant="body2" sx={{ fontWeight: 700, fontSize: "0.9rem", flex: 1 }}>
            {tool.title}
          </Typography>
        </Stack>
        <Typography
          variant="caption"
          sx={{
            color: "text.secondary",
            lineHeight: 1.4,
            fontSize: "0.78rem",
            display: "-webkit-box",
            WebkitBoxOrient: "vertical",
            WebkitLineClamp: 2,
            overflow: "hidden",
          }}
        >
          {tool.description}
        </Typography>
      </CardActionArea>
    </GlassCard>
  )
}

function CategorySection({ category, tools, onPick }) {
  return (
    <Box sx={{ mb: 2 }}>
      {/* Section header */}
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ mb: 1, px: 0.5 }}>
        <Box sx={{
          width: 32, height: 32,
          borderRadius: 1.25,
          display: "flex", alignItems: "center", justifyContent: "center",
          bgcolor: (t) => alpha(t.palette[category.accent].main, t.palette.mode === "dark" ? 0.22 : 0.13),
          color: `${category.accent}.main`,
          "& svg": { fontSize: 20 },
        }}>
          {category.icon}
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="baseline" spacing={1}>
            <Typography variant="subtitle1" sx={{ fontWeight: 700, letterSpacing: -0.2 }}>
              {category.label}
            </Typography>
            <Typography variant="caption" sx={{ color: "text.disabled", fontWeight: 600 }}>
              {tools.length}
            </Typography>
          </Stack>
          <Typography variant="caption" sx={{ color: "text.secondary", display: "block", lineHeight: 1.3 }}>
            {category.description}
          </Typography>
        </Box>
      </Stack>

      {/* Grid */}
      <Box
        sx={{
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: {
            xs: "1fr",
            sm: "repeat(2, 1fr)",
            md: "repeat(3, 1fr)",
            lg: "repeat(3, 1fr)",
          },
        }}
      >
        {tools.map((t) => (
          <ToolCard key={t.id} tool={t} onClick={() => onPick(t.id)} />
        ))}
      </Box>
    </Box>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Tools() {
  useDocumentTitle("Công cụ")
  const navigate = useNavigate()
  const visibleTools = TOOLS.filter((tool) => !WORKFLOW_OWNED_UTILITY_IDS.has(tool.id))
  const pick = (id) => {
    const tool = visibleTools.find((t) => t.id === id)
    navigate(tool?.route || `/utilities/${id}`)
  }

  // Hub search — a flat ranked result grid when a query is present, else the
  // categorized layout (title/description/id all match against the query).
  const [query, setQuery] = useState("")
  const q = query.trim().toLowerCase()
  const matches = q
    ? visibleTools.filter((t) => [t.title, t.description, t.id].some((s) => (s || "").toLowerCase().includes(q)))
    : null

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <PageHero
        icon={<BuildOutlinedIcon sx={{ fontSize: 22 }} />}
        title="Utilities"
        subtitle="Single-purpose utilities to refine finished videos"
      />

      {/* Body */}
      <Box sx={{ flex: 1, overflow: "auto", p: { xs: 2, md: 3 } }}>
        <Box sx={{ maxWidth: 1280, mx: "auto" }}>

          {/* Search */}
          <TextField
            fullWidth
            size="small"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search utilities — e.g. caption, reframe, speed, download…"
            sx={{ mb: 2.5 }}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchOutlinedIcon fontSize="small" sx={{ color: "text.secondary" }} />
                  </InputAdornment>
                ),
              },
            }}
          />

          {matches ? (
            /* Flat search results */
            matches.length ? (
              <Box>
                <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mb: 1.5 }}>
                  {matches.length} {matches.length === 1 ? "tool" : "tools"} matching “{query.trim()}”
                </Typography>
                <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)", md: "repeat(3, 1fr)" } }}>
                  {matches.map((t) => (
                    <ToolCard key={t.id} tool={t} onClick={() => pick(t.id)} />
                  ))}
                </Box>
              </Box>
            ) : (
              <Typography variant="body2" sx={{ color: "text.secondary", textAlign: "center", py: 6 }}>
                No utilities match “{query.trim()}”. Try “caption”, “audio”, “speed”, or “download”.
              </Typography>
            )
          ) : (
            <>
              {/* Categories */}
              {CATEGORIES.map((cat, i) => {
                const tools = visibleTools.filter((t) => t.category === cat.id)
                if (!tools.length) return null
                return (
                  <Box key={cat.id}>
                    <CategorySection category={cat} tools={tools} onPick={pick} />
                    {i < CATEGORIES.length - 1 && (
                      <Divider sx={{ mb: 2, opacity: 0.5 }} />
                    )}
                  </Box>
                )
              })}
            </>
          )}
        </Box>
      </Box>
    </Box>
  )
}
