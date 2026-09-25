import { useEffect, useState } from "react"
import {
  Alert, Button, FormControl, InputLabel, MenuItem, Select,
  Stack, TextField, Typography,
} from "@mui/material"
import SaveOutlinedIcon from "@mui/icons-material/SaveOutlined"
import useAppStore from "../../store/appStore"

const PROVIDERS = {
  edge_tts: { label: "Edge TTS" },
  openai_tts: { label: "OpenAI TTS", needsKey: true, defaultModel: "tts-1" },
  openai_compatible_tts: { label: "OpenAI-compatible TTS", base: true, defaultModel: "tts-1" },
  azure_tts: { label: "Azure Speech", needsKey: true, region: true },
  elevenlabs: { label: "ElevenLabs", needsKey: true, base: true, defaultModel: "eleven_multilingual_v2" },
  gpt_sovits: { label: "GPT-SoVITS (clone giọng cục bộ)", base: true, defaultBase: "http://127.0.0.1:9880" },
}

export default function TTSProviderSection({ settings, updateSettings }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [provider, setProvider] = useState(settings?.tts_provider || "edge_tts")
  const [voice, setVoice] = useState(settings?.preferred_tts_voice || "")
  const [model, setModel] = useState(settings?.tts_model || "")
  const [baseUrl, setBaseUrl] = useState(settings?.tts_base_url || "")
  const [region, setRegion] = useState(settings?.tts_region || "")
  const [key, setKey] = useState("")
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setProvider(settings?.tts_provider || "edge_tts")
    setVoice(settings?.preferred_tts_voice || "")
    setModel(settings?.tts_model || "")
    setBaseUrl(settings?.tts_base_url || "")
    setRegion(settings?.tts_region || "")
  }, [
    settings?.tts_provider,
    settings?.preferred_tts_voice,
    settings?.tts_model,
    settings?.tts_base_url,
    settings?.tts_region,
  ])

  const meta = PROVIDERS[provider] || PROVIDERS.edge_tts

  const changeProvider = (next) => {
    setProvider(next)
    setVoice("")
    setModel(PROVIDERS[next]?.defaultModel || "")
    setBaseUrl(PROVIDERS[next]?.defaultBase || "")
    setRegion("")
    setKey("")
  }

  const save = async () => {
    if (meta.needsKey && provider !== settings?.tts_provider && !key.trim()) {
      showSnackbar("Thêm API key cho nhà cung cấp TTS mới", "warning")
      return
    }
    if (meta.needsKey && !key.trim() && !settings?.tts_api_key_set) {
      showSnackbar("Thêm API key cho nhà cung cấp TTS này", "warning")
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
        HQ localization generates TTS at natural speed, measures the real audio,
        then fits it in the dubbing engine. Speaker-specific voices and reference
        audio are controlled by Localization Profiles and Expert mode.
      </Alert>

      <FormControl size="small" fullWidth>
        <InputLabel>Nhà cung cấp TTS</InputLabel>
        <Select value={provider} label="Nhà cung cấp TTS" onChange={(e) => changeProvider(e.target.value)}>
          {Object.entries(PROVIDERS).map(([id, p]) => (
            <MenuItem key={id} value={id}>{p.label}</MenuItem>
          ))}
        </Select>
      </FormControl>

      <TextField
        label="ID giọng mặc định"
        size="small"
        value={voice}
        onChange={(e) => setVoice(e.target.value)}
        helperText="Để trống để dùng mặc định của nhà cung cấp/ngôn ngữ. Chế độ nâng cao có thể override per speaker or segment."
      />

      {provider !== "edge_tts" && (
        <TextField
          label="Model"
          size="small"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder={meta.defaultModel || "provider model id"}
        />
      )}

      {meta.base && (
        <TextField
          label="Base URL"
          size="small"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder={meta.defaultBase || "https://..."}
        />
      )}

      {meta.region && (
        <TextField
          label="Azure Region"
          size="small"
          value={region}
          onChange={(e) => setRegion(e.target.value)}
          placeholder="southeastasia"
        />
      )}

      {provider !== "edge_tts" && provider !== "gpt_sovits" && (
        <TextField
          label="API key"
          type="password"
          size="small"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder={settings?.tts_api_key_set ? "(leave empty to keep stored key)" : "BYOK key"}
          autoComplete="off"
        />
      )}

      {provider === "gpt_sovits" && (
        <Typography variant="caption" color="text.secondary">
          GPT-SoVITS reference audio/text belongs to the Localization Profile or a
          speaker override, not a global secret.
        </Typography>
      )}

      <Stack direction="row" justifyContent="flex-end">
        <Button variant="contained" startIcon={<SaveOutlinedIcon />} onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save TTS"}
        </Button>
      </Stack>
    </Stack>
  )
}
