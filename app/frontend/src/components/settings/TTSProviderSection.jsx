import { useEffect, useMemo, useRef, useState } from "react"
import {
  Alert, Button, Chip, CircularProgress, FormControl, InputLabel, MenuItem,
  Select, Stack, TextField, Typography,
} from "@mui/material"
import SaveOutlinedIcon from "@mui/icons-material/SaveOutlined"
import http, { apiErrorMessage as errorMessage } from "../../api/http"
import useAppStore from "../../store/appStore"

const ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"
const PROVIDERS = {
  edge_tts: { label: "Edge TTS" },
  openai_tts: { label: "OpenAI TTS", needsKey: true, defaultModel: "tts-1" },
  openai_compatible_tts: { label: "TTS tùy chỉnh tương thích OpenAI", base: true, defaultModel: "tts-1" },
  azure_tts: { label: "Azure Speech", needsKey: true, region: true },
  elevenlabs: { label: "ElevenLabs", needsKey: true, base: true, defaultBase: ELEVENLABS_BASE, defaultModel: "eleven_multilingual_v2" },
  gpt_sovits: { label: "GPT-SoVITS (clone giọng cục bộ)", base: true, defaultBase: "http://127.0.0.1:9880" },
}

// A request made with responseType:"blob" also delivers ERRORS as a Blob, so an
// axios error body is not plain JSON and every provider status/message is lost.
// errorMessage (api/http) decodes it before surfacing anything here.

