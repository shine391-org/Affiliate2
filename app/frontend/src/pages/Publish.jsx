import { Alert, Box, Stack, Typography } from "@mui/material"
import CloudUploadOutlinedIcon from "@mui/icons-material/CloudUploadOutlined"
import PageHero from "../components/PageHero"
import useDocumentTitle from "../hooks/useDocumentTitle"

export default function Publish() {
  useDocumentTitle("Publish")
  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <PageHero
        icon={<CloudUploadOutlinedIcon sx={{ fontSize: 22 }} />}
        title="Publish"
        subtitle="Reserved workflow owner for approved outputs and destination accounts"
      />
      <Stack sx={{ p: { xs: 2, md: 3 }, maxWidth: 900 }} spacing={2}>
        <Alert severity="info">
          Publishing workflow will converge here during Phase F. Existing publish actions remain behind their current human approval gates.
        </Alert>
        <Typography variant="body2" color="text.secondary">
          This shell does not create fake uploads, schedules, accounts, or metrics.
        </Typography>
      </Stack>
    </Box>
  )
}
