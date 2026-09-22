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
  ollama: { label: "Ollama (local)", baseUrl: "http://localhost:11434/v1" },
  lmstudio: { label: "LM Studio (local)", baseUrl: "http://localhost:1234/v1" },
  vllm: { label: "vLLM (local)", baseUrl: "http://localhost:8000/v1" },
  compatible: { label: "Custom OpenAI-compatible" },
}

const isCompatible = (provider) => ["ollama", "lmstudio", "vllm", "compatible"].includes(provider)
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

  const meta = PROVIDERS[provider] || PROVIDERS.compatible
  const defaultModel = registry[provider]?.default_model || ""
  const effectiveModel = model || defaultModel
  const suggestions = useMemo(
    () => [...new Set([...(registry[provider]?.models || []), ...discovered])],
    [registry, provider, discovered],
  )
  const keyIsSet = !!settings?.ai_api_key_set
  const connected = savedReady(settings)

  const probePayload = () => ({
    provider,
    model: effectiveModel.trim() || null,
    base_url: isCompatible(provider) ? (baseUrl.trim() || null) : null,
    ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
  })

  const handleProviderChange = (next) => {
    setProvider(next)
    setModel("")
    setDiscovered([])
    setBaseUrl(PROVIDERS[next]?.baseUrl || "")
  }

  const handleSave = async () => {
    const hasNewKey = !!apiKey.trim()
    if (meta.keyRequired && !hasNewKey && !keyIsSet) {
      showSnackbar("Paste your API key first", "warning")
      return
    }
    if (!effectiveModel.trim()) {
      showSnackbar("Enter or discover a model ID", "warning")
      return
    }
    if (isCompatible(provider) && !baseUrl.trim()) {
      showSnackbar("Base URL is required", "warning")
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
      showSnackbar("AI provider saved", "success")
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
      showSnackbar(models.length ? `Found ${models.length} models` : "No models returned", models.length ? "success" : "warning")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Model discovery failed", "error")
    } finally {
      setProbing(false)
    }
  }

  const handleTest = async () => {
    if (!effectiveModel.trim()) {
      showSnackbar("Enter or discover a model ID first", "warning")
      return
    }
    setProbing(true)
    try {
      await http.post("/api/settings/ai/test", probePayload())
      showSnackbar("Connection OK", "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Connection failed", "error")
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
      showSnackbar("Stored API key removed", "info")
    } finally {
      setSaving(false)
    }
  }

  if (!editing && connected) {
    return (
      <Stack spacing={2}>
        <Alert severity="success" variant="outlined" icon={<CheckCircleIcon fontSize="small" />}>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>AI provider configured</Typography>
            <Chip size="small" label={PROVIDERS[settings.ai_provider]?.label || settings.ai_provider} color="primary" variant="outlined" />
            {settings.ai_model && <Chip size="small" label={`model: ${settings.ai_model}`} variant="outlined" />}
          </Stack>
          {settings.ai_base_url && (
            <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: 0.75 }}>
              {settings.ai_base_url}
            </Typography>
          )}
        </Alert>
        <Stack direction="row" spacing={1.5}>
          <Button variant="contained" startIcon={<EditIcon />} onClick={() => setEditing(true)} disabled={saving}>Update</Button>
          {keyIsSet && (
            <Button variant="outlined" color="error" startIcon={<DeleteIcon />} onClick={() => setConfirmRemove(true)} disabled={saving}>
              Remove key
            </Button>
          )}
        </Stack>
        <Dialog open={confirmRemove} onClose={() => setConfirmRemove(false)} maxWidth="xs" fullWidth>
          <DialogTitle>Remove API key?</DialogTitle>
          <DialogContent><Typography variant="body2">This deletes the encrypted key from the local database.</Typography></DialogContent>
          <DialogActions>
            <Button onClick={() => setConfirmRemove(false)}>Cancel</Button>
            <Button color="error" variant="contained" onClick={handleRemove}>Remove key</Button>
          </DialogActions>
        </Dialog>
      </Stack>
    )
  }

  return (
    <Stack spacing={2}>
      <Typography variant="body2" color="text.secondary">
        Use a cloud provider or a local OpenAI-compatible server. Model IDs are editable; the registry is only a suggestion list.
      </Typography>

      <FormControl size="small" fullWidth>
        <InputLabel>Provider</InputLabel>
        <Select value={provider} label="Provider" onChange={(e) => handleProviderChange(e.target.value)} disabled={saving || probing}>
          {Object.entries(PROVIDERS).map(([id, item]) => <MenuItem key={id} value={id}>{item.label}</MenuItem>)}
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

      <TextField
        label="Model ID"
        size="small"
        fullWidth
        value={effectiveModel}
        onChange={(e) => setModel(e.target.value)}
        placeholder="e.g. qwen3:14b or provider/model"
        disabled={saving || probing}
        inputProps={{ list: "ai-model-suggestions" }}
        helperText="Type any model ID, or discover models from supported endpoints."
      />
      <datalist id="ai-model-suggestions">
        {suggestions.map((m) => <option key={m} value={m} />)}
      </datalist>

      <TextField
        label={meta.keyRequired ? "API key" : "API key (optional)"}
        type={showKey ? "text" : "password"}
        size="small"
        fullWidth
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        placeholder={keyIsSet ? "(leave empty to keep existing key)" : (meta.placeholder || "optional for local servers")}
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
          {probing ? "Checking…" : "Discover models"}
        </Button>
        <Button variant="outlined" onClick={handleTest} disabled={saving || probing}>
          Test connection
        </Button>
        <Box sx={{ flex: 1 }} />
        {connected && <Button variant="text" onClick={() => setEditing(false)} disabled={saving || probing}>Cancel</Button>}
        <Button variant="contained" startIcon={<SaveIcon />} onClick={handleSave} disabled={saving || probing}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </Stack>
    </Stack>
  )
}
