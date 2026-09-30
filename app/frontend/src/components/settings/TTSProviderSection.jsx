import { useEffect, useMemo, useRef, useState } from "react"
import {
  Alert, Box, Button, Chip, CircularProgress, FormControl, InputLabel, MenuItem,
  Select, Stack, TextField, Typography,
} from "@mui/material"
import AddIcon from "@mui/icons-material/AddOutlined"
import DeleteIcon from "@mui/icons-material/DeleteOutlineOutlined"
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

export default function TTSProviderSection({ settings, updateSettings }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const setSettings = useAppStore((s) => s.setSettings)
  const connections = settings?.tts_connections || {}
  const activeId = settings?.tts_connection_id || ""

  const [selectedId, setSelectedId] = useState(activeId || Object.keys(connections)[0] || "")
  const [isNew, setIsNew] = useState(!selectedId)
  const [label, setLabel] = useState("")
  const [provider, setProvider] = useState("edge_tts")
  const [voice, setVoice] = useState("")
  const [model, setModel] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [region, setRegion] = useState("")
  const [key, setKey] = useState("")
  const [voices, setVoices] = useState([])
  const [models, setModels] = useState([])
  const [health, setHealth] = useState(null)
  const [saving, setSaving] = useState(false)
  const [probing, setProbing] = useState(false)
  const [loadingVoices, setLoadingVoices] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const audioRef = useRef(null)
  const blobUrlRef = useRef(null)

  const selected = selectedId ? connections[selectedId] : null
  const meta = PROVIDERS[provider] || PROVIDERS.edge_tts
  const keyIsSet = !!selected?.api_key_set && selected?.provider === provider
  const selectedVoice = useMemo(
    () => voices.find((item) => item.voice_id === voice),
    [voices, voice],
  )

  const stopPreview = () => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.removeAttribute("src")
      try { audioRef.current.load() } catch { /* detached */ }
    }
    if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current)
    blobUrlRef.current = null
  }

  useEffect(() => () => stopPreview(), [])

  const loadConnection = (id) => {
    const cfg = connections[id]
    if (!cfg) return
    setSelectedId(id)
    setIsNew(false)
    setLabel(cfg.label || id)
    setProvider(cfg.provider || "edge_tts")
    setVoice(cfg.voice || "")
    setModel(cfg.model || PROVIDERS[cfg.provider]?.defaultModel || "")
    setBaseUrl(cfg.base_url || PROVIDERS[cfg.provider]?.defaultBase || "")
    setRegion(cfg.region || "")
    setKey("")
    setVoices([])
    setModels([])
    setHealth(null)
  }

  useEffect(() => {
    if (selectedId && connections[selectedId]) {
      loadConnection(selectedId)
      return
    }
    const id = activeId || Object.keys(connections)[0]
    if (id) loadConnection(id)
  }, [activeId, settings?.tts_connections])

  const newConnection = () => {
    stopPreview()
    setSelectedId("")
    setIsNew(true)
    setLabel("")
    setProvider("edge_tts")
    setVoice("")
    setModel("")
    setBaseUrl("")
    setRegion("")
    setKey("")
    setVoices([])
    setModels([])
    setHealth(null)
  }

  const changeProvider = (next) => {
    stopPreview()
    setProvider(next)
    setVoice("")
    setModel(PROVIDERS[next]?.defaultModel || "")
    setBaseUrl(PROVIDERS[next]?.defaultBase || "")
    setRegion("")
    setKey("")
    setVoices([])
    setModels([])
    setHealth(null)
  }

  const probePayload = () => ({
    ...(selectedId ? { connection_id: selectedId } : {}),
    provider,
    voice_id: voice.trim() || null,
    model: model.trim() || null,
    base_url: baseUrl.trim() || null,
    region: region.trim() || null,
    ...(key.trim() ? { api_key: key.trim() } : {}),
  })

  const save = async () => {
    if (!label.trim()) {
      showSnackbar("Đặt tên cho kết nối TTS", "warning")
      return
    }
    if (meta.needsKey && !key.trim() && !keyIsSet) {
      showSnackbar("Thêm API key cho kết nối TTS này", "warning")
      return
    }
    if (provider === "elevenlabs" && !voice.trim()) {
      showSnackbar("Chọn một giọng ElevenLabs", "warning")
      return
    }
    if (meta.base && !baseUrl.trim()) {
      showSnackbar("Provider này cần Base URL", "warning")
      return
    }
    if (meta.region && !region.trim() && !baseUrl.trim()) {
      showSnackbar("Cần Azure Region hoặc endpoint tùy chỉnh", "warning")
      return
    }

    setSaving(true)
    try {
      const { data } = await http.post("/api/settings/tts/connections", {
        connection_id: selectedId || null,
        label: label.trim(),
        provider,
        voice: voice.trim() || null,
        model: model.trim() || null,
        base_url: baseUrl.trim() || null,
        region: region.trim() || null,
        ...(key.trim() ? { api_key: key.trim() } : {}),
        activate: true,
      })
      setSettings(data)
      setSelectedId(data.tts_connection_id || selectedId)
      setIsNew(false)
      setKey("")
      showSnackbar("Đã lưu và kích hoạt kết nối TTS", "success")
    } catch (err) {
      showSnackbar(await errorMessage(err, "Lưu kết nối TTS thất bại"), "error")
    } finally {
      setSaving(false)
    }
  }

  const activate = async () => {
    if (!selectedId || selectedId === activeId) return
    setSaving(true)
    try {
      const data = await updateSettings({ tts_connection_id: selectedId })
      setSettings(data)
      showSnackbar("Đã chuyển kết nối TTS active", "success")
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!selectedId) return
    setSaving(true)
    try {
      const { data } = await http.delete(`/api/settings/tts/connections/${selectedId}`)
      setSettings(data)
      const nextId = data.tts_connection_id || Object.keys(data.tts_connections || {})[0] || ""
      setSelectedId(nextId)
      setIsNew(!nextId)
      showSnackbar("Đã xóa kết nối TTS", "info")
    } catch (err) {
      showSnackbar(await errorMessage(err, "Không thể xóa kết nối TTS"), "error")
    } finally {
      setSaving(false)
    }
  }

  const loadVoices = async () => {
    setLoadingVoices(true)
    try {
      const { data } = await http.post("/api/settings/tts/voices", probePayload())
      const found = data?.voices || []
      setVoices(found)
      if (Array.isArray(data?.models)) setModels(data.models)
      if (!voice && found.length === 1) setVoice(found[0].voice_id)
      showSnackbar(found.length ? `Tìm thấy ${found.length} giọng` : "Không tìm thấy giọng nào", found.length ? "success" : "warning")
    } catch (err) {
      showSnackbar(await errorMessage(err, "Không thể tải danh sách giọng"), "error")
    } finally {
      setLoadingVoices(false)
    }
  }

  const testConnection = async () => {
    setProbing(true)
    setHealth(null)
    try {
      const { data } = await http.post("/api/settings/tts/test", probePayload())
      setHealth(data)
      if (Array.isArray(data?.voices)) setVoices(data.voices)
      if (Array.isArray(data?.models)) setModels(data.models)
      showSnackbar(data?.message || "Kiểm tra TTS hoàn tất", data?.ok ? "success" : "warning")
    } catch (err) {
      const message = await errorMessage(err, "Kiểm tra kết nối TTS thất bại")
      setHealth({ ok: false, message })
      showSnackbar(message, "error")
    } finally {
      setProbing(false)
    }
  }

  const previewVoice = async () => {
    if (!voice.trim()) {
      showSnackbar("Chọn hoặc nhập ID giọng trước", "warning")
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

  return (
    <Stack spacing={2}>
      <Alert severity="info" variant="outlined">
        <Typography variant="body2" sx={{ fontWeight: 700, mb: 0.5 }}>
          Đây chỉ là kết nối TTS dùng chung
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Provider / API key / Base URL / model / giọng mặc định nằm ở đây. AI dịch C6 và AI viết lại C7 nằm trong Kết nối AI + Hồ sơ bản địa hóa. Audio ducking, giữ nhạc nền, render và QC là lớp khác — không phải TTS.
        </Typography>
      </Alert>

      <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
        <Typography variant="caption" color="text.secondary">Luồng cấu hình:</Typography>
        <Typography variant="caption"><strong>1. AI dịch C6</strong></Typography>
        <Typography variant="caption">→</Typography>
        <Typography variant="caption"><strong>2. AI viết lại C7</strong></Typography>
        <Typography variant="caption">→</Typography>
        <Typography variant="caption"><strong>3. TTS tạo giọng</strong></Typography>
        <Typography variant="caption">→</Typography>
        <Typography variant="caption"><strong>4. Audio / render / QC</strong></Typography>
      </Stack>

      <Alert severity="info" variant="outlined">
        <Typography variant="body2" sx={{ fontWeight: 700 }}>TTS Connection Registry</Typography>
        <Typography variant="caption" color="text.secondary">
          Mỗi account/endpoint có connection ID riêng. Profile ghim connection cụ thể; TTS không tự failover sang provider khác vì đổi provider có thể đổi giọng/chất lượng.
        </Typography>
      </Alert>

      <Stack direction={{ xs: "column", md: "row" }} spacing={1}>
        <FormControl size="small" fullWidth>
          <InputLabel>Kết nối TTS đã lưu</InputLabel>
          <Select value={selectedId} label="Kết nối TTS đã lưu" onChange={(e) => loadConnection(e.target.value)}>
            {Object.entries(connections).map(([id, cfg]) => (
              <MenuItem key={id} value={id}>
                {cfg.label || id} · {cfg.provider}{id === activeId ? " · ACTIVE" : ""}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <Button variant="outlined" startIcon={<AddIcon />} onClick={newConnection}>Thêm kết nối</Button>
      </Stack>

      <TextField
        label="Tên kết nối"
        size="small"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        placeholder="ví dụ: ElevenLabs chính, Edge miễn phí, SoVITS local 2"
        helperText={selectedId ? `Connection ID: ${selectedId}` : "Provider không còn là identity; có thể lưu nhiều connection cùng provider."}
      />

      <FormControl size="small" fullWidth>
        <InputLabel>Provider TTS</InputLabel>
        <Select
          value={provider}
          label="Provider TTS"
          onChange={(e) => changeProvider(e.target.value)}
          disabled={!isNew}
        >
          {Object.entries(PROVIDERS).map(([id, item]) => <MenuItem key={id} value={id}>{item.label}</MenuItem>)}
        </Select>
      </FormControl>

      {!isNew && (
        <Typography variant="caption" color="text.secondary">
          Provider TTS của connection là cố định. Tạo connection mới nếu muốn đổi provider để job cũ vẫn resolve đúng giọng/credential.
        </Typography>
      )}

      {provider === "elevenlabs" && voices.length > 0 ? (
        <FormControl size="small" fullWidth>
          <InputLabel>Giọng ElevenLabs</InputLabel>
          <Select value={selectedVoice ? voice : ""} label="Giọng ElevenLabs" onChange={(e) => setVoice(e.target.value)}>
            {voices.map((item) => <MenuItem key={item.voice_id} value={item.voice_id}>{item.name} · {item.voice_id}</MenuItem>)}
          </Select>
        </FormControl>
      ) : (
        <TextField label="ID giọng" size="small" value={voice} onChange={(e) => setVoice(e.target.value)} />
      )}

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
              return <MenuItem key={id} value={id}>{item.name || id} · {id}</MenuItem>
            })}
            <MenuItem value="__custom__"><em>Nhập model khác…</em></MenuItem>
          </Select>
        </FormControl>
      ) : provider !== "edge_tts" ? (
        <TextField label="Mô hình" size="small" value={model} onChange={(e) => setModel(e.target.value)} placeholder={meta.defaultModel || "provider model id"} />
      ) : null}

      {meta.base && <TextField label="Base URL" size="small" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={meta.defaultBase || "http://..."} />}
      {meta.region && <TextField label="Vùng Azure" size="small" value={region} onChange={(e) => setRegion(e.target.value)} />}

      {provider !== "edge_tts" && provider !== "gpt_sovits" && (
        <TextField
          label="API key"
          type="password"
          size="small"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder={keyIsSet ? "Đã lưu cho connection này — để trống để giữ" : "BYOK key"}
          autoComplete="off"
        />
      )}

      {provider === "elevenlabs" && (
        <Button variant="outlined" onClick={loadVoices} disabled={loadingVoices} sx={{ alignSelf: "flex-start" }}>
          {loadingVoices ? <CircularProgress size={18} /> : "Tải danh sách giọng + model"}
        </Button>
      )}

      {health && (
        <Alert severity={health.ok ? (health.connection_verified === false ? "warning" : "success") : "error"}>
          {health.message || (health.ok ? "Cấu hình hợp lệ" : "Kết nối không hợp lệ")}
        </Alert>
      )}

      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        <Button variant="outlined" onClick={testConnection} disabled={probing}>Kiểm tra kết nối</Button>
        <Button variant="outlined" onClick={previewVoice} disabled={previewing || !voice.trim()}>Nghe thử giọng</Button>
        <Box sx={{ flex: 1 }} />
        {!isNew && selectedId !== activeId && <Button variant="outlined" onClick={activate} disabled={saving}>Kích hoạt</Button>}
        {!isNew && selectedId !== activeId && (
          <Button variant="outlined" color="error" startIcon={<DeleteIcon />} onClick={remove} disabled={saving}>Xóa</Button>
        )}
        <Button variant="contained" startIcon={<SaveOutlinedIcon />} onClick={save} disabled={saving || probing}>
          {saving ? "Đang lưu…" : "Lưu kết nối"}
        </Button>
      </Stack>
    </Stack>
  )
}
