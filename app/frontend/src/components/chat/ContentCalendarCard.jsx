import { Box, Paper, Typography, Chip, Divider } from "@mui/material"
import CalendarTodayIcon from "@mui/icons-material/CalendarToday"
import TrendingUpIcon from "@mui/icons-material/TrendingUp"

const platformColors = {
  youtube_shorts: "error",
  tiktok: "secondary",
  youtube_long: "primary",
}

const platformLabels = {
  youtube_shorts: "YT Shorts",
  tiktok: "TikTok",
  youtube_long: "YouTube",
}

export default function ContentCalendarCard({ calendar }) {
  if (!calendar || calendar.length === 0) return null

  return (
    <Paper
      variant="outlined"
      sx={{ p: 2, my: 1, borderRadius: 2, maxWidth: 560 }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1.5 }}>
        <CalendarTodayIcon fontSize="small" color="primary" />
        <Typography variant="subtitle2" fontWeight={700}>
          Content Calendar ({calendar.length} days)
        </Typography>
      </Box>

      {calendar.map((day, i) => (
        <Box key={day.date || i}>
          {i > 0 && <Divider sx={{ my: 1 }} />}
          <Box sx={{ display: "flex", gap: 1, alignItems: "flex-start" }}>
            <Typography
              variant="caption"
              sx={{ fontWeight: 700, minWidth: 80, mt: 0.3, color: "text.secondary" }}
            >
              {formatDate(day.date)}
            </Typography>
            <Box sx={{ flex: 1 }}>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.3 }}>
                <Typography variant="body2" fontWeight={600}>
                  {day.topic}
                </Typography>
              </Box>
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.3 }}>
                <Chip
                  label={platformLabels[day.platform] || day.platform}
                  size="small"
                  color={platformColors[day.platform] || "default"}
                  variant="outlined"
                  sx={{ height: 20, fontSize: "0.7rem" }}
                />
                {day.posting_time && (
                  <Typography variant="caption" color="text.secondary">
                    {day.posting_time}
                  </Typography>
                )}
              </Box>
              {day.why && (
                <Box sx={{ display: "flex", alignItems: "flex-start", gap: 0.3 }}>
                  <TrendingUpIcon sx={{ fontSize: 14, mt: 0.2, color: "success.main" }} />
                  <Typography variant="caption" color="text.secondary" sx={{ fontStyle: "italic" }}>
                    {day.why}
                  </Typography>
                </Box>
              )}
            </Box>
          </Box>
        </Box>
      ))}
    </Paper>
  )
}

function formatDate(dateStr) {
  if (!dateStr) return ""
  try {
    const d = new Date(dateStr + "T00:00:00")
    return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })
  } catch {
    return dateStr
  }
}