export default function TTSProviderSection({ settings, updateSettings }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const initialProvider = settings?.tts_provider || "edge_tts"
  const [provider, setProvider] = useState(initialProvider)
  const [voice, setVoice] = useState(settings?.preferred_tts_voice || "")
  const [model, setModel] = useState(settings?.tts_model || PROVIDERS[initialProvider]?.defaultModel || "")
  const [baseUrl, setBaseUrl] = useState(settings?.tts_base_url || PROVIDERS[initialProvider]?.defaultBase || "")
  const [region, setRegion] = useState(settings?.tts_region || "")
  const [key, setKey] = useState("")
  const [saving, setSaving] = useState(false)
  const [probing, setProbing] = useState(false)
  const [loadingVoices, setLoadingVoices] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [voices, setVoices] = useState([])
  const [models, setModels] = useState([])
  const [health, setHealth] = useState(null)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const audioRef = useRef(null)
  const blobUrlRef = useRef(null)

  const stopPreview = () => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.removeAttribute("src")
      try { audioRef.current.load() } catch { /* detached audio */ }
    }
    if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current)
    blobUrlRef.current = null
  }

  useEffect(() => () => stopPreview(), [])

  useEffect(() => {
    const nextProvider = settings?.tts_provider || "edge_tts"
    setProvider(nextProvider)
    setVoice(settings?.preferred_tts_voice || "")
    setModel(settings?.tts_model || PROVIDERS[nextProvider]?.defaultModel || "")
    setBaseUrl(settings?.tts_base_url || PROVIDERS[nextProvider]?.defaultBase || "")
    setRegion(settings?.tts_region || "")
  }, [
    settings?.tts_provider,
    settings?.preferred_tts_voice,
    settings?.tts_model,
    settings?.tts_base_url,
    settings?.tts_region,
  ])

  const meta = PROVIDERS[provider] || PROVIDERS.edge_tts
  const savedProvider = settings?.tts_provider_profiles?.[provider] || null
  const keyIsSet = provider === settings?.tts_provider
    ? !!settings?.tts_api_key_set
    : !!savedProvider?.api_key_set
  const selectedVoice = useMemo(
    () => voices.find((item) => item.voice_id === voice),
    [voices, voice],
  )
  const savedVoiceMissing = provider === "elevenlabs" && voices.length > 0 && voice && !selectedVoice

  const probePayload = () => ({
    provider,
    voice_id: voice.trim() || null,
    model: model.trim() || null,
    base_url: baseUrl.trim() || null,
    region: region.trim() || null,
    ...(key.trim() ? { api_key: key.trim() } : {}),
  })

  const changeProvider = (next) => {
    const saved = settings?.tts_provider_profiles?.[next] || null
    stopPreview()
    setProvider(next)
    setVoice(saved?.voice || "")
    setVoices([])
    setModels([])
    setHealth(null)
    setModel(saved?.model || PROVIDERS[next]?.defaultModel || "")
    setBaseUrl(saved?.base_url || PROVIDERS[next]?.defaultBase || "")
    setRegion(saved?.region || "")
    setKey("")
    setShowAdvanced(false)
  }

  const loadVoices = async () => {
    setLoadingVoices(true)
    try {
      const { data } = await http.post("/api/settings/tts/voices", probePayload())
      const found = data?.voices || []
      setVoices(found)
      if (!voice && found.length === 1) setVoice(found[0].voice_id)
      showSnackbar(found.length ? `Tìm thấy ${found.length} giọng` : "Không tìm thấy giọng nào", found.length ? "success" : "warning")
    } catch (err) {
      showSnackbar(await errorMessage(err, "Không thể tải danh sách giọng"), "error")
    } finally {
      setLoadingVoices(false)
    }
  }

  const healthLabel = (result) => {
    if (!result?.ok) return "CHƯA SẴN SÀNG"
    if (result.connection_verified === false) return "CẤU HÌNH HỢP LỆ"
    return "READY"
  }

  const healthSeverity = (result) => {
    if (!result?.ok) return "error"
    if (result.connection_verified === false) return "warning"
    return "success"
  }

  const testConnection = async () => {
    setProbing(true)
    setHealth(null)
    try {
      const { data } = await http.post("/api/settings/tts/test", probePayload())
      setHealth(data)
      const message = data?.ok
        ? (data?.connection_verified === false
          ? (data?.message || "Cấu hình TTS hợp lệ (chưa xác minh kết nối)")
          : "Kết nối TTS sẵn sàng")
        : (data?.message || "Cấu hình TTS chưa hợp lệ")
      showSnackbar(message, data?.ok ? (data?.connection_verified === false ? "warning" : "success") : "warning")
      if (Array.isArray(data?.voices)) setVoices(data.voices)
      if (Array.isArray(data?.models)) setModels(data.models)
    } catch (err) {
      const message = await errorMessage(err, "Kiểm tra kết nối TTS thất bại")
      setHealth({ ok: false, connection_verified: false, message })
      showSnackbar(message, "error")
    } finally {
      setProbing(false)
    }
  }

  const previewVoice = async () => {
    if (!voice.trim()) {
      showSnackbar("Hãy chọn hoặc nhập ID giọng trước", "warning")
      return
    }
    stopPreview()
    setPreviewing(true)
    try {
      const response = await http.post("/api/tts/preview", probePayload(), { responseType: "blob" })
      const url = URL.createObjectURL(response.data)
      blobUrlRef.current = url
      if (!audioRef.current) audioRef.current = new Audio()
      audioRef.current.src = url
      audioRef.current.onended = stopPreview
      audioRef.current.play().then(
        () => showSnackbar("Đang phát bản thử giọng", "success"),
        () => showSnackbar("Trình duyệt đã chặn tự phát; hãy bấm lại Nghe thử giọng", "warning"),
      )
    } catch (err) {
      showSnackbar(await errorMessage(err, "Nghe thử giọng thất bại"), "error")
    } finally {
      setPreviewing(false)
    }
  }

  const save = async () => {
    if (meta.needsKey && !key.trim() && !keyIsSet) {
      showSnackbar("Thêm API key cho nhà cung cấp TTS này", "warning")
      return
    }
    if (provider === "elevenlabs" && !voice.trim()) {
      showSnackbar("Hãy tải và chọn một giọng ElevenLabs", "warning")
      return
    }
    if (meta.base && !baseUrl.trim()) {
      showSnackbar("Nhà cung cấp này cần Base URL", "warning")
      return
    }
    if (meta.region && !region.trim() && !baseUrl.trim()) {
      showSnackbar("Cần Azure Region hoặc endpoint tùy chỉnh", "warning")
      return
    }

    setSaving(true)
    try {
      const updates = {
        tts_provider: provider,
        preferred_tts_voice: voice.trim(),
        tts_model: model.trim(),
        tts_base_url: baseUrl.trim(),
        tts_region: region.trim(),
      }
      if (key.trim()) updates.tts_api_key = key.trim()
      await updateSettings(updates)
      setKey("")
      showSnackbar("Đã lưu nhà cung cấp TTS", "success")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Stack spacing={2}>
      <Alert severity="info" variant="outlined">
        <Typography variant="body2" sx={{ fontWeight: 700, mb: 0.5 }}>Đây chỉ là kết nối TTS dùng chung</Typography>
        <Typography variant="caption" color="text.secondary" component="div">
          Provider / API key / Base URL / model / giọng mặc định nằm ở đây. Kiểm tra kết nối không tạo speech; Nghe thử giọng là hành động riêng có thể dùng quota.
          <br />1. AI dịch C6 · 2. AI viết lại C7 · 3. TTS tạo giọng · 4. Audio / render / QC.
        </Typography>
      </Alert>

      <FormControl size="small" fullWidth>
        <InputLabel>Nhà cung cấp TTS</InputLabel>
        <Select value={provider} label="Nhà cung cấp TTS" onChange={(e) => changeProvider(e.target.value)}>
          {Object.entries(PROVIDERS).map(([id, item]) => (
            <MenuItem key={id} value={id}>
              {item.label}{settings?.tts_provider_profiles?.[id] ? " · đã lưu" : ""}
            </MenuItem>
          ))}
        </Select>
      </FormControl>

      {provider === "elevenlabs" && voices.length > 0 ? (
        <FormControl size="small" fullWidth error={savedVoiceMissing}>
          <InputLabel>Giọng ElevenLabs</InputLabel>
          <Select value={selectedVoice ? voice : ""} label="Giọng ElevenLabs" onChange={(e) => setVoice(e.target.value)}>
            {voices.map((item) => (
              <MenuItem key={item.voice_id} value={item.voice_id}>
                {item.name} · {item.voice_id}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      ) : provider === "elevenlabs" ? (
        <TextField
          label="ID giọng ElevenLabs"
          size="small"
          value={voice}
          onChange={(e) => setVoice(e.target.value)}
          helperText={voice ? "Giọng đã lưu cho provider này. Bấm Tải danh sách giọng để chọn lại." : "Bấm Tải danh sách giọng để chọn từ tài khoản."}
        />
      ) : (
        <TextField label="ID giọng mặc định" size="small" value={voice} onChange={(e) => setVoice(e.target.value)} />
      )}

      {savedVoiceMissing && <Alert severity="error">Giọng đã lưu không còn tồn tại hoặc tài khoản không truy cập được: {voice}</Alert>}

      {provider !== "edge_tts" && models.length > 0 ? (
        <FormControl size="small" fullWidth>
          <InputLabel>Mô hình</InputLabel>
          <Select
            value={models.some((item) => (item.model_id || item.id || item) === model) ? model : "__custom__"}
            label="Mô hình"
            onChange={(e) => setModel(e.target.value === "__custom__" ? "" : e.target.value)}
          >
            {models.map((item) => {
              const id = item.model_id || item.id || item
              const name = item.name || id
              return <MenuItem key={id} value={id}>{name} · {id}</MenuItem>
            })}
            <MenuItem value="__custom__"><em>Nhập model khác…</em></MenuItem>
          </Select>
        </FormControl>
      ) : provider !== "edge_tts" ? (
        <TextField label="Mô hình" size="small" value={model} onChange={(e) => setModel(e.target.value)} placeholder={meta.defaultModel || "provider model id"} />
      ) : null}

      {meta.region && <TextField label="Vùng Azure" size="small" value={region} onChange={(e) => setRegion(e.target.value)} placeholder="southeastasia" />}

      {provider !== "edge_tts" && provider !== "gpt_sovits" && (
        <TextField label="API key" type="password" size="small" value={key} onChange={(e) => setKey(e.target.value)} placeholder={keyIsSet ? "Đã lưu cho provider này — để trống để giữ" : "BYOK key"} autoComplete="off" />
      )}

      {provider === "elevenlabs" && (
        <Button variant="outlined" onClick={loadVoices} disabled={loadingVoices} sx={{ alignSelf: "flex-start" }}>
          {loadingVoices ? <CircularProgress size={18} /> : "Tải danh sách giọng"}
        </Button>
      )}

      {meta.base && (
        <Stack spacing={1}>
          <Button size="small" variant="text" onClick={() => setShowAdvanced((value) => !value)} sx={{ alignSelf: "flex-start" }}>
            {showAdvanced ? "Ẩn cấu hình nâng cao" : "Nâng cao / endpoint tùy chỉnh"}
          </Button>
          {showAdvanced && (
            <TextField label="Base URL" size="small" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={meta.defaultBase || "https://..."} helperText={provider === "elevenlabs" ? `Mặc định chính thức: ${ELEVENLABS_BASE}` : "Chỉ thay đổi khi dùng proxy/endpoint tùy chỉnh."} />
          )}
        </Stack>
      )}

      {health && (
        <Alert severity={healthSeverity(health)}>
          <Stack spacing={0.5}>
            <Typography variant="body2" sx={{ fontWeight: 700 }}>{healthLabel(health)}</Typography>
            <Typography variant="body2">{health.message}</Typography>
            {health.ok && health.connection_verified === false && (
              <Typography variant="caption" color="text.secondary" component="div">
                Chưa có yêu cầu nào được gửi tới nhà cung cấp. Bấm “Nghe thử giọng” để xác minh kết nối thật.
              </Typography>
            )}
            <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
              {health.voice_count != null && <Chip size="small" label={`${health.voice_count} giọng`} />}
              {health.selected_voice_valid != null && <Chip size="small" label={`Giọng: ${health.selected_voice_valid ? "hợp lệ" : "không hợp lệ"}`} />}
              {health.model_valid != null && <Chip size="small" label={`Mô hình: ${health.model_valid ? "hợp lệ" : "không hợp lệ"}`} />}
            </Stack>
          </Stack>
        </Alert>
      )}

      <Stack direction="row" spacing={1} justifyContent="flex-end" flexWrap="wrap" useFlexGap>
        <Button variant="outlined" onClick={testConnection} disabled={probing || saving}>Kiểm tra kết nối</Button>
        <Button variant="outlined" onClick={previewVoice} disabled={previewing || probing || !voice.trim()}>Nghe thử giọng</Button>
        <Button variant="contained" startIcon={<SaveOutlinedIcon />} onClick={save} disabled={saving}>
          {saving ? "Đang lưu…" : "Lưu TTS"}
        </Button>
      </Stack>
    </Stack>
  )
}
