import { useEffect, useRef, useState } from "react"
import { Alert, Autocomplete, Button, Checkbox, FormControlLabel, MenuItem, Stack, TextField, Typography } from "@mui/material"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

export default function SpeakerVoicePanel({ jobId, detail, disabled = false, onSaved }) {
  const state = detail?.speaker_state || {}
  const speakers = state.detected_speakers || []
  const selection = JSON.stringify(state.speaker_voices || {})
  const confirmed = JSON.stringify(Object.fromEntries(Object.entries(state.speaker_genders || {})
    .filter(([, fact]) => fact.source === "reviewer").map(([speaker, fact]) => [speaker, fact.gender])))
  const outputSelection = JSON.stringify(state.voice_genders || {})
  const [voices, setVoices] = useState({})
  const [genders, setGenders] = useState({})
  const [outputGenders, setOutputGenders] = useState({})
  const [shared, setShared] = useState(false)
  const [saving, setSaving] = useState(false)
  const showSnackbar = useAppStore((store) => store.showSnackbar)
  const activeJob = useRef(jobId)
  activeJob.current = jobId
  useEffect(() => {
    activeJob.current = jobId
    setSaving(false)
    return () => { activeJob.current = null }
  }, [jobId])
  useEffect(() => {
    setVoices(JSON.parse(selection))
    setGenders(JSON.parse(confirmed))
    setOutputGenders(JSON.parse(outputSelection))
    setShared(Boolean(state.shared_voice))
  }, [jobId, selection, confirmed, outputSelection, state.shared_voice])

  const save = async () => {
    setSaving(true)
    let saved = false
    try {
      await http.post(`/api/localization/jobs/${jobId}/override`, {
        revision: detail.translation_review_revision,
        profile_updates: {
          speaker_voice_mode: shared ? "shared" : "distinct",
          speaker_voices: Object.fromEntries(Object.entries(voices).filter(([, voice]) => voice.trim())),
          speaker_genders: genders,
          voice_genders: Object.fromEntries(Object.entries(outputGenders).filter(([voice]) => Object.values(voices).includes(voice))),
        },
      })
      saved = true
      if (activeJob.current !== jobId) return
      if (!detail.translation_review_pending) {
        await http.post(`/api/localization/jobs/${jobId}/rerun`, { from_stage: "dubbing" })
      }
      if (activeJob.current !== jobId) return
      showSnackbar(detail.translation_review_pending ? "Đã lưu giọng cho tác vụ này. Duyệt bản dịch để tiếp tục." : "Đã lưu giọng; đang chạy lại lồng tiếng.", "success")
      await onSaved?.()
    } catch (error) {
      if (activeJob.current !== jobId) return
      showSnackbar(saved ? `Đã lưu giọng nhưng chưa chạy lại lồng tiếng: ${error.response?.data?.detail || "hãy thử chạy lại"}`
        : (error.response?.data?.detail || "Không thể lưu giọng"), "error")
      if (saved) await onSaved?.()
    } finally {
      if (activeJob.current === jobId) setSaving(false)
    }
  }

  return <Stack spacing={1.25} data-testid="speaker-voice-panel">
    <Typography variant="subtitle2">Người nói · {Math.max(speakers.length, state.diarization?.speaker_count || 0)} đã phát hiện</Typography>
    {state.diarization?.diarization_reason && <Alert severity="warning">Chưa xác minh được người nói: {state.diarization.diarization_reason}. Kiểm tra và sửa người nói trong phần nâng cao.</Alert>}
    {(state.diarization?.overlap_intervals || []).length > 0 && <Alert severity="warning">Có lời thoại chồng nhau; cần kiểm tra trước khi duyệt QC.</Alert>}
    <Typography variant="caption">Chọn giọng nam/nữ sau khi nghe bản gốc. Gợi ý từ âm vực cần được xác nhận; không tự đổi giọng để đủ số người nói.</Typography>
    {speakers.map((speaker) => <Stack key={speaker} spacing={1}>
      <TextField select size="small" label={`Giọng gốc của ${speaker}`} value={genders[speaker] || "unknown"}
        disabled={disabled || saving} onChange={(event) => setGenders((current) => ({ ...current, [speaker]: event.target.value }))}
        helperText={state.speaker_genders?.[speaker]?.source === "acoustic_pitch"
          ? `Gợi ý âm vực: ${state.speaker_genders[speaker].gender === "male" ? "Nam" : state.speaker_genders[speaker].gender === "female" ? "Nữ" : "Chưa rõ"}; chưa xác minh bằng nghe.` : undefined}>
        <MenuItem value="unknown">Chưa xác nhận</MenuItem><MenuItem value="male">Nam</MenuItem><MenuItem value="female">Nữ</MenuItem>
      </TextField>
      <Autocomplete freeSolo options={state.available_voices || []}
      inputValue={voices[speaker] || ""} disabled={disabled || saving || shared}
      onInputChange={(_, voice) => setVoices((current) => ({ ...current, [speaker]: voice }))}
      renderInput={(params) => <TextField {...params} size="small" label={`Giọng lồng tiếng của ${speaker}`} placeholder="Tự chọn giọng riêng hoặc nhập ID giọng"
        helperText={`Giọng TTS: ${(state.voice_catalogue || []).find((row) => row.voice_id === voices[speaker])?.gender === "female" ? "Nữ"
          : (state.voice_catalogue || []).find((row) => row.voice_id === voices[speaker])?.gender === "male" ? "Nam" : "Chưa có thông tin"}`} />} />
      {voices[speaker] && !(state.voice_catalogue || []).some((row) => row.voice_id === voices[speaker] && row.gender !== "unknown")
        && <TextField select size="small" label={`Xác nhận giọng TTS ${speaker}`} value={outputGenders[voices[speaker]] || "unknown"}
          disabled={disabled || saving || shared} onChange={(event) => setOutputGenders((current) => ({ ...current, [voices[speaker]]: event.target.value }))}>
          <MenuItem value="unknown">Chưa xác nhận</MenuItem><MenuItem value="male">Nam</MenuItem><MenuItem value="female">Nữ</MenuItem>
        </TextField>}
    </Stack>)}
    {speakers.length > 1 && <FormControlLabel label="Dùng chung một giọng cho mọi người nói" control={<Checkbox checked={shared}
      disabled={disabled || saving} onChange={(event) => setShared(event.target.checked)} />} />}
    {speakers.length > 0 && <Button variant="outlined" disabled={disabled || saving || !detail?.translation_review_revision}
      onClick={save} sx={{ alignSelf: "flex-start" }}>Lưu giọng cho tác vụ</Button>}
  </Stack>
}
