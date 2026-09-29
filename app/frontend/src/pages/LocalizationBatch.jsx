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

export default function LocalizationBatch({ embedded = false }) {
  useDocumentTitle("Bản địa hóa hàng loạt")
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
      showSnackbar("Hãy chọn ít nhất một video", "warning")
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
        startJob(job.job_id, "tool:translate", "Đã xếp hàng để bản địa hóa HQ…")
      })
      setJobs(created)
      showSnackbar("Đã xếp hàng " + created.length + " tác vụ bản địa hóa", "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Gửi hàng loạt thất bại", "error")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {!embedded && (
        <PageHero
          backTo="/studio/localize"
          icon={<TranslateOutlinedIcon sx={{ fontSize: 22 }} />}
          title="Bản địa hóa hàng loạt"
          subtitle="Chạy cùng quy trình bản địa hóa HQ cho tối đa 20 video"
        />
      )}
      <Box sx={{ flex: 1, overflow: "auto", p: embedded ? 1 : 3 }}>
        <Stack spacing={2.5} sx={{ maxWidth: 900, mx: "auto" }}>
          <Alert severity="info" variant="outlined">
            Mỗi mục có manifest từng bước, trạng thái tiếp tục và báo cáo QC riêng
            cùng vùng làm việc nâng cao. Chế độ hàng loạt không dùng engine rẻ hơn.
          </Alert>

          <Button
            component="label"
            variant="outlined"
            startIcon={<CloudUploadOutlinedIcon />}
            sx={{ alignSelf: "flex-start" }}
          >
            Chọn video
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
              <InputLabel>Hồ sơ</InputLabel>
              <Select value={profileId} label="Hồ sơ" onChange={(e) => setProfileId(e.target.value)}>
                {profiles.map((p) => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControl size="small" fullWidth>
              <InputLabel>Ngôn ngữ đích</InputLabel>
              <Select value={target} label="Ngôn ngữ đích" onChange={(e) => setTarget(e.target.value)}>
                <MenuItem value="vi">Tiếng Việt</MenuItem>
                <MenuItem value="en">Tiếng Anh</MenuItem>
                <MenuItem value="zh">Tiếng Trung</MenuItem>
                <MenuItem value="ja">Tiếng Nhật</MenuItem>
                <MenuItem value="ko">Tiếng Hàn</MenuItem>
              </Select>
            </FormControl>
            <FormControl size="small" fullWidth>
              <InputLabel>Chế độ</InputLabel>
              <Select value={mode} label="Chế độ" onChange={(e) => setMode(e.target.value)}>
                <MenuItem value="full_dub">Lồng tiếng HQ đầy đủ</MenuItem>
                <MenuItem value="captions_only">Chỉ phụ đề</MenuItem>
              </Select>
            </FormControl>
          </Stack>

          <Button
            variant="contained"
            onClick={submit}
            disabled={submitting || !files.length || !profileId}
            sx={{ alignSelf: "flex-start" }}
          >
            {submitting ? "Đang xếp hàng…" : "Chạy hàng loạt"}
          </Button>

          {jobs.length > 0 && (
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Tác vụ đã xếp hàng</Typography>
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
