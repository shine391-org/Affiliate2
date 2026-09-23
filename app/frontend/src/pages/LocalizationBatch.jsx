import { useEffect, useState } from "react"
import {
  Alert, Box, Button, Chip, FormControl, InputLabel, MenuItem,
  Select, Stack, Typography,
} from "@mui/material"
import CloudUploadOutlinedIcon from "@mui/icons-material/CloudUploadOutlined"
import TranslateOutlinedIcon from "@mui/icons-material/TranslateOutlined"
import http from "../api/http"
import PageHero from "../components/PageHero"
import useAppStore from "../store/appStore"
import useDocumentTitle from "../hooks/useDocumentTitle"

export default function LocalizationBatch() {
  useDocumentTitle("Localization Batch")
  const startJob = useAppStore((s) => s.startJob)
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [files, setFiles] = useState([])
  const [profiles, setProfiles] = useState([])
  const [profileId, setProfileId] = useState("")
  const [target, setTarget] = useState("vi")
  const [mode, setMode] = useState("full_dub")
  const [submitting, setSubmitting] = useState(false)
  const [jobs, setJobs] = useState([])

  useEffect(() => {
    http.get("/api/settings/localization-profiles")
      .then(({ data }) => {
        setProfiles(data?.profiles || [])
        setProfileId(data?.default_profile_id || "")
      })
      .catch(() => {})
  }, [])

  const submit = async () => {
    if (!files.length) {
      showSnackbar("Select at least one video", "warning")
      return
    }
    setSubmitting(true)
    try {
      const form = new FormData()
      files.forEach((file) => form.append("files", file))
      form.append("target_language", target)
      form.append("mode", mode)
      form.append("profile_id", profileId)
      const { data } = await http.post("/api/localization/batch", form, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 120000,
      })
      const created = data?.jobs || []
      created.forEach((job) => {
        startJob(job.job_id, "tool:translate", "Queued for HQ localization…")
      })
      setJobs(created)
      showSnackbar("Queued " + created.length + " localization jobs", "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Batch submit failed", "error")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <PageHero
        backTo="/tools"
        icon={<TranslateOutlinedIcon sx={{ fontSize: 22 }} />}
        title="Localization Batch"
        subtitle="Run the same proven HQ localization pipeline across up to 20 videos"
      />
      <Box sx={{ flex: 1, overflow: "auto", p: 3 }}>
        <Stack spacing={2.5} sx={{ maxWidth: 900, mx: "auto" }}>
          <Alert severity="info" variant="outlined">
            Every item gets its own persisted stage manifest, resume state, QC report
            and Expert workspace. Batch does not use a cheaper second engine.
          </Alert>

          <Button
            component="label"
            variant="outlined"
            startIcon={<CloudUploadOutlinedIcon />}
            sx={{ alignSelf: "flex-start" }}
          >
            Select videos
            <input
              hidden
              type="file"
              multiple
              accept=".mp4,.mov,.mkv,.webm,.m4v"
              onChange={(e) => setFiles(Array.from(e.target.files || []).slice(0, 20))}
            />
          </Button>

          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            {files.map((file) => <Chip key={file.name + file.size} label={file.name} />)}
          </Stack>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <FormControl size="small" fullWidth>
              <InputLabel>Profile</InputLabel>
              <Select value={profileId} label="Profile" onChange={(e) => setProfileId(e.target.value)}>
                {profiles.map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControl size="small" fullWidth>
              <InputLabel>Target</InputLabel>
              <Select value={target} label="Target" onChange={(e) => setTarget(e.target.value)}>
                <MenuItem value="vi">Vietnamese</MenuItem>
                <MenuItem value="en">English</MenuItem>
                <MenuItem value="zh">Chinese</MenuItem>
                <MenuItem value="ja">Japanese</MenuItem>
                <MenuItem value="ko">Korean</MenuItem>
              </Select>
            </FormControl>
            <FormControl size="small" fullWidth>
              <InputLabel>Mode</InputLabel>
              <Select value={mode} label="Mode" onChange={(e) => setMode(e.target.value)}>
                <MenuItem value="full_dub">HQ full dub</MenuItem>
                <MenuItem value="captions_only">Captions only</MenuItem>
              </Select>
            </FormControl>
          </Stack>

          <Button
            variant="contained"
            onClick={submit}
            disabled={submitting || !files.length || !profileId}
            sx={{ alignSelf: "flex-start" }}
          >
            {submitting ? "Queuing…" : "Run batch"}
          </Button>

          {jobs.length > 0 && (
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Queued jobs</Typography>
              <Stack spacing={0.75}>
                {jobs.map((job) => (
                  <Typography key={job.job_id} variant="caption" color="text.secondary">
                    {job.filename + " · " + job.job_id}
                  </Typography>
                ))}
              </Stack>
            </Box>
          )}
        </Stack>
      </Box>
    </Box>
  )
}
