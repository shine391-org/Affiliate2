import { useCallback, useEffect, useRef, useState } from "react"
import { Link as RouterLink } from "react-router-dom"
import {
  Alert, Box, Button, Checkbox, Chip, FormControl, FormControlLabel, InputLabel, MenuItem,
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
  const [autoRepair, setAutoRepair] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [jobs, setJobs] = useState([])

  const [profilesLoading, setProfilesLoading] = useState(true)
  const [profilesError, setProfilesError] = useState("")
  const profileRequest = useRef(0)
  const selectedProfile = profiles.find((profile) => profile.id === profileId)

  const loadProfiles = useCallback(async () => {
    const request = ++profileRequest.current
    setProfilesLoading(true)
    setProfilesError("")
    try {
      const { data } = await http.get("/api/settings/localization-profiles")
      if (request !== profileRequest.current) return
      const next = data?.profiles || []
      setProfiles(next)
      setProfileId((previous) => next.some((profile) => profile.id === previous)
        ? previous
        : next.find((profile) => profile.id === data?.default_profile_id)?.id || next[0]?.id || "")
    } catch (err) {
      if (request !== profileRequest.current) return
      setProfilesError(err.response?.data?.detail || "Không thể tải hồ sơ bản địa hóa. Kiểm tra kết nối và thử lại.")
    } finally {
      if (request === profileRequest.current) setProfilesLoading(false)
    }
  }, [])

  useEffect(() => {
    loadProfiles()
    return () => { profileRequest.current += 1 }
  }, [loadProfiles])

  const submit = async () => {
    if (!files.length) {
      showSnackbar("Hãy chọn ít nhất một video", "warning")
      return
    }
    if (profilesLoading || profilesError || !selectedProfile) return
    setSubmitting(true)
    try {
      const form = new FormData()
      files.forEach((file) => form.append("files", file))
      form.append("target_language", target)
      form.append("mode", mode)
      form.append("profile_id", profileId)
      form.append("auto_repair", String(autoRepair))
      const { data } = await http.post("/api/localization/batch", form, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 120000,
      })
      const created = data?.jobs || []
      created.forEach((job) => {
        startJob(job.job_id, "tool:translate", `Đã xếp hàng để bản địa hóa · ${selectedProfile.name}…`)
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
          subtitle="Chạy hồ sơ đã chọn cho tối đa 20 video"
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
              onChange={(e) => {
                const chosen = Array.from(e.target.files || [])
                if (chosen.length > 20) {
                  showSnackbar(`Chỉ nhận tối đa 20 video. Đã bỏ qua ${chosen.length - 20} tệp.`, "warning")
                }
                setFiles(chosen.slice(0, 20))
                e.target.value = ""
              }}
            />
          </Button>

          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            {files.map((file) => <Chip key={file.name + file.size} label={file.name} />)}
          </Stack>

          {profilesLoading && <Typography role="status">Đang tải hồ sơ…</Typography>}
          {profilesError && <Alert severity="error">{profilesError}</Alert>}
          {!profilesLoading && !profilesError && !profiles.length && (
            <Alert severity="warning">Chưa có hồ sơ bản địa hóa. Tạo hồ sơ trong Cài đặt rồi tải lại.</Alert>
          )}
          <Button onClick={loadProfiles} disabled={profilesLoading || submitting} sx={{ alignSelf: "flex-start" }}>
            Tải lại hồ sơ
          </Button>

          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <FormControl size="small" fullWidth disabled={profilesLoading || submitting}>
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
                <MenuItem value="full_dub">Lồng tiếng đầy đủ</MenuItem>
                <MenuItem value="captions_only">Chỉ phụ đề</MenuItem>
              </Select>
            </FormControl>
          </Stack>

          <FormControlLabel
            control={<Checkbox checked={autoRepair} onChange={(event) => setAutoRepair(event.target.checked)} />}
            label="Tự sửa lỗi có thể phục hồi (giới hạn retry; không tự duyệt QC)"
          />

          <Button
            variant="contained"
            onClick={submit}
            disabled={submitting || profilesLoading || Boolean(profilesError) || !files.length || !selectedProfile}
            sx={{ alignSelf: "flex-start" }}
          >
            {submitting ? "Đang xếp hàng…" : "Chạy hàng loạt"}
          </Button>

          {jobs.length > 0 && (
            <Box>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Tác vụ đã xếp hàng</Typography>
              <Stack spacing={0.75}>
                {jobs.map((job) => (
                  <Button key={job.job_id} component={RouterLink}
                    to={`/studio/localize?job=${encodeURIComponent(job.job_id)}`}
                    size="small" sx={{ justifyContent: "flex-start" }}>
                    {job.filename} · Mở tác vụ
                  </Button>
                ))}
              </Stack>
            </Box>
          )}
        </Stack>
      </Box>
    </Box>
  )
}
