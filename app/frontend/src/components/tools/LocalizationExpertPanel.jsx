import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Chip,
  CircularProgress, Divider, Stack, TextField, Typography,
} from "@mui/material"
import ExpandMoreOutlinedIcon from "@mui/icons-material/ExpandMoreOutlined"
import RefreshOutlinedIcon from "@mui/icons-material/RefreshOutlined"
import ReplayOutlinedIcon from "@mui/icons-material/ReplayOutlined"
import SaveOutlinedIcon from "@mui/icons-material/SaveOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

const STAGE_LABELS = {
  separation: "Tách âm thanh",
  asr: "ASR / Căn chỉnh",
  semantic: "Phân đoạn ngữ nghĩa",
  translation: "Dịch thuật",
  dubbing: "Lồng tiếng",
  audio_mix: "Trộn âm thanh",
  final_render: "Kết xuất video",
  qc: "Kiểm định (QC)",
}

const qcColor = (status) => (
  status === "READY" ? "success" : status === "FAILED" ? "error" : "warning"
)

function SegmentEditor({ segment, onSave, busy }) {
  const [text, setText] = useState(segment.translated_text || "")
  const [start, setStart] = useState(segment.overrides?.target_start_ms ?? "")
  const [end, setEnd] = useState(segment.overrides?.target_end_ms ?? "")
  const [voice, setVoice] = useState(segment.overrides?.voice_id || "")

  useEffect(() => {
    setText(segment.translated_text || "")
    setStart(segment.overrides?.target_start_ms ?? "")
    setEnd(segment.overrides?.target_end_ms ?? "")
    setVoice(segment.overrides?.voice_id || "")
  }, [segment])

  const save = () => {
    const payload = {
      id: segment.id,
      translated_text: text,
      voice_id: voice,
    }
    if (start !== "" || end !== "") {
      payload.target_start_ms = Number(start)
      payload.target_end_ms = Number(end)
    } else {
      payload.clear_timing = true
    }
    onSave(payload)
  }

  return (
    <Stack spacing={1.25}>
      <Typography variant="caption" color="text.secondary">
        {(segment.speaker_id ? segment.speaker_id + " · " : "") +
          "nguồn " + segment.source_start_ms + "–" + segment.source_end_ms + " ms"}
      </Typography>
      <Typography variant="body2">{segment.source_text}</Typography>
      <TextField
        label="Lời thoại đích"
        size="small"
        multiline
        minRows={2}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        <TextField label="Bắt đầu (ms)" size="small" type="number" value={start} onChange={(e) => setStart(e.target.value)} />
        <TextField label="Kết thúc (ms)" size="small" type="number" value={end} onChange={(e) => setEnd(e.target.value)} />
      </Stack>
      <TextField
        label="Ghi đè giọng đọc"
        size="small"
        value={voice}
        onChange={(e) => setVoice(e.target.value)}
        placeholder="ID giọng của nhà cung cấp (tùy chọn)"
      />
      <Button
        size="small"
        variant="outlined"
        startIcon={<SaveOutlinedIcon />}
        onClick={save}
        disabled={busy || !text.trim()}
      >
        Lưu & chạy lại các bước sau
      </Button>
    </Stack>
  )
}

