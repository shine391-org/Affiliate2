import { useEffect, useMemo, useState } from "react"
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, IconButton, InputAdornment, InputLabel, MenuItem, Select, Stack,
  TextField, Typography,
} from "@mui/material"
import VisibilityIcon from "@mui/icons-material/VisibilityOutlined"
import VisibilityOffIcon from "@mui/icons-material/VisibilityOffOutlined"
import SaveIcon from "@mui/icons-material/SaveOutlined"
import EditIcon from "@mui/icons-material/EditOutlined"
import DeleteIcon from "@mui/icons-material/DeleteOutlineOutlined"
import CheckCircleIcon from "@mui/icons-material/CheckCircleOutline"
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

const isCompatible = (provider) => ["gemini", "ollama", "lmstudio", "vllm", "compatible"].includes(provider)
const savedReady = (settings) => {
  const provider = settings?.ai_provider || "openai"
  const meta = PROVIDERS[provider] || PROVIDERS.openai
  if (meta.keyRequired) return !!settings?.ai_api_key_set
  const base = settings?.ai_base_url || meta.baseUrl
  return !!(base && settings?.ai_model)
}

export default function AIProviderSection({ settings, updateSettings }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const initialProvider = settings?.ai_provider || "openai"
  const [provider, setProvider] = useState(initialProvider)
  const [model, setModel] = useState(settings?.ai_model || "")
  const [baseUrl, setBaseUrl] = useState(settings?.ai_base_url || PROVIDERS[initialProvider]?.baseUrl || "")
  const [apiKey, setApiKey] = useState("")
  const [showKey, setShowKey] = useState(false)
  const [registry, setRegistry] = useState({})
  const [discovered, setDiscovered] = useState([])
  const [saving, setSaving] = useState(false)
  const [probing, setProbing] = useState(false)
  const [editing, setEditing] = useState(!savedReady(settings))
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [effectiveRuntime, setEffectiveRuntime] = useState(null)

  useEffect(() => {
    if (!settings) return
    const p = settings.ai_provider || "openai"
    setProvider(p)
    setModel(settings.ai_model || "")
    setBaseUrl(settings.ai_base_url || PROVIDERS[p]?.baseUrl || "")
    if (!savedReady(settings)) setEditing(true)
  }, [settings?.ai_provider, settings?.ai_model, settings?.ai_base_url, settings?.ai_api_key_set])

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
  }, [settings?.ai_provider, settings?.ai_model, settings?.ai_base_url, settings?.ai_api_key_set])

  const meta = PROVIDERS[provider] || PROVIDERS.compatible
  const defaultModel = registry[provider]?.default_model || meta.defaultModel || ""
  const effectiveModel = model || defaultModel
  const suggestions = useMemo(
    () => [...new Set([...(registry[provider]?.models || []), ...discovered])],
    [registry, provider, discovered],
  )
  const savedProvider = settings?.ai_provider_profiles?.[provider] || null
  const keyIsSet = provider === settings?.ai_provider
    ? !!settings?.ai_api_key_set
    : !!savedProvider?.api_key_set
  const connected = savedReady(settings)

  const effectiveRuntimeCard = (
    <Alert
      severity={effectiveRuntime?.configured ? "info" : "warning"}
      variant="outlined"
    >
      {effectiveRuntime?.configured ? (
        <Stack spacing={0.5}>
          <Typography variant="body2" sx={{ fontWeight: 700 }}>
            AI runtime thực tế
          </Typography>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            <Chip size="small" label={"Nguồn: " + (effectiveRuntime.source === "settings" ? "Cài đặt" : ".env / môi trường")} />
            <Chip size="small" label={"Provider: " + (effectiveRuntime.provider || "—")} />
            <Chip size="small" label={"Model: " + (effectiveRuntime.model || "mặc định")} />
          </Stack>
          {effectiveRuntime.base_url && (
            <Typography variant="caption" color="text.secondary" sx={{ wordBreak: "break-all" }}>
              Base URL: {effectiveRuntime.base_url}
            </Typography>
          )}
          {effectiveRuntime.source === "environment" && (
            <Typography variant="caption" color="text.secondary">
              Vì vậy pipeline vẫn có thể chạy dù form bên dưới chưa lưu API key. Muốn UI và runtime khớp tuyệt đối, hãy cấu hình cùng provider trong Cài đặt.
            </Typography>
          )}
        </Stack>
      ) : (
        <Typography variant="body2">
          Không có AI runtime hợp lệ trong Cài đặt hoặc môi trường. Các bước cần AI phải báo lỗi rõ thay vì fallback bí mật.
        </Typography>
      )}
    </Alert>
  )

  const probePayload = () => ({
    provider,
    model: effectiveModel.trim() || null,
    base_url: isCompatible(provider) ? (baseUrl.trim() || null) : null,
    ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
  })

  const handleProviderChange = (next) => {
    const saved = settings?.ai_provider_profiles?.[next] || null
    setProvider(next)
    setModel(saved?.model || "")
    setDiscovered([])
    setBaseUrl(saved?.base_url || PROVIDERS[next]?.baseUrl || "")
    setApiKey("")
  }

  const handleSave = async () => {
    const hasNewKey = !!apiKey.trim()
    const switchingProvider = provider !== (settings?.ai_provider || "openai")
    if (meta.keyRequired && !hasNewKey && !keyIsSet) {
      showSnackbar("Dán API key cho nhà cung cấp này", "warning")
      return
    }
    if (!effectiveModel.trim()) {
      showSnackbar("Nhập hoặc dò tìm ID mô hình", "warning")
      return
    }
    if (isCompatible(provider) && !baseUrl.trim()) {
      showSnackbar("Cần Base URL", "warning")
      return
    }

    setSaving(true)
    try {
      const updates = {
        ai_provider: provider,
        ai_model: effectiveModel.trim(),
        ai_base_url: isCompatible(provider) ? baseUrl.trim() : "",
      }
      if (hasNewKey) updates.ai_api_key = apiKey.trim()
      await updateSettings(updates)
      setApiKey("")
      setEditing(false)
      showSnackbar("Đã lưu nhà cung cấp AI", "success")
    } finally {
      setSaving(false)
    }
  }

  const handleDiscover = async () => {
    setProbing(true)
    try {
      const { data } = await http.post("/api/settings/ai/models", probePayload())
      const models = data?.models || []
      setDiscovered(models)
      if (!model && models.length === 1) setModel(models[0])
      showSnackbar(models.length ? `Tìm thấy ${models.length} mô hình` : "Không có mô hình nào được trả về", models.length ? "success" : "warning")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Dò tìm mô hình thất bại", "error")
    } finally {
      setProbing(false)
    }
  }

  const handleTest = async () => {
    if (!effectiveModel.trim()) {
      showSnackbar("Hãy nhập hoặc dò tìm ID mô hình trước", "warning")
      return
    }
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

  const handleRemove = async () => {
    setConfirmRemove(false)
    setSaving(true)
    try {
      await updateSettings({ ai_api_key: "" })
      setApiKey("")
      setEditing(true)
      showSnackbar("Đã xóa API key đã lưu", "info")
    } finally {
      setSaving(false)
    }
  }

  if (!editing && connected) {
    return (
      <Stack spacing={2}>
        {effectiveRuntimeCard}
        <Alert severity="success" variant="outlined" icon={<CheckCircleIcon fontSize="small" />}>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>Đã cấu hình nhà cung cấp AI</Typography>
            <Chip size="small" label={PROVIDERS[settings.ai_provider]?.label || settings.ai_provider} color="primary" variant="outlined" />
            {settings.ai_model && <Chip size="small" label={`mô hình: ${settings.ai_model}`} variant="outlined" />}
          </Stack>
          {settings.ai_base_url && (
            <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: 0.75 }}>
              {settings.ai_base_url}
            </Typography>
          )}
        </Alert>
        <Stack direction="row" spacing={1.5}>
          <Button variant="contained" startIcon={<EditIcon />} onClick={() => setEditing(true)} disabled={saving}>Cập nhật</Button>
          {keyIsSet && (
            <Button variant="outlined" color="error" startIcon={<DeleteIcon />} onClick={() => setConfirmRemove(true)} disabled={saving}>
              Xóa khóa
            </Button>
          )}
        </Stack>
        <Dialog open={confirmRemove} onClose={() => setConfirmRemove(false)} maxWidth="xs" fullWidth>
          <DialogTitle>Xóa API key?</DialogTitle>
          <DialogContent><Typography variant="body2">Thao tác này xóa khóa đã mã hóa khỏi cơ sở dữ liệu cục bộ.</Typography></DialogContent>
          <DialogActions>
            <Button onClick={() => setConfirmRemove(false)}>Hủy</Button>
            <Button color="error" variant="contained" onClick={handleRemove}>Xóa khóa</Button>
          </DialogActions>
        </Dialog>
      </Stack>
    )
  }

  return (
    <Stack spacing={2}>
      {effectiveRuntimeCard}
      <Typography variant="body2" color="text.secondary">
        Chọn cloud provider hoặc endpoint tương thích OpenAI chạy local. Gemini dùng endpoint OpenAI-compatible chính thức của Google; không có runtime riêng thứ hai.
      </Typography>

      <FormControl size="small" fullWidth>
        <InputLabel>Nhà cung cấp</InputLabel>
        <Select value={provider} label="Nhà cung cấp" onChange={(e) => handleProviderChange(e.target.value)} disabled={saving || probing}>
          {Object.entries(PROVIDERS).map(([id, item]) => (
            <MenuItem key={id} value={id}>
              {item.label}{settings?.ai_provider_profiles?.[id] ? " · đã lưu" : ""}
            </MenuItem>
          ))}
        </Select>
      </FormControl>

      {isCompatible(provider) && (
        <TextField
          label="Base URL"
          size="small"
          fullWidth
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder={meta.baseUrl || "http://localhost:8000/v1"}
          disabled={saving || probing}
        />
      )}

      {suggestions.length > 0 && (
        <FormControl size="small" fullWidth>
          <InputLabel>Chọn mô hình</InputLabel>
          <Select
            value={suggestions.includes(effectiveModel) ? effectiveModel : "__custom__"}
            label="Chọn mô hình"
            onChange={(e) => setModel(e.target.value === "__custom__" ? "" : e.target.value)}
            disabled={saving || probing}
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
          fullWidth
          value={effectiveModel}
          onChange={(e) => setModel(e.target.value)}
          placeholder="ví dụ: qwen3:14b hoặc provider/model"
          disabled={saving || probing}
          helperText={suggestions.length
            ? "Chọn mô hình đã quét ở trên hoặc nhập ID tùy chỉnh."
            : "Nhập ID mô hình hoặc bấm Dò tìm mô hình để tải danh sách chọn."}
        />
      )}

      <TextField
        label={meta.keyRequired ? "API key" : "API key (tùy chọn)"}
        type={showKey ? "text" : "password"}
        size="small"
        fullWidth
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        placeholder={keyIsSet ? "(đã lưu cho provider này — để trống để giữ)" : (meta.placeholder || "tùy chọn với máy chủ cục bộ")}
        disabled={saving || probing}
        autoComplete="off"
        InputProps={{
          endAdornment: (
            <InputAdornment position="end">
              <IconButton size="small" onClick={() => setShowKey((v) => !v)} edge="end" tabIndex={-1}>
                {showKey ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
              </IconButton>
            </InputAdornment>
          ),
        }}
      />

      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <Button variant="outlined" onClick={handleDiscover} disabled={saving || probing || provider === "anthropic"}>
          {probing ? "Đang kiểm tra…" : "Dò tìm mô hình"}
        </Button>
        <Button variant="outlined" onClick={handleTest} disabled={saving || probing}>
          Kiểm tra kết nối
        </Button>
        <Box sx={{ flex: 1 }} />
        {connected && <Button variant="text" onClick={() => setEditing(false)} disabled={saving || probing}>Hủy</Button>}
        <Button variant="contained" startIcon={<SaveIcon />} onClick={handleSave} disabled={saving || probing}>
          {saving ? "Đang lưu…" : "Lưu"}
        </Button>
      </Stack>
    </Stack>
  )
}
