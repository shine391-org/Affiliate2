import { useState } from "react"
import {
  Box, Typography, Chip, Button, Stack, Paper, IconButton,
  Collapse, Tooltip,
} from "@mui/material"
import NewspaperIcon from "@mui/icons-material/Newspaper"
import OpenInNewIcon from "@mui/icons-material/OpenInNew"
import BookmarkAddIcon from "@mui/icons-material/BookmarkAddOutlined"
import MovieCreationIcon from "@mui/icons-material/MovieCreationOutlined"
import ExpandMoreIcon from "@mui/icons-material/ExpandMore"
import ExpandLessIcon from "@mui/icons-material/ExpandLess"
import ThumbUpIcon from "@mui/icons-material/ThumbUpOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

function timeAgo(dateStr) {
  if (!dateStr) return ""
  const d = new Date(dateStr)
  const now = new Date()
  const diff = Math.floor((now - d) / 1000)
  if (diff < 60) return "just now"
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`
  return d.toLocaleDateString([], { month: "short", day: "numeric" })
}

function ScoreChip({ score }) {
  const color = score >= 70 ? "success" : score >= 40 ? "warning" : "error"
  return <Chip label={score.toFixed(0)} size="small" color={color} sx={{ fontWeight: 700, fontSize: "0.7rem", height: 22 }} />
}

function ArticleCard({ article, onSave, onGenerate }) {
  const [expanded, setExpanded] = useState(false)
  const a = article.analysis || {}

  return (
    <Paper variant="outlined" sx={{
      p: 1.5, borderRadius: 2, borderColor: "divider",
      "&:hover": { borderColor: "primary.light", bgcolor: "action.hover" },
      transition: "all 0.15s",
    }}>
      {/* Header row */}
      <Stack direction="row" spacing={0.75} alignItems="center" sx={{ mb: 0.5 }}>
        <Chip
          label={article.source_domain || "Unknown"}
          size="small" variant="outlined"
          sx={{ fontSize: "0.6rem", height: 18, textTransform: "lowercase" }}
        />
        <ScoreChip score={article.virality_score || 0} />
        {article.published_at && (
          <Typography variant="caption" sx={{ color: "text.secondary", fontSize: "0.65rem" }}>
            {timeAgo(article.published_at)}
          </Typography>
        )}
        {article.engagement > 0 && (
          <Typography variant="caption" sx={{ color: "text.secondary", fontSize: "0.65rem" }}>
            <ThumbUpIcon sx={{ fontSize: 11, verticalAlign: "middle", mr: 0.3 }} />{article.engagement}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        {article.url && (
          <Tooltip title="Open article" arrow>
            <IconButton size="small" onClick={() => window.open(article.url, "_blank", "noopener")}
              sx={{ p: 0.25, color: "text.secondary" }}>
              <OpenInNewIcon sx={{ fontSize: "0.85rem" }} />
            </IconButton>
          </Tooltip>
        )}
      </Stack>

      {/* Title */}
      <Typography variant="body2" sx={{ fontWeight: 600, fontSize: "0.82rem", lineHeight: 1.3, mb: 0.5 }}>
        {article.title}
      </Typography>

      {/* Hook & Angle (always visible) */}
      {a.hook && (
        <Typography variant="caption" sx={{ color: "primary.main", fontWeight: 500, display: "block", mb: 0.25, fontSize: "0.72rem" }}>
          Hook: "{a.hook}"
        </Typography>
      )}
      {a.suggested_angle && (
        <Typography variant="caption" sx={{ color: "text.secondary", fontStyle: "italic", display: "block", mb: 0.5, fontSize: "0.68rem" }}>
          Angle: {a.suggested_angle}
        </Typography>
      )}

      {/* Expandable details */}
      {(a.talking_points || a.key_quotes) && (
        <Button
          size="small" variant="text"
          onClick={() => setExpanded(!expanded)}
          endIcon={expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          sx={{ fontSize: "0.65rem", color: "text.secondary", p: 0, minHeight: 0, textTransform: "none" }}
        >
          {expanded ? "Less" : `Talking points${a.talking_points ? ` (${a.talking_points.length})` : ""}`}
        </Button>
      )}

      <Collapse in={expanded}>
        <Box sx={{ mt: 0.75, pl: 1, borderLeft: 2, borderColor: "divider" }}>
          {a.talking_points && a.talking_points.length > 0 && (
            <Box sx={{ mb: 0.75 }}>
              <Typography variant="caption" sx={{ fontWeight: 600, fontSize: "0.65rem", color: "text.secondary" }}>
                Talking Points
              </Typography>
              {a.talking_points.map((tp, i) => (
                <Typography key={i} variant="caption" sx={{ display: "block", fontSize: "0.68rem", lineHeight: 1.4, color: "text.primary" }}>
                  {i + 1}. {tp}
                </Typography>
              ))}
            </Box>
          )}
          {a.key_quotes && a.key_quotes.length > 0 && (
            <Box sx={{ mb: 0.5 }}>
              <Typography variant="caption" sx={{ fontWeight: 600, fontSize: "0.65rem", color: "text.secondary" }}>
                Key Quotes
              </Typography>
              {a.key_quotes.map((q, i) => (
                <Typography key={i} variant="caption" sx={{ display: "block", fontSize: "0.68rem", fontStyle: "italic", lineHeight: 1.4, color: "text.primary" }}>
                  "{q}"
                </Typography>
              ))}
            </Box>
          )}
          {a.suggested_title && (
            <Typography variant="caption" sx={{ color: "text.secondary", fontSize: "0.65rem" }}>
              Suggested title: <strong>{a.suggested_title}</strong>
            </Typography>
          )}
          {a.suggested_hashtags && a.suggested_hashtags.length > 0 && (
            <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mt: 0.5 }}>
              {a.suggested_hashtags.map((tag, i) => (
                <Chip key={i} label={tag} size="small" variant="outlined"
                  sx={{ fontSize: "0.55rem", height: 16 }} />
              ))}
            </Stack>
          )}
        </Box>
      </Collapse>

      {/* Action buttons */}
      <Stack direction="row" spacing={0.75} sx={{ mt: 1 }}>
        <Button size="small" variant="outlined" startIcon={<BookmarkAddIcon />}
          onClick={() => onSave(article.id)}
          sx={{ fontSize: "0.68rem", textTransform: "none" }}>
          Save to Library
        </Button>
        <Button size="small" variant="contained" startIcon={<MovieCreationIcon />}
          onClick={() => onGenerate(article.id)}
          sx={{ fontSize: "0.68rem", textTransform: "none" }}>
          Generate Video
        </Button>
      </Stack>
    </Paper>
  )
}


export default function NewsResultsCard({ results, query }) {
  const [expanded, setExpanded] = useState(false)
  const [saving, setSaving] = useState(false)
  const showSnackbar = useAppStore((s) => s.showSnackbar)

  if (!results || results.length === 0) return null

  const visible = expanded ? results : results.slice(0, 6)

  const handleSave = async (articleId) => {
    try {
      await http.post("/api/news/save", { article_ids: [articleId] })
      showSnackbar("Article saved to Library!", "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || err.message, "error")
    }
  }

  const handleGenerate = async (articleId) => {
    // Save to library first, then user can generate from Videos page
    try {
      await http.post("/api/news/save", { article_ids: [articleId] })
      showSnackbar("Article saved to Library — open Library to generate video", "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || err.message, "error")
    }
  }

  const handleSaveAll = async () => {
    setSaving(true)
    try {
      const ids = results.map(r => r.id)
      await http.post("/api/news/save", { article_ids: ids })
      showSnackbar(`${ids.length} articles saved to Library!`, "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || err.message, "error")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Box sx={{
      bgcolor: "background.paper",
      border: 1, borderColor: "divider",
      borderRadius: 3, p: 2, mb: 0.5,
    }}>
      {/* Header */}
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <NewspaperIcon sx={{ fontSize: 18, color: "primary.main" }} />
          <Typography variant="body2" sx={{ fontWeight: 700 }}>
            News Research
          </Typography>
          {query && (
            <Chip label={query} size="small" variant="outlined" sx={{ fontSize: "0.65rem", height: 20 }} />
          )}
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            {results.length} article{results.length !== 1 ? "s" : ""} found
          </Typography>
        </Stack>
        <Button size="small" variant="outlined" startIcon={<BookmarkAddIcon />}
          onClick={handleSaveAll} disabled={saving}
          sx={{ fontSize: "0.68rem", textTransform: "none" }}>
          {saving ? "Saving..." : "Save All"}
        </Button>
      </Stack>

      {/* Article list */}
      <Stack spacing={1}>
        {visible.map((r) => (
          <ArticleCard
            key={r.id}
            article={r}
            onSave={handleSave}
            onGenerate={handleGenerate}
          />
        ))}
      </Stack>

      {/* Show more */}
      {!expanded && results.length > 6 && (
        <Button size="small" variant="text" onClick={() => setExpanded(true)}
          sx={{ mt: 1, color: "text.secondary", textTransform: "none" }}>
          Show all {results.length} articles
        </Button>
      )}
    </Box>
  )
}
