import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogContent, DialogTitle, Drawer,
  FormControl, FormControlLabel, InputLabel, MenuItem, Paper, Radio, RadioGroup,
  Select, Stack, Step, StepButton, Stepper, Typography,
} from "@mui/material"
import BatchPredictionOutlinedIcon from "@mui/icons-material/BatchPredictionOutlined"
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined"
import TuneOutlinedIcon from "@mui/icons-material/TuneOutlined"
import { useNavigate, useSearchParams } from "react-router-dom"
import http from "../api/http"
import useAppStore from "../store/appStore"
import LocalizationExpertPanel from "../components/tools/LocalizationExpertPanel"
import LocalizationBatch from "./LocalizationBatch"
import { LOCALIZE_STEPS, localizeStepForDetail } from "../studioModel"

const stepIndex = (value) => Math.max(0, LOCALIZE_STEPS.findIndex((item) => item.value === value))
const STAGE_STATUS_LABELS = { pending: "đang chờ", running: "đang chạy", complete: "hoàn tất", failed: "thất bại", skipped: "đã bỏ qua" }
const QC_STATUS_LABELS = { READY: "Sẵn sàng", FAILED: "Thất bại", NEEDS_REVIEW: "Cần duyệt", COMPLETE: "Hoàn tất" }
const formatMs = (value) => `${(Number(value || 0) / 1000).toFixed(2)}s`

function StageChip({ name, record }) {
  const status = record?.status || "pending"
  return <Chip size="small" variant="outlined" color={status === "complete" ? "success" : status === "failed" ? "error" : "default"} label={`${name}: ${STAGE_STATUS_LABELS[status] || status}`} />
}

