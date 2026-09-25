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
  separation: "Separation",
  asr: "ASR / alignment",
  semantic: "Semantic cues",
  translation: "Translation",
  dubbing: "Dubbing",
  audio_mix: "Audio mix",
  final_render: "Final render",
  qc: "QC",
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
          "source " + segment.source_start_ms + "–" + segment.source_end_ms + " ms"}
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
        Save + rerun downstream
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
      await http.post("/api/localization/jobs/" + jobId + "/rerun", { from_stage: stage })
      showSnackbar("Đang chạy lại từ " + (STAGE_LABELS[stage] || stage), "info")
      const status = await pollUntilTerminal()
      showSnackbar(
        status === "success" ? "Localization rerun complete" : "Rerun ended: " + status,
        status === "success" ? "success" : "warning",
      )
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Chạy lại thất bại", "error")
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
              Continue dubbing
            </Button>
          }
        >
          Translation review is required before TTS. Compare every source line with the Vietnamese target below; edit anything inaccurate, then continue the same job from C7.
        </Alert>
      )}

      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Typography variant="subtitle2" sx={{ flex: 1 }}>Chỉnh bản địa hóa nâng cao</Typography>
        {qc?.status && <Chip size="small" color={qcColor(qc.status)} label={"QC " + qc.status} />}
        <Button size="small" startIcon={<RefreshOutlinedIcon />} onClick={load} disabled={busy}>Làm mới</Button>
      </Stack>

      {qc && ((qc.failures?.length || 0) > 0 || (qc.warnings?.length || 0) > 0) && (
        <Alert severity={qc.status === "FAILED" ? "error" : "warning"}>
          {(qc.failures?.length || 0) + " failures · " + (qc.warnings?.length || 0) + " warnings"}
          {qc.metrics?.speech_coverage_ratio != null
            ? " · speech coverage " + Math.round(qc.metrics.speech_coverage_ratio * 100) + "%"
            : ""}
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

      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        {["translation", "dubbing", "final_render", "qc"].map((stage) => (
          <Button key={stage} size="small" variant="outlined" startIcon={<ReplayOutlinedIcon />} onClick={() => rerun(stage)} disabled={busy}>
            {"Rerun " + STAGE_LABELS[stage]}
          </Button>
        ))}
      </Stack>

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