export default function LocalizationExpertPanel({ jobId }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    if (!jobId) return
    setLoading(true)
    try {
      const { data } = await http.get("/api/localization/jobs/" + jobId)
      setDetail(data)
    } catch (err) {
      setDetail({ error: err.response?.data?.detail || err.message })
    } finally {
      setLoading(false)
    }
  }, [jobId])

  useEffect(() => { load() }, [load])

  const pollUntilTerminal = useCallback(async () => {
    for (let i = 0; i < 180; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000))
      try {
        const { data } = await http.get("/api/jobs/" + jobId)
        if (["success", "failed", "cancelled"].includes(data.status)) {
          await load()
          return data.status
        }
      } catch {
        // Backend may be restarting; keep polling.
      }
    }
    await load()
    return "timeout"
  }, [jobId, load])

  const rerun = async (stage) => {
    setBusy(true)
    try {
      const { data } = await http.post("/api/localization/jobs/" + jobId + "/rerun", { from_stage: stage })
      showSnackbar(
        data?.tts_rebound
          ? "Đã dùng TTS hiện tại; tác vụ đang tiếp tục từ " + (STAGE_LABELS[stage] || stage)
          : "Đang chạy lại từ " + (STAGE_LABELS[stage] || stage),
        "info",
      )
      // Dispatch is enough for this control. Do not lock the editor for the
      // entire long-running job; the backend rejects duplicate reruns while
      // active and Activity owns live progress.
      await load()
    } catch (err) {
      const detailMessage = err.response?.data?.detail
      showSnackbar(
        typeof detailMessage === "string" ? detailMessage : detailMessage?.message || "Chạy lại thất bại",
        "error",
      )
    } finally {
      setBusy(false)
    }
  }

  const rebindTts = async () => {
    setBusy(true)
    try {
      await http.post("/api/localization/jobs/" + jobId + "/rebind-tts", { source: "settings" })
      showSnackbar("Đã dùng TTS hiện tại; tác vụ đang tiếp tục từ bước Lồng tiếng", "info")
      // Rebinding only dispatches the resumed job. Release the UI immediately
      // instead of keeping every recovery action disabled until render/QC ends.
      await load()
    } catch (err) {
      const detailMessage = err.response?.data?.detail
      showSnackbar(typeof detailMessage === "string" ? detailMessage : detailMessage?.message || "Không thể gắn lại TTS", "error")
    } finally {
      setBusy(false)
    }
  }

  const saveSegment = async (change) => {
    setBusy(true)
    try {
      await http.post("/api/localization/jobs/" + jobId + "/override", { segments: [change] })
      if (detail?.translation_review_pending) {
        showSnackbar("Đã lưu bản dịch. Hãy duyệt các dòng còn lại rồi tiếp tục lồng tiếng.", "success")
        await load()
      } else {
        await http.post("/api/localization/jobs/" + jobId + "/rerun", { from_stage: "dubbing" })
        showSnackbar("Đã lưu ghi đè; đang chạy lại phần lồng tiếng phía sau", "info")
        await pollUntilTerminal()
      }
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Ghi đè thất bại", "error")
    } finally {
      setBusy(false)
    }
  }

  const continueAfterTranslation = async () => {
    setBusy(true)
    try {
      await http.post("/api/localization/jobs/" + jobId + "/continue-after-translation")
      showSnackbar("Đã duyệt bản dịch; tiếp tục lồng tiếng HQ từ C7", "info")
      await pollUntilTerminal()
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Không thể tiếp tục lồng tiếng", "error")
    } finally {
      setBusy(false)
    }
  }

  const regenerateChunk = async (chunkId) => {
    setBusy(true)
    try {
      await http.post("/api/localization/jobs/" + jobId + "/chunks/" + chunkId + "/regenerate")
      showSnackbar("Đang tạo lại đoạn " + chunkId, "info")
      await pollUntilTerminal()
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Tạo lại đoạn thất bại", "error")
    } finally {
      setBusy(false)
    }
  }

  const manifest = detail?.manifest || {}
  const stages = manifest.stages || {}
  const transcript = detail?.transcript?.segments || []
  const chunks = detail?.chunks || []
  const qc = detail?.qc
  const ttsBinding = detail?.tts_binding
  const dubbingMeta = stages?.dubbing?.metadata || {}
  const rewriteIssues = (dubbingMeta.issues || []).filter((item) =>
    String(item?.reason || "").startsWith("rewrite_")
  )
  const timingOverflows = (dubbingMeta.issues || []).filter((item) =>
    item?.reason === "timing_overflow"
  )
  const reviewPending = !!detail?.translation_review_pending
  const finalReady = stages?.final_render?.status === "complete"
  const stageEntries = useMemo(() => Object.entries(stages), [stages])

  if (loading) return <Stack alignItems="center" sx={{ py: 4 }}><CircularProgress size={28} /></Stack>
  if (detail?.error) return <Alert severity="warning">{detail.error}</Alert>

  return (
    <Stack spacing={2}>
      {finalReady && (
        <Box component="video" src={"/api/tools/download/" + jobId} controls playsInline sx={{ width: "100%", borderRadius: 1.5, bgcolor: "#000" }} />
      )}

      {reviewPending && (
        <Alert
          severity="warning"
          action={
            <Button color="inherit" size="small" onClick={continueAfterTranslation} disabled={busy}>
              Tiếp tục lồng tiếng
            </Button>
          }
        >
          Bản dịch cần được kiểm tra trước khi lồng tiếng TTS. Đối chiếu từng dòng gốc với bản dịch tiếng Việt bên dưới; chỉnh sửa các câu chưa chuẩn rồi bấm tiếp tục tác vụ từ bước C7.
        </Alert>
      )}

      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Typography variant="subtitle2" sx={{ flex: 1 }}>Chỉnh bản địa hóa nâng cao</Typography>
        {qc?.status && <Chip size="small" color={qcColor(qc.status)} label={"QC " + qc.status} />}
        <Button size="small" startIcon={<RefreshOutlinedIcon />} onClick={load} disabled={busy}>Làm mới</Button>
      </Stack>

      {qc && ((qc.failures?.length || 0) > 0 || (qc.warnings?.length || 0) > 0) && (
        <Alert severity={qc.status === "FAILED" ? "error" : "warning"}>
          {(qc.failures?.length || 0) + " lỗi · " + (qc.warnings?.length || 0) + " cảnh báo"}
          {qc.metrics?.speech_coverage_ratio != null
            ? " · tỷ lệ giọng đọc " + Math.round(qc.metrics.speech_coverage_ratio * 100) + "%"
            : ""}
        </Alert>
      )}

      {ttsBinding && (
        <Alert severity={ttsBinding.binding_mismatch ? "warning" : "info"} variant="outlined">
          <Stack spacing={1}>
            <Typography variant="subtitle2">Liên kết TTS của tác vụ</Typography>
            <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" }, gap: 1 }}>
              <Box>
                <Typography variant="caption" color="text.secondary">TTS của job</Typography>
                <Typography variant="body2">{ttsBinding.job?.tts_provider || "kế thừa"} · {ttsBinding.job?.tts_voice || "chưa chọn giọng"}</Typography>
              </Box>
              <Box>
                <Typography variant="caption" color="text.secondary">TTS hiện tại</Typography>
                <Typography variant="body2">{ttsBinding.current?.tts_provider || "không khả dụng"} · {ttsBinding.current?.tts_voice || "chưa chọn giọng"}</Typography>
              </Box>
            </Box>
            {ttsBinding.binding_mismatch && <Typography variant="body2">Cấu hình TTS đã thay đổi. Có thể tiếp tục cùng job bằng TTS hiện tại mà không chạy lại tách âm thanh, ASR hoặc dịch thuật.</Typography>}
            {ttsBinding.current_error && <Typography variant="caption" color="error">{ttsBinding.current_error}</Typography>}
            <Button size="small" variant="contained" color="warning" onClick={rebindTts} disabled={busy || !ttsBinding.current} sx={{ alignSelf: "flex-start" }}>
              Dùng TTS hiện tại & tiếp tục
            </Button>
          </Stack>
        </Alert>
      )}

      {(stages?.dubbing?.status === "complete" || stages?.dubbing?.status === "failed") && (
        <Alert
          severity={rewriteIssues.some((item) => item.reason === "rewrite_ai_unavailable") ? "warning" : "info"}
          variant="outlined"
        >
          <Stack spacing={0.75}>
            <Typography variant="subtitle2">C7 · Viết lại thời lượng</Typography>
            <Typography variant="body2">
              {"Đã viết lại: " + Number(dubbingMeta.rewrite_count || 0) + " đoạn" +
                (dubbingMeta.rewrite_provider ? " · provider " + dubbingMeta.rewrite_provider : "") +
                (dubbingMeta.rewrite_model ? " · model " + dubbingMeta.rewrite_model : "")}
            </Typography>
            {dubbingMeta.max_raw_required_speed != null && (
              <Typography variant="caption" color="text.secondary">
                {"Tốc độ yêu cầu lớn nhất: " + Number(dubbingMeta.max_raw_required_speed || 1).toFixed(2) + "×" +
                  (dubbingMeta.max_fitted_speed_factor != null
                    ? " → sau fit " + Number(dubbingMeta.max_fitted_speed_factor || 1).toFixed(2) + "×"
                    : "")}
              </Typography>
            )}
            {rewriteIssues.slice(0, 4).map((item, index) => (
              <Typography key={item.chunk_id + "-" + index} variant="caption" color={item.reason === "rewrite_ai_unavailable" ? "error" : "text.secondary"}>
                {"Chunk " + (item.chunk_id ?? "?") + " · " + item.reason +
                  (item.message ? " · " + item.message : item.detail ? " · " + item.detail : "")}
              </Typography>
            ))}
            {timingOverflows.slice(0, 4).map((item, index) => (
              <Typography key={"overflow-" + item.chunk_id + "-" + index} variant="caption" color="error">
                {"Chunk " + (item.chunk_id ?? "?") + " vẫn overflow · " +
                  Number(item.required_speed || 1).toFixed(2) + "× / trần " +
                  Number(item.speed_max || 1).toFixed(2) + "×"}
              </Typography>
            ))}
          </Stack>
        </Alert>
      )}

      <Box>
        <Typography variant="caption" color="text.secondary">Các bước</Typography>
        <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap sx={{ mt: 0.75 }}>
          {stageEntries.map(([name, record]) => (
            <Chip
              key={name}
              size="small"
              variant="outlined"
              color={record.status === "complete" ? "success" : record.status === "failed" ? "error" : "default"}
              label={(STAGE_LABELS[name] || name) + ": " + record.status}
            />
          ))}
        </Stack>
      </Box>

      {!ttsBinding?.binding_mismatch && (
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {["translation", "dubbing", "final_render", "qc"].map((stage) => (
            <Button key={stage} size="small" variant="outlined" startIcon={<ReplayOutlinedIcon />} onClick={() => rerun(stage)} disabled={busy}>
              {"Chạy lại từ " + STAGE_LABELS[stage]}
            </Button>
          ))}
        </Stack>
      )}

      <Divider />

      <Typography variant="subtitle2">Các đoạn lồng tiếng</Typography>
      {reviewPending ? (
        <Typography variant="caption" color="text.secondary">Chưa bắt đầu lồng tiếng — hãy duyệt bản dịch C6 trước.</Typography>
      ) : chunks.length === 0 ? (
        <Typography variant="caption" color="text.secondary">Không có đoạn lồng tiếng cho lần chạy này.</Typography>
      ) : chunks.map((chunk) => (
        <Stack key={chunk.id} direction="row" spacing={1} alignItems="center">
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>{"Đoạn " + chunk.id}</Typography>
            <Typography variant="caption" color="text.secondary">
              {chunk.start_ms + "–" + chunk.end_ms + " ms · speed " + Number(chunk.speed_factor || 1).toFixed(2) + "×" +
                (chunk.speaker_id ? " · " + chunk.speaker_id : "")}
            </Typography>
          </Box>
          <Button size="small" onClick={() => regenerateChunk(chunk.id)} disabled={busy}>Tạo lại</Button>
        </Stack>
      ))}

      <Divider />

      <Typography variant="subtitle2">Các đoạn</Typography>
      {transcript.map((segment) => (
        <Accordion key={segment.id} disableGutters elevation={0} sx={{ border: 1, borderColor: "divider", borderRadius: "8px !important" }}>
          <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="body2" noWrap>{segment.translated_text || segment.source_text}</Typography>
              <Typography variant="caption" color="text.secondary">{segment.id}</Typography>
            </Box>
          </AccordionSummary>
          <AccordionDetails>
            <SegmentEditor segment={segment} onSave={saveSegment} busy={busy} />
          </AccordionDetails>
        </Accordion>
      ))}
    </Stack>
  )
}
