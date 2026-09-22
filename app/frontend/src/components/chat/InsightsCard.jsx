import { Box, Typography, Card, CardContent, Button, Stack } from "@mui/material"
import MovieCreationIcon from "@mui/icons-material/MovieCreationOutlined"
import LightbulbIcon from "@mui/icons-material/LightbulbOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

export default function InsightsCard({ videos }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  if (!videos || videos.length === 0) return null

  const handleGenerate = async (id) => {
    try {
      const { data } = await http.post(`/api/downloaded/${id}/generate`)
      showSnackbar("Video generation started!", "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || err.message, "error")
    }
  }

  return (
    <Box sx={{
      bgcolor: "background.paper",
      border: 1, borderColor: "divider",
      borderRadius: 3, p: 2, mb: 0.5,
      boxShadow: (theme) => theme.customShadows?.sm,
      transition: "all 0.2s ease",
    }}>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
        <LightbulbIcon fontSize="small" sx={{ color: "warning.main" }} />
        <Typography variant="body2" sx={{ fontWeight: 600, color: "text.primary" }}>
          Analysis Complete — {videos.length} video{videos.length !== 1 ? "s" : ""} analyzed
        </Typography>
      </Stack>

      <Stack spacing={1}>
        {videos.map((v) => (
          <Card key={v.id} elevation={0} sx={{ border: 1, borderColor: "divider", borderRadius: 2 }}>
            <CardContent sx={{ p: 1.5, "&:last-child": { pb: 1.5 } }}>
              <Typography variant="body2" sx={{ fontWeight: 500, mb: 0.5, color: "text.primary" }}>
                {v.title || "Untitled"}
              </Typography>

              {v.insights && (
                <Stack spacing={0.25} sx={{ mb: 1 }}>
                  {v.insights.hook && (
                    <Typography variant="caption">
                      <Box component="span" sx={{ color: "primary.main", fontWeight: 500 }}>Hook: </Box>
                      {v.insights.hook}
                    </Typography>
                  )}
                  {v.insights.suggested_angle && (
                    <Typography variant="caption">
                      <Box component="span" sx={{ color: "info.main", fontWeight: 500 }}>Your angle: </Box>
                      {v.insights.suggested_angle}
                    </Typography>
                  )}
                  {v.insights.why_viral && (
                    <Typography variant="caption" sx={{ color: "text.secondary" }}>
                      {v.insights.why_viral}
                    </Typography>
                  )}
                </Stack>
              )}

              <Button size="small" variant="contained" startIcon={<MovieCreationIcon />}
                onClick={() => handleGenerate(v.id)}>
                Generate Video
              </Button>
            </CardContent>
          </Card>
        ))}
      </Stack>
    </Box>
  )
}
