import { useEffect, useMemo, useState } from "react"
import {
  Alert, Box, Button, Chip, FormControl, IconButton, InputAdornment,
  InputLabel, MenuItem, Select, Stack, TextField, Typography,
} from "@mui/material"
import AddIcon from "@mui/icons-material/AddOutlined"
import DeleteIcon from "@mui/icons-material/DeleteOutlineOutlined"
import SaveIcon from "@mui/icons-material/SaveOutlined"
import VisibilityIcon from "@mui/icons-material/VisibilityOutlined"
import VisibilityOffIcon from "@mui/icons-material/VisibilityOffOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

const PROVIDERS = {
  anthropic: { label: "Anthropic (Claude)", keyRequired: true, placeholder: "sk-ant-..." },
  openai: { label: "OpenAI (GPT)", keyRequired: true, placeholder: "sk-..." },
  openrouter: { label: "OpenRouter", keyRequired: true, placeholder: "sk-or-..." },
  gemini: {
    label: "Google Gemini (AI Studio)",
    keyRequired: true,
    placeholder: "AIza...",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai/",
    defaultModel: "gemini-3.8-flash",
  },
  ollama: { label: "Ollama (local)", baseUrl: "http://localhost:11434/v1" },
  lmstudio: { label: "LM Studio (local)", baseUrl: "http://localhost:1234/v1" },
  vllm: { label: "vLLM (local)", baseUrl: "http://localhost:8000/v1" },
  compatible: { label: "Tùy chỉnh tương thích OpenAI" },
}

const isCompatible = (provider) =>
  ["gemini", "ollama", "lmstudio", "vllm", "compatible"].includes(provider)

