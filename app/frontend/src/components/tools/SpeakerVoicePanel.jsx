import { useEffect, useState } from "react"
import { Alert, Autocomplete, Button, Checkbox, FormControlLabel, Stack, TextField, Typography } from "@mui/material"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

export default function SpeakerVoicePanel({ jobId, detail, disabled = false, onSaved }) {
  const state = detail?.speaker_state || {}
  const speakers = state.detected_speakers || []
  const selection = JSON.stringify(state.speaker_voices || {})
  const [voices, setVoices] = useState({})
  const [shared, setShared] = useState(false)
  const [saving, setSaving] = useState(false)
  const showSnackbar = useAppStore((store) => store.showSnackbar)
  useEffect(() => {
    setVoices(JSON.parse(selection))
    setShared(Boolean(state.shared_voice))
  }, [jobId, selection, state.shared_voice])

  const save = async () => {
    setSaving(true)
    try {
      await http.post(`/api/localization/jobs/${jobId}/override`, {
        revision: detail.translation_review_revision,
        profile_updates: {
          speaker_voice_mode: shared ? "shared" : "distinct",
          speaker_voices: Object.fromEntries(Object.entries(voices).filter(([, voice]) => voice.trim())),
        },
      })
      if (!detail.translation_review_pending) {
        await http.post(`/api/localization/jobs/${jobId}/rerun`, { from_stage: "dubbing" })
      }
      showSnackbar(detail.translation_review_pending ? "Đã lưu giọng cho tác vụ này. Duyệt bản dịch để tiếp tục." : "Đã lưu giọng; đang chạy lại lồng tiếng.", "success")
      await onSaved?.()
    } catch (error) {
      showSnackbar(error.response?.data?.detail || "Không thể lưu giọng", "error")
    } finally {
      setSaving(false)
    }
  }

  return <Stack spacing={1.25} data-testid="speaker-voice-panel">
    <Typography variant="subtitle2">Người nói · {Math.max(speakers.length, state.diarization?.speaker_count || 0)} đã phát hiện</Typography>
    {state.diarization?.diarization_reason && <Alert severity="warning">Chưa xác minh được người nói: {state.diarization.diarization_reason}. Kiểm tra và sửa người nói trong phần nâng cao.</Alert>}
    {(state.diarization?.overlap_intervals || []).length > 0 && <Alert severity="warning">Có lời thoại chồng nhau; cần kiểm tra trước khi duyệt QC.</Alert>}
    {speakers.map((speaker) => <Autocomplete key={speaker} freeSolo options={state.available_voices || []}
      inputValue={voices[speaker] || ""} disabled={disabled || saving || shared}
      onInputChange={(_, voice) => setVoices((current) => ({ ...current, [speaker]: voice }))}
      renderInput={(params) => <TextField {...params} size="small" label={`Giọng của ${speaker}`} placeholder="Tự chọn giọng riêng hoặc nhập ID giọng" />} />)}
    {speakers.length > 1 && <FormControlLabel label="Dùng chung một giọng cho mọi người nói" control={<Checkbox checked={shared}
      disabled={disabled || saving} onChange={(event) => setShared(event.target.checked)} />} />}
    {speakers.length > 0 && <Button variant="outlined" disabled={disabled || saving || !detail?.translation_review_revision}
      onClick={save} sx={{ alignSelf: "flex-start" }}>Lưu giọng cho tác vụ</Button>}
  </Stack>
}
