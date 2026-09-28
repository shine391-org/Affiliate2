import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogContent, DialogTitle, Divider, Drawer,
  FormControl, FormControlLabel, InputLabel, MenuItem, Paper, Radio, RadioGroup,
  Select, Slider, Stack, Step, StepButton, Stepper, Typography,
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
import {
  buildLocalizationRenderPayload,
  canRerenderLocalization,
  captionPreviewStyle,
  hydrateLocalizationJobConfig,
  localizationJobFailure,
  maskPreset,
  pickPreviewReframeMethod,
  previewObjectFit,
  resolveProfileTargetAspect,
} from "../localizationPreview"

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

  const [targetAspect, setTargetAspect] = useState("9:16")
  const [aspectOverridden, setAspectOverridden] = useState(false)
  const [enableMask, setEnableMask] = useState(false)
  const [maskMode, setMaskMode] = useState("blur")
  const [maskBox, setMaskBox] = useState(() => maskPreset("bottom"))
  const [captionPlacementMode, setCaptionPlacementMode] = useState("mask_region")
  const [captionVerticalOffset, setCaptionVerticalOffset] = useState(0)
  const [rerendering, setRerendering] = useState(false)
  const [hydratedJobId, setHydratedJobId] = useState("")

  useEffect(() => {
    let alive = true
    http.get("/api/settings/localization-profiles").then(({ data }) => {
      if (!alive) return
      setProfiles(data?.profiles || [])
      setProfileId((current) => current || data?.default_profile_id || "")
    }).catch(() => {})
    return () => { alive = false }
  }, [])

  const selectedProfile = useMemo(() => profiles.find((item) => item.id === profileId) || null, [profiles, profileId])

  useEffect(() => {
    setTargetAspect((current) => resolveProfileTargetAspect({
      currentAspect: current,
      selectedProfile,
      aspectOverridden,
      jobConfigHydrated: Boolean(jobId && hydratedJobId === jobId),
    }))
  }, [selectedProfile, aspectOverridden, hydratedJobId, jobId])

  const previewAspect = useMemo(() => {
    if (targetAspect === "9:16") return "9 / 16"
    if (targetAspect === "16:9") return "16 / 9"
    if (targetAspect === "1:1") return "1 / 1"
    if (source?.media_metadata?.width && source?.media_metadata?.height) {
      return `${source.media_metadata.width} / ${source.media_metadata.height}`
    }
    return "16 / 9"
  }, [targetAspect, source])

  const applyMaskPreset = (preset) => setMaskBox(maskPreset(preset))

  useEffect(() => {
    setDetail(null)
    setDetailError("")
    setHydratedJobId("")
    setAspectOverridden(false)
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

  const effective = detail?.manifest?.effective_profile || selectedProfile || {}
  const stages = detail?.manifest?.stages || {}
  const segments = detail?.transcript?.segments || []
  const chunks = detail?.chunks || []
  const qc = detail?.qc
  const activeLocalization = Boolean(jobId)
    && ["pending", "running"].includes(detail?.job_status)
  const canRerender = canRerenderLocalization(detail)
  const jobFailure = localizationJobFailure(detail)

  useEffect(() => {
    if (!jobId || hydratedJobId === jobId || !detail?.manifest) return
    const config = hydrateLocalizationJobConfig(detail)
    if (!config || config.jobId !== jobId) return
    if (config.profileId) setProfileId(config.profileId)
    setTarget(config.target)
    setMode(config.mode)
    setReviewTranslation(config.reviewTranslation)
    setTargetAspect(config.targetAspect)
    setEnableMask(config.enableMask)
    if (config.maskBox) setMaskBox(config.maskBox)
    setMaskMode(config.maskMode)
    setCaptionPlacementMode(config.captionPlacementMode)
    setCaptionVerticalOffset(config.captionVerticalOffset)
    setHydratedJobId(jobId)
  }, [detail, hydratedJobId, jobId])

  const previewMethod = useMemo(() => pickPreviewReframeMethod(
    source?.media_metadata?.width,
    source?.media_metadata?.height,
    targetAspect,
  ), [source, targetAspect])
  const captionPlacement = useMemo(() => ({
    mode: enableMask ? captionPlacementMode : "default",
    vertical_offset: captionVerticalOffset,
  }), [captionPlacementMode, captionVerticalOffset, enableMask])
  const captionSampleStyle = useMemo(
    () => captionPreviewStyle(maskBox, captionPlacement),
    [captionPlacement, maskBox],
  )
  const renderPayload = useMemo(() => buildLocalizationRenderPayload({
    enableMask,
    maskBox,
    maskMode,
    captionPlacementMode,
    captionVerticalOffset,
    targetAspect,
  }), [captionPlacementMode, captionVerticalOffset, enableMask, maskBox, maskMode, targetAspect])

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
      if (targetAspect) {
        form.append("target_aspect", targetAspect)
      }
      if (renderPayload.mask_box) form.append("mask_box", JSON.stringify(renderPayload.mask_box))
      form.append("caption_placement", JSON.stringify(renderPayload.caption_placement))
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

  const rerender = async () => {
    if (!jobId) return
    setRerendering(true)
    try {
      await http.post(`/api/localization/jobs/${jobId}/rerender`, renderPayload)
      startJob(jobId, "tool:translate", "Đang kết xuất lại video và QC…")
      showSnackbar("Đã cập nhật vùng che/Vietsub; chỉ chạy lại Kết xuất cuối và QC", "info")
      await loadDetail()
    } catch (error) {
      showSnackbar(error.response?.data?.detail || "Không thể kết xuất lại video", "error")
    } finally {
      setRerendering(false)
    }
  }

  const sourcePanel = (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "minmax(0, 1.4fr) minmax(320px, .6fr)" }, gap: 2 }}>
      <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
        {source ? (
          <Stack spacing={1.25}>
            <Box sx={{ position: "relative", width: "100%", maxHeight: 460, bgcolor: "#000", borderRadius: 1.5, overflow: "hidden", display: "flex", justifyContent: "center", alignItems: "center" }}>
              <Box sx={{ position: "relative", width: "auto", height: 460, maxHeight: 460, maxWidth: "100%", aspectRatio: previewAspect, bgcolor: "#050505", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
                {previewMethod === "blur_fill" && (
                  <Box component="video" muted preload="metadata" src={`/api/downloaded/${source.id}/stream`} sx={{ position: "absolute", inset: -12, width: "calc(100% + 24px)", height: "calc(100% + 24px)", objectFit: "cover", filter: "blur(18px) brightness(.65)", display: "block" }} />
                )}
                <Box component="video" controls preload="metadata" src={`/api/downloaded/${source.id}/stream`} sx={{ position: "relative", width: "100%", height: "100%", objectFit: previewObjectFit(previewMethod), display: "block", zIndex: 1 }} />
                {enableMask && (
                  <Box
                    sx={{
                      position: "absolute",
                      left: `${(maskBox.x * 100).toFixed(1)}%`,
                      top: `${(maskBox.y * 100).toFixed(1)}%`,
                      width: `${(maskBox.width * 100).toFixed(1)}%`,
                      height: `${(maskBox.height * 100).toFixed(1)}%`,
                      border: "2px dashed #f59e0b",
                      bgcolor: maskMode === "solid" ? "rgba(0,0,0,0.85)" : "rgba(245, 158, 11, 0.35)",
                      backdropFilter: maskMode === "blur" ? "blur(6px)" : "none",
                      pointerEvents: "none",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: "#fff",
                      fontSize: "0.75rem",
                      fontWeight: 700,
                      textShadow: "0 1px 2px rgba(0,0,0,0.8)",
                      zIndex: 2,
                    }}
                  >
                    [Vùng che chữ: {maskMode === "solid" ? "Hộp đen" : "Làm mờ"}]
                  </Box>
                )}
                {captionSampleStyle && (
                  <Box sx={{ position: "absolute", ...captionSampleStyle, zIndex: 3, color: "#fff", fontWeight: 900, fontSize: "clamp(.7rem, 2vw, 1.05rem)", textAlign: "center", textShadow: "0 2px 3px #000, 0 0 5px #000", pointerEvents: "none" }}>
                    Phụ đề tiếng Việt
                  </Box>
                )}
              </Box>
            </Box>
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
          <FormControl size="small" fullWidth>
            <InputLabel>Hồ sơ bản địa hóa</InputLabel>
            <Select value={profileId} label="Hồ sơ bản địa hóa" onChange={(e) => setProfileId(e.target.value)}>
              {profiles.map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl size="small" fullWidth>
            <InputLabel>Tỷ lệ khung hình</InputLabel>
            <Select
              value={targetAspect}
              label="Tỷ lệ khung hình"
              onChange={(e) => {
                const nextVal = e.target.value
                setTargetAspect(nextVal)
                setAspectOverridden(true)
                if (enableMask) {
                  applyMaskPreset("bottom")
                  showSnackbar("Đã đổi tỷ lệ khung hình, vui lòng kiểm tra lại vùng che chữ.", "info")
                }
              }}
            >
              <MenuItem value="9:16">Dọc (9:16 — TikTok / Reels / Shorts)</MenuItem>
              <MenuItem value="16:9">Ngang (16:9 — YouTube ngang)</MenuItem>
              <MenuItem value="1:1">Vuông (1:1 — Feed)</MenuItem>
              <MenuItem value="source">Giữ nguyên nguồn (Source)</MenuItem>
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
          <RadioGroup value={mode} onChange={(e) => setMode(e.target.value)}>
            <FormControlLabel value="full_dub" control={<Radio size="small" />} label="Lồng tiếng HQ đầy đủ" />
            <FormControlLabel value="captions_only" control={<Radio size="small" />} label="Chỉ phụ đề đã dịch" />
          </RadioGroup>
          {mode === "full_dub" && (
            <FormControlLabel control={<Checkbox checked={reviewTranslation} onChange={(e) => setReviewTranslation(e.target.checked)} />} label="Tạm dừng sau C6 để duyệt bản dịch" />
          )}

          <Divider sx={{ my: 0.5 }} />

          <Box>
            <FormControlLabel
              control={<Checkbox checked={enableMask} onChange={(e) => setEnableMask(e.target.checked)} />}
              label={<Typography sx={{ fontWeight: 600, fontSize: "0.9rem" }}>Che chữ / phụ đề gốc trong video</Typography>}
            />
            {enableMask && (
              <Stack spacing={1.5} sx={{ mt: 1, p: 1.5, bgcolor: "action.hover", borderRadius: 1.5 }}>
                <RadioGroup row value={maskMode} onChange={(e) => setMaskMode(e.target.value)}>
                  <FormControlLabel value="blur" control={<Radio size="small" />} label="Làm mờ (Blur)" />
                  <FormControlLabel value="solid" control={<Radio size="small" />} label="Hộp tối (Solid)" />
                </RadioGroup>
                <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                  <Button size="small" variant="outlined" onClick={() => applyMaskPreset("bottom")}>Dưới</Button>
                  <Button size="small" variant="outlined" onClick={() => applyMaskPreset("bottom-full")}>Dưới toàn dải</Button>
                  <Button size="small" variant="outlined" onClick={() => applyMaskPreset("top")}>Trên</Button>
                </Stack>
                <Box>
                  <Typography variant="caption" color="text.secondary">Vị trí ngang (X): {Math.round(maskBox.x * 100)}%</Typography>
                  <Slider
                    size="small"
                    min={0}
                    max={90}
                    value={Math.round(maskBox.x * 100)}
                    onChange={(_, val) => setMaskBox((prev) => {
                      const newX = val / 100
                      const newW = (newX + prev.width > 1.0) ? Math.max(0.04, 1.0 - newX) : prev.width
                      return { ...prev, x: newX, width: newW }
                    })}
                  />
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">Vị trí dọc (Y): {Math.round(maskBox.y * 100)}%</Typography>
                  <Slider
                    size="small"
                    min={0}
                    max={90}
                    value={Math.round(maskBox.y * 100)}
                    onChange={(_, val) => setMaskBox((prev) => {
                      const newY = val / 100
                      const newH = (newY + prev.height > 1.0) ? Math.max(0.04, 1.0 - newY) : prev.height
                      return { ...prev, y: newY, height: newH }
                    })}
                  />
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">Chiều cao (Height): {Math.round(maskBox.height * 100)}%</Typography>
                  <Slider
                    size="small"
                    min={4}
                    max={50}
                    value={Math.round(maskBox.height * 100)}
                    onChange={(_, val) => setMaskBox((prev) => {
                      const newH = val / 100
                      const newY = (prev.y + newH > 1.0) ? Math.max(0, 1.0 - newH) : prev.y
                      return { ...prev, height: newH, y: newY }
                    })}
                  />
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">Chiều rộng (Width): {Math.round(maskBox.width * 100)}%</Typography>
                  <Slider
                    size="small"
                    min={4}
                    max={100}
                    value={Math.round(maskBox.width * 100)}
                    onChange={(_, val) => setMaskBox((prev) => {
                      const newW = val / 100
                      const newX = (prev.x + newW > 1.0) ? Math.max(0, 1.0 - newW) : prev.x
                      return { ...prev, width: newW, x: newX }
                    })}
                  />
                </Box>
                <Divider />
                <Box>
                  <Typography variant="caption" sx={{ fontWeight: 700 }}>Vị trí Vietsub</Typography>
                  <RadioGroup value={captionPlacementMode} onChange={(e) => setCaptionPlacementMode(e.target.value)}>
                    <FormControlLabel value="mask_region" control={<Radio size="small" />} label="Theo vùng chữ gốc" />
                    <FormControlLabel value="default" control={<Radio size="small" />} label="Theo kiểu phụ đề mặc định" />
                  </RadioGroup>
                </Box>
                {captionPlacementMode === "mask_region" && (
                  <Box>
                    <Typography variant="caption" color="text.secondary">Dịch Vietsub lên / xuống: {Math.round(captionVerticalOffset * 100)}%</Typography>
                    <Slider size="small" min={-25} max={25} value={Math.round(captionVerticalOffset * 100)} onChange={(_, val) => setCaptionVerticalOffset(val / 100)} />
                  </Box>
                )}
              </Stack>
            )}
          </Box>

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
          Xem & chỉnh sửa bản dịch
        </Button>
      )}
    </Stack>
  )

  const voicePanel = (
    <Stack spacing={2}><Alert severity="info" variant="outlined">Mô hình viết lại thời lượng C7 <strong>{effective.rewrite_model || "mặc định kết nối"}</strong> tách biệt với mô hình dịch C6.</Alert><Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "repeat(3, 1fr)" }, gap: 1.5 }}><Paper variant="outlined" sx={{ p: 2 }}><Typography variant="subtitle2">Giọng đọc</Typography><Typography variant="body2">{effective.tts_provider || "mặc định nhà cung cấp"} · {effective.tts_voice || "giọng của hồ sơ"}</Typography></Paper><Paper variant="outlined" sx={{ p: 2 }}><Typography variant="subtitle2">Giới hạn nhịp</Typography><Typography variant="body2">Ưu tiên {effective.dub_speed_accept || 1.15}× · tối đa {effective.dub_speed_max || 1.3}×</Typography></Paper><Paper variant="outlined" sx={{ p: 2 }}><Typography variant="subtitle2">Viết lại</Typography><Typography variant="body2">{effective.dub_rewrite_attempts ?? 2} lần thử</Typography></Paper></Box>{chunks.length > 0 && <Stack spacing={1}>{chunks.map((chunk) => <Paper variant="outlined" key={chunk.id} sx={{ p: 1.25 }}><Typography variant="body2">Đoạn {chunk.id} · {chunk.speaker_id || "người nói —"} · tốc độ {Number(chunk.speed_factor || 1).toFixed(2)}×</Typography></Paper>)}</Stack>}{jobId && <Button variant="outlined" startIcon={<TuneOutlinedIcon />} onClick={() => setExpertOpen(true)} sx={{ alignSelf: "flex-start" }}>Nhịp / giọng nâng cao</Button>}</Stack>
  )

  const renderPanel = (
    <Stack spacing={2}>
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "repeat(2, 1fr)" }, gap: 1.5 }}>
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2">Âm thanh</Typography>
          <Typography variant="body2">Chế độ: {effective.audio_mode || "mặc định hồ sơ"}</Typography>
          <Typography variant="body2">Nền: {effective.preserve_background === false ? "thay thế" : "giữ lại"}</Typography>
          <Typography variant="body2">Giọng: {effective.tts_voice || "giọng của hồ sơ"}</Typography>
        </Paper>
        <Paper variant="outlined" sx={{ p: 2 }}>
          <Typography variant="subtitle2">Hình ảnh & phụ đề</Typography>
          <Typography variant="body2">Kiểu: {effective.caption_style || "viral"}</Typography>
          <Typography variant="body2">Tỷ lệ: {targetAspect || effective.target_aspect || "nguồn"}</Typography>
          <Typography variant="body2">Vùng che: {enableMask ? (maskMode === "solid" ? "Hộp tối" : "Làm mờ") : "Tắt"}</Typography>
          <Typography variant="body2">Vietsub: {captionPlacementMode === "mask_region" && enableMask ? "Theo vùng chữ gốc" : "Vị trí mặc định"}</Typography>
        </Paper>
      </Box>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        <StageChip name="Trộn âm thanh" record={stages.audio_mix} />
        <StageChip name="Kết xuất cuối" record={stages.final_render} />
      </Stack>
      {jobId && (
        <Stack spacing={0.75}>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            <Button variant="contained" onClick={rerender} disabled={activeLocalization || rerendering || !canRerender}>
              {rerendering ? "Đang kết xuất…" : "Kết xuất lại video"}
            </Button>
            <Button variant="outlined" startIcon={<TuneOutlinedIcon />} onClick={() => setExpertOpen(true)}>
              Điều khiển kết xuất nâng cao
            </Button>
          </Stack>
          {!activeLocalization && !canRerender && (
            <Typography variant="caption" color="text.secondary">
              Hoàn tất bản dịch/lồng tiếng trước khi kết xuất lại.
            </Typography>
          )}
        </Stack>
      )}
    </Stack>
  )

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
        {jobFailure && (
          <Alert severity="error" data-testid="localization-job-failure">
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Tác vụ bản địa hóa thất bại</Typography>
            {jobFailure.step ? <Typography variant="body2">Bước: {jobFailure.step}</Typography> : null}
            <Typography variant="body2" sx={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>{jobFailure.message}</Typography>
          </Alert>
        )}
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