export default function StudioLocalize({ source, sourcesLoading }) {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const startJob = useAppStore((s) => s.startJob)
  const [profiles, setProfiles] = useState([])
  const [profileId, setProfileId] = useState("")
  const [target, setTarget] = useState("vi")
  const [mode, setMode] = useState("full_dub")
  const [reviewTranslation, setReviewTranslation] = useState(true)
  const [detail, setDetail] = useState(null)
  const [detailError, setDetailError] = useState("")
  const [starting, setStarting] = useState(false)
  const [batchOpen, setBatchOpen] = useState(params.get("batch") === "1")
  const [expertOpen, setExpertOpen] = useState(false)
  const jobId = params.get("job") || ""
  const requestedStep = params.get("step") || ""
  const activeStep = LOCALIZE_STEPS.some((item) => item.value === requestedStep)
    ? requestedStep
    : localizeStepForDetail(detail)

  useEffect(() => {
    let alive = true
    http.get("/api/settings/localization-profiles").then(({ data }) => {
      if (!alive) return
      setProfiles(data?.profiles || [])
      setProfileId(data?.default_profile_id || "")
    }).catch(() => {})
    return () => { alive = false }
  }, [])

  useEffect(() => {
    if (!jobId) { setDetail(null); setDetailError("") }
  }, [jobId])

  const loadDetail = useCallback(async () => {
    if (!jobId) { setDetail(null); setDetailError(""); return }
    try {
      const { data } = await http.get(`/api/localization/jobs/${jobId}`)
      setDetail(data)
      setDetailError("")
    } catch (error) {
      if (error.response?.status === 404) { setDetail(null); setDetailError(""); return }
      if (error.response?.status !== 404) setDetailError(error.response?.data?.detail || error.message)
    }
  }, [jobId])

  useEffect(() => {
    let alive = true
    loadDetail()
    if (!jobId) return undefined
    const timer = setInterval(() => { if (alive) loadDetail() }, 3000)
    return () => { alive = false; clearInterval(timer) }
  }, [jobId, loadDetail])

  const selectedProfile = useMemo(() => profiles.find((item) => item.id === profileId) || null, [profiles, profileId])
  const effective = detail?.manifest?.effective_profile || selectedProfile || {}
  const stages = detail?.manifest?.stages || {}
  const segments = detail?.transcript?.segments || []
  const chunks = detail?.chunks || []
  const qc = detail?.qc
  const activeLocalization = Boolean(jobId) && (
    !detail || ["pending", "running"].includes(detail?.job_status)
  )

  const setStep = (value) => setParams((previous) => {
    const next = new URLSearchParams(previous)
    next.set("step", value)
    return next
  })

  const start = async () => {
    if (!source) { showSnackbar("Hãy chọn nguồn từ Thư viện trước", "warning"); return }
    setStarting(true)
    try {
      const form = new FormData()
      form.append("downloaded_video_id", source.id)
      form.append("target_language", target)
      form.append("mode", mode)
      form.append("profile_id", profileId)
      form.append("review_translation", String(mode === "full_dub" && reviewTranslation))
      const { data } = await http.post("/api/tools/translate", form, { headers: { "Content-Type": "multipart/form-data" }, timeout: 120000 })
      startJob(data.job_id, "tool:translate", "Đang bắt đầu bản địa hóa HQ…")
      setParams((previous) => {
        const next = new URLSearchParams(previous)
        next.set("job", data.job_id)
        next.set("step", "source")
        return next
      })
      showSnackbar(
        data.reused
          ? "Tác vụ giống hệt đang chạy — đã mở tác vụ hiện tại thay vì tạo bản sao."
          : "Đã bắt đầu tác vụ bản địa hóa từ nguồn đã chọn trong Thư viện",
        data.reused ? "info" : "success",
      )
    } catch (error) {
      showSnackbar(error.response?.data?.detail || "Không thể bắt đầu bản địa hóa", "error")
    } finally {
      setStarting(false)
    }
  }

  const sourcePanel = (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "minmax(0, 1.4fr) minmax(300px, .6fr)" }, gap: 2 }}>
      <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
        {source ? (
          <Stack spacing={1.25}>
            <Box component="video" controls preload="metadata" src={`/api/downloaded/${source.id}/stream`} sx={{ width: "100%", maxHeight: 460, bgcolor: "#000", borderRadius: 1.5 }} />
            <Typography variant="h6" sx={{ fontWeight: 700 }}>{source.title || source.id}</Typography>
            <Typography variant="body2" color="text.secondary">
              {[source.platform, source.duration_seconds ? `${source.duration_seconds}s` : null, source.media_metadata?.width ? `${source.media_metadata.width}×${source.media_metadata.height}` : null].filter(Boolean).join(" · ")}
            </Typography>
            {source.source_url && <Typography variant="caption" color="text.secondary" sx={{ wordBreak: "break-all" }}>{source.source_url}</Typography>}
          </Stack>
        ) : (
          <Alert severity="info">{sourcesLoading ? "Đang tải nguồn từ Thư viện…" : "Chọn nguồn ở phần đầu Studio. Đổi chế độ vẫn giữ nguyên nguồn và không nhập lại."}</Alert>
        )}
      </Paper>
      <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
        <Stack spacing={2}>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Cấu hình tác vụ</Typography>
          <FormControl size="small" fullWidth><InputLabel>Hồ sơ bản địa hóa</InputLabel><Select value={profileId} label="Hồ sơ bản địa hóa" onChange={(e) => setProfileId(e.target.value)}>{profiles.map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}</Select></FormControl>
          <FormControl size="small" fullWidth><InputLabel>Ngôn ngữ đích</InputLabel><Select value={target} label="Ngôn ngữ đích" onChange={(e) => setTarget(e.target.value)}><MenuItem value="vi">Tiếng Việt</MenuItem><MenuItem value="en">Tiếng Anh</MenuItem><MenuItem value="zh">Tiếng Trung</MenuItem><MenuItem value="ja">Tiếng Nhật</MenuItem><MenuItem value="ko">Tiếng Hàn</MenuItem></Select></FormControl>
          <RadioGroup value={mode} onChange={(e) => setMode(e.target.value)}><FormControlLabel value="full_dub" control={<Radio size="small" />} label="Lồng tiếng HQ đầy đủ" /><FormControlLabel value="captions_only" control={<Radio size="small" />} label="Chỉ phụ đề đã dịch" /></RadioGroup>
          {mode === "full_dub" && <FormControlLabel control={<Checkbox checked={reviewTranslation} onChange={(e) => setReviewTranslation(e.target.checked)} />} label="Tạm dừng sau C6 để duyệt bản dịch" />}
          {selectedProfile && <Alert severity="info" variant="outlined">C6 <strong>{selectedProfile.translation_model || "mặc định kết nối"}</strong> · C7 <strong>{selectedProfile.rewrite_model || "mặc định kết nối"}</strong>. Đây là hai mô hình độc lập.</Alert>}
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
            <Button variant="outlined" onClick={() => navigate("/settings?section=localization")}>Quản lý hồ sơ</Button>
            <Button variant="contained" onClick={start} disabled={!source || !profileId || starting || activeLocalization}>
              {starting ? "Đang bắt đầu…" : activeLocalization ? "Tác vụ này đang xử lý…" : "Bắt đầu bản địa hóa"}
            </Button>
          </Stack>
        </Stack>
      </Paper>
    </Box>
  )

  const transcriptPanel = segments.length ? (
    <Stack spacing={1}>{segments.map((segment) => <Paper key={segment.id} variant="outlined" sx={{ p: 1.5, borderRadius: 2 }}><Stack direction={{ xs: "column", sm: "row" }} spacing={1}><Chip size="small" label={segment.id} /><Typography variant="caption" color="text.secondary">{segment.speaker_id || "người nói —"} · {formatMs(segment.source_start_ms)}–{formatMs(segment.source_end_ms)}</Typography></Stack><Typography variant="body2" sx={{ mt: 1 }}>{segment.source_text}</Typography><Typography variant="caption" color="text.secondary">Căn chỉnh: {segment.alignment_status || (effective.alignment_mode === "whisperx" ? "đã cấu hình WhisperX" : "không yêu cầu")}</Typography></Paper>)}</Stack>
  ) : <Alert severity="info">Bản chép lời xuất hiện tại đây sau C5. ASR {effective.asr_model || effective.asr_quality || "mặc định hồ sơ"} · alignment {effective.alignment_mode || "off"} · diarization {effective.diarization_mode || "off"}.</Alert>

  const translationPanel = (
    <Stack spacing={2}>
      {detail?.translation_review_pending && <Alert severity="warning">Đang chờ duyệt bản dịch C6. C7/TTS chưa bắt đầu. Hãy sửa các dòng chưa chính xác rồi chủ động tiếp tục lồng tiếng.</Alert>}
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "minmax(220px,.7fr) minmax(0,1.6fr) minmax(260px,.7fr)" }, gap: 2 }}>
        <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2 }}>{source ? <Box component="video" controls src={`/api/downloaded/${source.id}/stream`} sx={{ width: "100%", bgcolor: "#000", borderRadius: 1 }} /> : <Typography variant="body2">Không có nguồn</Typography>}</Paper>
        <Paper variant="outlined" sx={{ p: 1.5, borderRadius: 2, maxHeight: 560, overflow: "auto" }}><Stack spacing={1}>{segments.map((segment) => <Box key={segment.id} sx={{ pb: 1, borderBottom: 1, borderColor: "divider" }}><Typography variant="caption" color="text.secondary">{segment.id} · {formatMs(segment.source_start_ms)}–{formatMs(segment.source_end_ms)}</Typography><Typography variant="body2">{segment.source_text}</Typography><Typography variant="body2" sx={{ color: "primary.main", fontWeight: 600 }}>{segment.translated_text || "Chưa dịch"}</Typography></Box>)}</Stack></Paper>
        <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}><Typography variant="subtitle2">Bản dịch C6</Typography><Typography variant="body2" sx={{ mt: 1 }}>Mô hình: <strong>{effective.translation_model || "mặc định kết nối"}</strong></Typography><Typography variant="body2">Nhà cung cấp: <strong>{effective.translation_provider || "kết nối trong Cài đặt"}</strong></Typography><Typography variant="body2">Preset: <strong>{effective.translation_preset || "natural_vietnamese"}</strong></Typography><Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>{effective.translation_instructions || "Không có hướng dẫn riêng"}</Typography><Typography variant="caption" color="text.secondary">Thuật ngữ: {Object.keys(effective.glossary || {}).length} mục</Typography></Paper>
      </Box>
      {jobId && (
        <Button variant="outlined" startIcon={<TuneOutlinedIcon />} onClick={() => setExpertOpen(true)} sx={{ alignSelf: "flex-start" }}>
          Review / edit translation
        </Button>
      )}
    </Stack>
  )

  const voicePanel = (
    <Stack spacing={2}><Alert severity="info" variant="outlined">Mô hình viết lại thời lượng C7 <strong>{effective.rewrite_model || "mặc định kết nối"}</strong> tách biệt với mô hình dịch C6.</Alert><Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "repeat(3, 1fr)" }, gap: 1.5 }}><Paper variant="outlined" sx={{ p: 2 }}><Typography variant="subtitle2">Giọng đọc</Typography><Typography variant="body2">{effective.tts_provider || "mặc định nhà cung cấp"} · {effective.tts_voice || "giọng của hồ sơ"}</Typography></Paper><Paper variant="outlined" sx={{ p: 2 }}><Typography variant="subtitle2">Giới hạn nhịp</Typography><Typography variant="body2">Ưu tiên {effective.dub_speed_accept || 1.15}× · tối đa {effective.dub_speed_max || 1.3}×</Typography></Paper><Paper variant="outlined" sx={{ p: 2 }}><Typography variant="subtitle2">Viết lại</Typography><Typography variant="body2">{effective.dub_rewrite_attempts ?? 2} lần thử</Typography></Paper></Box>{chunks.length > 0 && <Stack spacing={1}>{chunks.map((chunk) => <Paper variant="outlined" key={chunk.id} sx={{ p: 1.25 }}><Typography variant="body2">Đoạn {chunk.id} · {chunk.speaker_id || "người nói —"} · tốc độ {Number(chunk.speed_factor || 1).toFixed(2)}×</Typography></Paper>)}</Stack>}{jobId && <Button variant="outlined" startIcon={<TuneOutlinedIcon />} onClick={() => setExpertOpen(true)} sx={{ alignSelf: "flex-start" }}>Nhịp / giọng nâng cao</Button>}</Stack>
  )

  const renderPanel = <Stack spacing={2}><Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "repeat(2, 1fr)" }, gap: 1.5 }}><Paper variant="outlined" sx={{ p: 2 }}><Typography variant="subtitle2">Âm thanh</Typography><Typography variant="body2">Chế độ: {effective.audio_mode || "mặc định hồ sơ"}</Typography><Typography variant="body2">Nền: {effective.preserve_background === false ? "thay thế" : "giữ lại"}</Typography><Typography variant="body2">Giọng: {effective.tts_voice || "giọng của hồ sơ"}</Typography></Paper><Paper variant="outlined" sx={{ p: 2 }}><Typography variant="subtitle2">Hình ảnh & phụ đề</Typography><Typography variant="body2">Kiểu: {effective.caption_style || "viral"}</Typography><Typography variant="body2">Tỷ lệ: {effective.target_aspect || "nguồn"}</Typography><Typography variant="body2">Kết xuất: {effective.render_profile || "hq"}</Typography></Paper></Box><Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap><StageChip name="Trộn âm thanh" record={stages.audio_mix} /><StageChip name="Kết xuất cuối" record={stages.final_render} /></Stack>{jobId && <Button variant="outlined" startIcon={<TuneOutlinedIcon />} onClick={() => setExpertOpen(true)} sx={{ alignSelf: "flex-start" }}>Điều khiển kết xuất nâng cao</Button>}</Stack>

  const qcPanel = <Stack spacing={2}>{qc ? <Alert severity={qc.status === "READY" ? "success" : qc.status === "FAILED" ? "error" : "warning"}><strong>{QC_STATUS_LABELS[qc.status] || qc.status}</strong> · {(qc.failures || []).length} lỗi · {(qc.warnings || []).length} cảnh báo</Alert> : <Alert severity="info">QC chưa hoàn tất.</Alert>}{qc && [...(qc.failures || []), ...(qc.warnings || [])].map((issue, index) => <Paper key={index} variant="outlined" sx={{ p: 1.5 }}><Typography variant="body2">{typeof issue === "string" ? issue : issue.message || JSON.stringify(issue)}</Typography></Paper>)}<Stack direction={{ xs: "column", sm: "row" }} spacing={1}>{qc?.status === "READY" && <Button variant="contained" onClick={() => navigate("/library")}>Mở đầu ra cuối trong Thư viện</Button>}{qc?.status && qc.status !== "READY" && <Button variant="contained" color="warning" startIcon={<FactCheckOutlinedIcon />} onClick={() => navigate(`/review?job=${encodeURIComponent(jobId)}`)}>Mở trong Duyệt</Button>}</Stack>{jobId && <Button variant="outlined" startIcon={<TuneOutlinedIcon />} onClick={() => setExpertOpen(true)} sx={{ alignSelf: "flex-start" }}>Mở duyệt nâng cao</Button>}</Stack>

  const panels = { source: sourcePanel, transcript: transcriptPanel, translation: translationPanel, "voice-timing": voicePanel, render: renderPanel, qc: qcPanel }

  return (
    <Box sx={{ p: { xs: 1.5, md: 2.5 }, maxWidth: 1500, mx: "auto" }}>
      <Stack spacing={2}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
          <Box sx={{ flex: 1 }}><Typography variant="h6" sx={{ fontWeight: 800 }}>Bản địa hóa</Typography><Typography variant="caption" color="text.secondary">Một tác vụ Phase C xuyên suốt Nguồn → Bản chép lời → Bản dịch → Giọng & Nhịp → Kết xuất → QC</Typography></Box>
          <Button variant="outlined" startIcon={<BatchPredictionOutlinedIcon />} onClick={() => setBatchOpen(true)}>Chạy hàng loạt</Button>
        </Stack>
        <Stepper nonLinear activeStep={stepIndex(activeStep)} sx={{ overflowX: "auto", pb: 0.5 }}>
          {LOCALIZE_STEPS.map((item) => <Step key={item.value}><StepButton onClick={() => setStep(item.value)}>{item.label}</StepButton></Step>)}
        </Stepper>
        {detailError && <Alert severity="warning">{detailError}</Alert>}
        {jobId && <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap><Chip label={`Tác vụ ${jobId}`} size="small" /><StageChip name="Bản dịch" record={stages.translation} /><StageChip name="Lồng tiếng" record={stages.dubbing} /><StageChip name="QC" record={stages.qc} /><Box sx={{ flex: 1 }} /><Button size="small" variant="outlined" startIcon={<TuneOutlinedIcon />} onClick={() => setExpertOpen(true)}>Nâng cao / Chuyên gia</Button></Stack>}
        {panels[activeStep]}
      </Stack>
      <Dialog open={batchOpen} onClose={() => setParams((previous) => { const next = new URLSearchParams(previous); next.delete("batch"); return next }, { replace: true })} maxWidth="md" fullWidth><DialogTitle>Chạy bản địa hóa hàng loạt</DialogTitle><DialogContent><LocalizationBatch embedded /></DialogContent></Dialog>
      <Drawer
        anchor="right"
        open={expertOpen}
        onClose={() => setExpertOpen(false)}
        PaperProps={{ sx: { width: { xs: "100%", sm: 620 }, maxWidth: "100%" } }}
      >
        <Box sx={{ p: 2, borderBottom: 1, borderColor: "divider" }}>
          <Typography variant="h6" sx={{ fontWeight: 800 }}>Nâng cao / Chuyên gia</Typography>
          <Typography variant="caption" color="text.secondary">Điều khiển chuyên sâu về bản dịch, nhịp, giọng và chạy lại cho tác vụ đã lưu này.</Typography>
        </Box>
        <Box sx={{ p: 2, overflow: "auto" }}>
          {jobId ? <LocalizationExpertPanel jobId={jobId} /> : <Alert severity="info">Hãy bắt đầu hoặc mở một tác vụ bản địa hóa trước.</Alert>}
        </Box>
      </Drawer>
    </Box>
  )
}