export default function AIProviderSection({ settings, updateSettings }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const setSettings = useAppStore((s) => s.setSettings)
  const connections = settings?.ai_connections || {}
  const activeId = settings?.ai_connection_id || ""

  const firstId = activeId || Object.keys(connections)[0] || ""
  const [selectedId, setSelectedId] = useState(firstId)
  const [isNew, setIsNew] = useState(!firstId)
  const [label, setLabel] = useState("")
  const [provider, setProvider] = useState("compatible")
  const [model, setModel] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [showKey, setShowKey] = useState(false)
  const [registry, setRegistry] = useState({})
  const [discovered, setDiscovered] = useState([])
  const [saving, setSaving] = useState(false)
  const [probing, setProbing] = useState(false)
  const [effectiveRuntime, setEffectiveRuntime] = useState(null)

  const selected = selectedId ? connections[selectedId] : null
  const meta = PROVIDERS[provider] || PROVIDERS.compatible
  const defaultModel = registry[provider]?.default_model || meta.defaultModel || ""
  const effectiveModel = model || defaultModel
  const suggestions = useMemo(
    () => [...new Set([...(registry[provider]?.models || []), ...discovered])],
    [registry, provider, discovered],
  )
  const keyIsSet = !!selected?.api_key_set

  const loadConnection = (id) => {
    const cfg = connections[id]
    if (!cfg) return
    setSelectedId(id)
    setIsNew(false)
    setLabel(cfg.label || id)
    setProvider(cfg.provider || "compatible")
    setModel(cfg.model || "")
    setBaseUrl(cfg.base_url || PROVIDERS[cfg.provider]?.baseUrl || "")
    setApiKey("")
    setDiscovered([])
  }

  useEffect(() => {
    if (selectedId && connections[selectedId]) {
      loadConnection(selectedId)
      return
    }
    const id = activeId || Object.keys(connections)[0]
    if (id) loadConnection(id)
  }, [activeId, settings?.ai_connections])

  useEffect(() => {
    http.get("/api/config/model_registry")
      .then(({ data }) => setRegistry(data?.value || {}))
      .catch(() => {})
  }, [])

  useEffect(() => {
    let alive = true
    http.get("/api/settings/ai/effective")
      .then(({ data }) => { if (alive) setEffectiveRuntime(data || null) })
      .catch(() => { if (alive) setEffectiveRuntime(null) })
    return () => { alive = false }
  }, [activeId, settings?.ai_provider, settings?.ai_model])

  const newConnection = () => {
    setSelectedId("")
    setIsNew(true)
    setLabel("")
    setProvider("compatible")
    setModel("")
    setBaseUrl("")
    setApiKey("")
    setDiscovered([])
  }

  const changeProvider = (next) => {
    setProvider(next)
    setModel("")
    setBaseUrl(PROVIDERS[next]?.baseUrl || "")
    setApiKey("")
    setDiscovered([])
  }

  const probePayload = () => ({
    ...(selectedId ? { connection_id: selectedId } : {}),
    provider,
    model: effectiveModel.trim() || null,
    base_url: isCompatible(provider) ? (baseUrl.trim() || null) : null,
    ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
  })

  const save = async () => {
    if (!label.trim()) {
      showSnackbar("Đặt tên cho kết nối để có thể dùng nhiều endpoint cùng provider", "warning")
      return
    }
    if (!effectiveModel.trim()) {
      showSnackbar("Nhập hoặc dò tìm ID mô hình", "warning")
      return
    }
    if (isCompatible(provider) && !baseUrl.trim()) {
      showSnackbar("Cần Base URL cho endpoint tương thích", "warning")
      return
    }
    if (meta.keyRequired && !apiKey.trim() && !keyIsSet) {
      showSnackbar("Dán API key cho kết nối này", "warning")
      return
    }

    setSaving(true)
    try {
      const { data } = await http.post("/api/settings/ai/connections", {
        connection_id: selectedId || null,
        label: label.trim(),
        provider,
        model: effectiveModel.trim(),
        base_url: isCompatible(provider) ? baseUrl.trim() : null,
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        activate: true,
      })
      setSettings(data)
      setSelectedId(data.ai_connection_id || selectedId)
      setIsNew(false)
      setApiKey("")
      showSnackbar("Đã lưu và kích hoạt kết nối AI", "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Lưu kết nối AI thất bại", "error")
    } finally {
      setSaving(false)
    }
  }

  const activate = async () => {
    if (!selectedId || selectedId === activeId) return
    setSaving(true)
    try {
      const data = await updateSettings({ ai_connection_id: selectedId })
      setSettings(data)
      showSnackbar("Đã chuyển kết nối AI active", "success")
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!selectedId) return
    setSaving(true)
    try {
      const { data } = await http.delete(`/api/settings/ai/connections/${selectedId}`)
      setSettings(data)
      const nextId = data.ai_connection_id || Object.keys(data.ai_connections || {})[0] || ""
      setSelectedId(nextId)
      setIsNew(!nextId)
      showSnackbar("Đã xóa kết nối AI", "info")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Không thể xóa kết nối AI", "error")
    } finally {
      setSaving(false)
    }
  }

  const discover = async () => {
    setProbing(true)
    try {
      const { data } = await http.post("/api/settings/ai/models", probePayload())
      const models = data?.models || []
      setDiscovered(models)
      if (!model && models.length === 1) setModel(models[0])
      showSnackbar(
        models.length ? `Tìm thấy ${models.length} mô hình` : "Không có mô hình nào được trả về",
        models.length ? "success" : "warning",
      )
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Dò tìm mô hình thất bại", "error")
    } finally {
      setProbing(false)
    }
  }

  const testConnection = async () => {
    setProbing(true)
    try {
      await http.post("/api/settings/ai/test", probePayload())
      showSnackbar("Kết nối OK", "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Kết nối thất bại", "error")
    } finally {
      setProbing(false)
    }
  }

  return (
    <Stack spacing={2}>
      <Alert severity="info" variant="outlined">
        <Typography variant="body2" sx={{ fontWeight: 700 }}>Connection Registry</Typography>
        <Typography variant="caption" color="text.secondary">
          Mỗi endpoint/account có một connection ID riêng. Vì vậy có thể lưu Hermes A + Hermes B + nhiều LM Studio cùng lúc dù tất cả đều dùng transport compatible.
        </Typography>
      </Alert>

      {effectiveRuntime?.configured && (
        <Alert severity="success" variant="outlined">
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            <Chip size="small" label={`Active: ${activeId || effectiveRuntime.provider}`} />
            <Chip size="small" label={`Provider: ${effectiveRuntime.provider || "—"}`} />
            <Chip size="small" label={`Model: ${effectiveRuntime.model || "—"}`} />
          </Stack>
        </Alert>
      )}

      <Stack direction={{ xs: "column", md: "row" }} spacing={1}>
        <FormControl size="small" fullWidth>
          <InputLabel>Kết nối đã lưu</InputLabel>
          <Select
            value={selectedId}
            label="Kết nối đã lưu"
            onChange={(e) => loadConnection(e.target.value)}
          >
            {Object.entries(connections).map(([id, cfg]) => (
              <MenuItem key={id} value={id}>
                {cfg.label || id} · {cfg.provider}{id === activeId ? " · ACTIVE" : ""}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <Button variant="outlined" startIcon={<AddIcon />} onClick={newConnection}>
          Thêm kết nối
        </Button>
      </Stack>

      <TextField
        label="Tên kết nối"
        size="small"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        placeholder="ví dụ: Hermes chính, Hermes dự phòng, LM Studio Qwen"
        helperText={selectedId ? `Connection ID: ${selectedId}` : "Tên sẽ tạo connection ID ổn định; provider không còn là identity."}
      />

      <FormControl size="small" fullWidth>
        <InputLabel>Provider / transport</InputLabel>
        <Select value={provider} label="Provider / transport" onChange={(e) => changeProvider(e.target.value)}>
          {Object.entries(PROVIDERS).map(([id, item]) => (
            <MenuItem key={id} value={id}>{item.label}</MenuItem>
          ))}
        </Select>
      </FormControl>

      {isCompatible(provider) && (
        <TextField
          label="Base URL"
          size="small"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder={meta.baseUrl || "http://127.0.0.1:8645/v1"}
        />
      )}

      {suggestions.length > 0 && (
        <FormControl size="small" fullWidth>
          <InputLabel>Chọn mô hình</InputLabel>
          <Select
            value={suggestions.includes(effectiveModel) ? effectiveModel : "__custom__"}
            label="Chọn mô hình"
            onChange={(e) => setModel(e.target.value === "__custom__" ? "" : e.target.value)}
          >
            {suggestions.map((m) => <MenuItem key={m} value={m}>{m}</MenuItem>)}
            <MenuItem value="__custom__"><em>Nhập ID khác…</em></MenuItem>
          </Select>
        </FormControl>
      )}

      {(suggestions.length === 0 || !suggestions.includes(effectiveModel)) && (
        <TextField
          label="ID mô hình"
          size="small"
          value={effectiveModel}
          onChange={(e) => setModel(e.target.value)}
          placeholder="provider/model hoặc model local"
        />
      )}

      <TextField
        label={meta.keyRequired ? "API key" : "API key (tùy chọn)"}
        type={showKey ? "text" : "password"}
        size="small"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        placeholder={keyIsSet ? "Đã lưu cho connection này — để trống để giữ" : (meta.placeholder || "local thường không cần")}
        autoComplete="off"
        InputProps={{
          endAdornment: (
            <InputAdornment position="end">
              <IconButton size="small" onClick={() => setShowKey((v) => !v)} edge="end">
                {showKey ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
              </IconButton>
            </InputAdornment>
          ),
        }}
      />

      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
        <Button variant="outlined" onClick={discover} disabled={probing || provider === "anthropic"}>
          {probing ? "Đang kiểm tra…" : "Dò tìm mô hình"}
        </Button>
        <Button variant="outlined" onClick={testConnection} disabled={probing}>Kiểm tra kết nối</Button>
        <Box sx={{ flex: 1 }} />
        {!isNew && selectedId !== activeId && (
          <Button variant="outlined" onClick={activate} disabled={saving}>Kích hoạt</Button>
        )}
        {!isNew && selectedId !== activeId && (
          <Button color="error" variant="outlined" startIcon={<DeleteIcon />} onClick={remove} disabled={saving}>
            Xóa
          </Button>
        )}
        <Button variant="contained" startIcon={<SaveIcon />} onClick={save} disabled={saving || probing}>
          {saving ? "Đang lưu…" : "Lưu kết nối"}
        </Button>
      </Stack>
    </Stack>
  )
}
