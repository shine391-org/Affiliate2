import { useEffect, useMemo, useState } from "react"
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Checkbox,
  Chip, Divider, FormControl, FormControlLabel, InputLabel, MenuItem, Select,
  Stack, TextField, Typography,
} from "@mui/material"
import DeleteOutlineOutlinedIcon from "@mui/icons-material/DeleteOutlineOutlined"
import SaveOutlinedIcon from "@mui/icons-material/SaveOutlined"
import AddOutlinedIcon from "@mui/icons-material/AddOutlined"
import ExpandMoreOutlinedIcon from "@mui/icons-material/ExpandMoreOutlined"
import RefreshOutlinedIcon from "@mui/icons-material/RefreshOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

const PRESETS = [
  ["natural_vietnamese", "Natural Vietnamese — faithful first"],
  ["literal", "Literal / maximum fidelity"],
  ["affiliate_conversational", "Affiliate conversational"],
  ["custom", "Custom instructions"],
]

const clone = (value) => JSON.parse(JSON.stringify(value))

const glossaryToText = (glossary = {}) => (
  Object.entries(glossary).map(([src, dst]) => `${src} => ${dst}`).join("\n")
)

const textToGlossary = (text) => {
  const out = {}
  for (const raw of (text || "").split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    const splitAt = line.indexOf("=>")
    if (splitAt < 1) throw new Error(`Invalid glossary line: "${line}". Use source => target`)
    const src = line.slice(0, splitAt).trim()
    const dst = line.slice(splitAt + 2).trim()
    if (!src || !dst) throw new Error(`Invalid glossary line: "${line}". Use source => target`)
    out[src] = dst
  }
  return out
}

export default function LocalizationProfilesSection({ settings }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [store, setStore] = useState({ profiles: [], default_profile_id: "", prompt_defaults: {} })
  const [selectedId, setSelectedId] = useState("")
  const [draft, setDraft] = useState(null)
  const [glossaryText, setGlossaryText] = useState("")
  const [busy, setBusy] = useState(false)
  const [discovering, setDiscovering] = useState(false)
  const [discoveredModels, setDiscoveredModels] = useState([])

  const load = async (preferId = "") => {
    const { data } = await http.get("/api/settings/localization-profiles")
    setStore(data)
    const nextId = preferId || selectedId || data.default_profile_id || data.profiles?.[0]?.id || ""
    setSelectedId(nextId)
    const profile = data.profiles?.find((p) => p.id === nextId) || data.profiles?.[0] || null
    setDraft(profile ? clone(profile) : null)
    setGlossaryText(glossaryToText(profile?.glossary))
  }

  useEffect(() => { load().catch(() => {}) }, [])

  useEffect(() => {
    const profile = store.profiles?.find((p) => p.id === selectedId)
    if (!profile) return
    setDraft(clone(profile))
    setGlossaryText(glossaryToText(profile.glossary))
  }, [selectedId])

  const isDefault = selectedId === store.default_profile_id
  const globalProvider = settings?.ai_provider || "global AI provider"
  const globalModel = settings?.ai_model || "configured global model"
  const modelSummary = useMemo(() => {
    if (!draft) return ""
    return `C6: ${draft.translation_model || globalModel} · C7 rewrite: ${draft.rewrite_model || globalModel}`
  }, [draft, globalModel])

  const patch = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }))

  const discoverModels = async () => {
    const provider = settings?.ai_provider || ""
    if (!provider || provider === "anthropic") {
      showSnackbar("Model discovery is not available for this AI connection", "warning")
      return
    }
    setDiscovering(true)
    try {
      const { data } = await http.post("/api/settings/ai/models", {
        provider,
        model: settings?.ai_model || null,
        base_url: settings?.ai_base_url || null,
      })
      const models = data?.models || []
      setDiscoveredModels(models)
      showSnackbar(models.length ? `Found ${models.length} models on the configured connection` : "No models returned", models.length ? "success" : "warning")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Model discovery failed", "error")
    } finally {
      setDiscovering(false)
    }
  }

  const save = async () => {
    if (!draft) return
    if (!draft.translation_guardrails?.trim() || !draft.rewrite_guardrails?.trim()) {
      showSnackbar("C6 and C7 semantic guardrails cannot be empty", "error")
      return
    }
    setBusy(true)
    try {
      const payload = {
        ...draft,
        translation_provider: draft.translation_provider || null,
        translation_model: draft.translation_model?.trim() || null,
        translation_base_url: draft.translation_base_url?.trim() || null,
        rewrite_provider: draft.rewrite_provider || null,
        rewrite_model: draft.rewrite_model?.trim() || null,
        rewrite_base_url: draft.rewrite_base_url?.trim() || null,
        translation_instructions: draft.translation_instructions || "",
        rewrite_instructions: draft.rewrite_instructions || "",
        translation_guardrails: draft.translation_guardrails.trim(),
        rewrite_guardrails: draft.rewrite_guardrails.trim(),
        glossary: textToGlossary(glossaryText),
      }
      const { data } = await http.post("/api/settings/localization-profiles", payload)
      setStore(data)
      setSelectedId(payload.id)
      setDraft(clone(data.profiles.find((p) => p.id === payload.id) || payload))
      setGlossaryText(glossaryToText(payload.glossary))
      showSnackbar("Localization profile saved", "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || err.message || "Profile save failed", "error")
    } finally {
      setBusy(false)
    }
  }

  const makeDefault = async () => {
    if (!selectedId) return
    setBusy(true)
    try {
      const { data } = await http.post(`/api/settings/localization-profiles/${selectedId}/default`)
      setStore(data)
      showSnackbar("Default localization profile updated", "success")
    } finally {
      setBusy(false)
    }
  }

  const addProfile = () => {
    const id = "profile-" + Date.now().toString(36)
    const base = store.profiles?.find((p) => p.id === store.default_profile_id) || store.profiles?.[0]
    const next = {
      ...(base ? clone(base) : {}),
      id,
      name: "New localization profile",
      translation_preset: "natural_vietnamese",
      translation_instructions: "",
      glossary: {},
      translation_provider: null,
      translation_model: null,
      translation_base_url: null,
      rewrite_provider: null,
      rewrite_model: null,
      rewrite_base_url: null,
      rewrite_instructions: "",
      translation_guardrails: base?.translation_guardrails || store.prompt_defaults?.translation_guardrails || "",
      rewrite_guardrails: base?.rewrite_guardrails || store.prompt_defaults?.rewrite_guardrails || "",
    }
    setSelectedId(id)
    setDraft(next)
    setGlossaryText("")
  }

  const remove = async () => {
    if (!selectedId) return
    setBusy(true)
    try {
      const { data } = await http.delete(`/api/settings/localization-profiles/${selectedId}`)
      setStore(data)
      const nextId = data.default_profile_id || data.profiles?.[0]?.id || ""
      setSelectedId(nextId)
      const profile = data.profiles?.find((p) => p.id === nextId) || null
      setDraft(profile ? clone(profile) : null)
      setGlossaryText(glossaryToText(profile?.glossary))
      showSnackbar("Localization profile removed", "info")
    } finally {
      setBusy(false)
    }
  }

  if (!draft) return <Typography variant="body2" color="text.secondary">Loading localization profiles…</Typography>

  return (
    <Stack spacing={2}>
      <Alert severity="info" variant="outlined">
        Connections and credentials live in Settings. This profile stores reusable production defaults only:
        C6 translation, C7 timing rewrite, ASR, voice/timing, audio/render and QC policy.
      </Alert>

      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <FormControl size="small" fullWidth>
          <InputLabel>Profile</InputLabel>
          <Select value={selectedId} label="Profile" onChange={(e) => setSelectedId(e.target.value)}>
            {store.profiles.map((p) => (
              <MenuItem key={p.id} value={p.id}>
                {p.name}{p.id === store.default_profile_id ? " · default" : ""}
              </MenuItem>
            ))}
            {!store.profiles.some((p) => p.id === selectedId) && draft && (
              <MenuItem value={selectedId}>{draft.name} · unsaved</MenuItem>
            )}
          </Select>
        </FormControl>
        <Button startIcon={<AddOutlinedIcon />} variant="outlined" onClick={addProfile} disabled={busy}>New</Button>
      </Stack>

      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <TextField size="small" fullWidth label="Profile ID" value={draft.id || ""} onChange={(e) => patch("id", e.target.value)} />
        <TextField size="small" fullWidth label="Name" value={draft.name || ""} onChange={(e) => patch("name", e.target.value)} />
      </Stack>

      <Box sx={{ p: 1.5, border: 1, borderColor: "divider", borderRadius: 2 }}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
          <Box sx={{ flex: 1 }}>
            <Typography variant="subtitle2">Configured AI connection</Typography>
            <Typography variant="caption" color="text.secondary">
              {globalProvider} · global/default model {globalModel}
            </Typography>
          </Box>
          <Button
            size="small"
            variant="outlined"
            startIcon={<RefreshOutlinedIcon />}
            onClick={discoverModels}
            disabled={discovering}
          >
            {discovering ? "Discovering…" : "Discover models"}
          </Button>
        </Stack>
        {discoveredModels.length > 0 && (
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
            {discoveredModels.slice(0, 8).map((model) => <Chip key={model} label={model} size="small" variant="outlined" />)}
            {discoveredModels.length > 8 && <Chip label={`+${discoveredModels.length - 8} more`} size="small" />}
          </Stack>
        )}
      </Box>

      <datalist id="localization-model-suggestions">
        {[...new Set([settings?.ai_model, ...discoveredModels].filter(Boolean))].map((model) => <option key={model} value={model} />)}
      </datalist>

      <Accordion defaultExpanded disableGutters>
        <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Translation (C6)</Typography>
            <Typography variant="caption" color="text.secondary">Faithful translation model, style, instructions and glossary</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            <FormControl size="small" fullWidth>
              <InputLabel>Translation style</InputLabel>
              <Select value={draft.translation_preset || "natural_vietnamese"} label="Translation style" onChange={(e) => patch("translation_preset", e.target.value)}>
                {PRESETS.map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
              </Select>
            </FormControl>
            <TextField
              size="small" fullWidth label="C6 translation model"
              value={draft.translation_model || ""}
              onChange={(e) => patch("translation_model", e.target.value)}
              placeholder={globalModel}
              inputProps={{ list: "localization-model-suggestions" }}
              helperText="Blank = inherit the global/default model. Type any compatible model ID or use Discover models."
            />
            <TextField
              size="small" fullWidth label="Compatible Base URL override (optional)"
              value={draft.translation_base_url || ""}
              onChange={(e) => patch("translation_base_url", e.target.value)}
              placeholder={settings?.ai_base_url || "inherit Settings endpoint"}
              helperText="Connection credentials remain in AI Connections."
            />
            <Alert severity="info" variant="outlined">
              Translation instructions are additive. Semantic guardrails are editable in Prompt Guardrails below; the parser protocol (stable IDs/order and JSON shape) remains app-owned.
            </Alert>
            <TextField
              size="small" multiline minRows={4}
              label="Translation instructions"
              value={draft.translation_instructions || ""}
              onChange={(e) => patch("translation_instructions", e.target.value)}
              helperText="Add domain/style preferences for this profile. Leave blank to use only the locked core prompt."
            />
            <TextField
              size="small" multiline minRows={4}
              label="Glossary"
              value={glossaryText}
              onChange={(e) => setGlossaryText(e.target.value)}
              placeholder={"产品 => sản phẩm\n免税 => miễn thuế"}
              helperText="One term per line: source => target"
            />
          </Stack>
        </AccordionDetails>
      </Accordion>

      <Accordion disableGutters>
        <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Voice & Timing (C7)</Typography>
            <Typography variant="caption" color="text.secondary">Independent rewrite model, voice defaults and timing policy</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            <TextField
              size="small" fullWidth label="C7 duration rewrite model"
              value={draft.rewrite_model || ""}
              onChange={(e) => patch("rewrite_model", e.target.value)}
              placeholder={globalModel}
              inputProps={{ list: "localization-model-suggestions" }}
              helperText="May use a smaller/faster model than C6. Blank = inherit global/default model."
            />
            <TextField
              size="small" fullWidth label="Compatible Base URL override (optional)"
              value={draft.rewrite_base_url || ""}
              onChange={(e) => patch("rewrite_base_url", e.target.value)}
              placeholder={settings?.ai_base_url || "inherit Settings endpoint"}
            />
            <Alert severity="info" variant="outlined">
              Rewrite instructions are additive. Semantic guardrails are editable in Prompt Guardrails below; the parser protocol (stable IDs/order and JSON shape) remains app-owned.
            </Alert>
            <TextField
              size="small" multiline minRows={4}
              label="C7 rewrite instructions"
              value={draft.rewrite_instructions || ""}
              onChange={(e) => patch("rewrite_instructions", e.target.value)}
              placeholder="Example: Prefer compact spoken Vietnamese; remove filler before facts; keep product features, quantities and causal meaning."
              helperText="Applied only to C7 duration rewrite for this profile. It does not replace the locked core prompt."
            />
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <FormControl size="small" fullWidth>
                <InputLabel>TTS provider</InputLabel>
                <Select value={draft.tts_provider || ""} label="TTS provider" onChange={(e) => patch("tts_provider", e.target.value || null)}>
                  <MenuItem value=""><em>Inherit TTS connection</em></MenuItem>
                  {["edge_tts", "openai_tts", "openai_compatible_tts", "azure_tts", "elevenlabs", "gpt_sovits"].map((value) => <MenuItem key={value} value={value}>{value}</MenuItem>)}
                </Select>
              </FormControl>
              <TextField size="small" fullWidth label="Voice ID" value={draft.tts_voice || ""} onChange={(e) => patch("tts_voice", e.target.value || null)} placeholder={settings?.preferred_tts_voice || "inherit language/provider default"} />
            </Stack>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <TextField size="small" type="number" fullWidth label="Preferred speed ceiling" value={draft.dub_speed_accept ?? 1.15} onChange={(e) => patch("dub_speed_accept", Number(e.target.value))} inputProps={{ step: 0.01, min: 1, max: 2 }} />
              <TextField size="small" type="number" fullWidth label="Hard speed ceiling" value={draft.dub_speed_max ?? 1.3} onChange={(e) => patch("dub_speed_max", Number(e.target.value))} inputProps={{ step: 0.01, min: 1, max: 2 }} />
              <TextField size="small" type="number" fullWidth label="Rewrite attempts" value={draft.dub_rewrite_attempts ?? 2} onChange={(e) => patch("dub_rewrite_attempts", Number(e.target.value))} inputProps={{ step: 1, min: 0, max: 5 }} />
            </Stack>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <FormControl size="small" fullWidth>
                <InputLabel>Video retime</InputLabel>
                <Select value={draft.video_retime_mode || "fallback"} label="Video retime" onChange={(e) => patch("video_retime_mode", e.target.value)}>
                  <MenuItem value="off">Off</MenuItem>
                  <MenuItem value="fallback">Fallback only</MenuItem>
                </Select>
              </FormControl>
              <TextField size="small" type="number" fullWidth label="Max video slowdown" value={draft.video_retime_max_slowdown ?? 1.12} onChange={(e) => patch("video_retime_max_slowdown", Number(e.target.value))} inputProps={{ step: 0.01, min: 1, max: 1.35 }} />
            </Stack>
          </Stack>
        </AccordionDetails>
      </Accordion>

      <Accordion disableGutters>
        <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Prompt Guardrails (Advanced)</Typography>
            <Typography variant="caption" color="text.secondary">Editable semantic rules per profile; stable IDs/order and JSON output protocol stay locked for parser safety</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            <Alert severity="warning" variant="outlined">
              These rules directly affect translation fidelity. Edit them when the policy is wrong, but keep both fields non-empty. Use Reset to restore the app-recommended defaults.
            </Alert>

            <Box>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }} sx={{ mb: 1 }}>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="subtitle2">C6 translation semantic guardrails</Typography>
                  <Typography variant="caption" color="text.secondary">Meaning/fidelity rules used before style, glossary and custom translation instructions.</Typography>
                </Box>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={!store.prompt_defaults?.translation_guardrails}
                  onClick={() => patch("translation_guardrails", store.prompt_defaults?.translation_guardrails || "")}
                >
                  Reset C6 default
                </Button>
              </Stack>
              <TextField
                size="small"
                fullWidth
                multiline
                minRows={7}
                label="C6 semantic guardrails"
                value={draft.translation_guardrails || ""}
                onChange={(e) => patch("translation_guardrails", e.target.value)}
                error={!draft.translation_guardrails?.trim()}
                helperText="Editable. Structural rules for stable IDs/order and JSON output are not part of this field and remain locked in code."
              />
            </Box>

            <Divider />

            <Box>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }} sx={{ mb: 1 }}>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="subtitle2">C7 duration-rewrite semantic guardrails</Typography>
                  <Typography variant="caption" color="text.secondary">Meaning-preservation rules applied before profile-specific compression/style instructions.</Typography>
                </Box>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={!store.prompt_defaults?.rewrite_guardrails}
                  onClick={() => patch("rewrite_guardrails", store.prompt_defaults?.rewrite_guardrails || "")}
                >
                  Reset C7 default
                </Button>
              </Stack>
              <TextField
                size="small"
                fullWidth
                multiline
                minRows={7}
                label="C7 semantic guardrails"
                value={draft.rewrite_guardrails || ""}
                onChange={(e) => patch("rewrite_guardrails", e.target.value)}
                error={!draft.rewrite_guardrails?.trim()}
                helperText="Editable. Stable IDs/order and JSON output remain locked so C7 cannot break the persisted pipeline contract."
              />
            </Box>
          </Stack>
        </AccordionDetails>
      </Accordion>

      <Accordion disableGutters>
        <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>ASR / Alignment</Typography>
            <Typography variant="caption" color="text.secondary">Reusable transcript quality and speaker-analysis defaults</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <FormControl size="small" fullWidth>
                <InputLabel>ASR quality</InputLabel>
                <Select value={draft.asr_quality || "best"} label="ASR quality" onChange={(e) => patch("asr_quality", e.target.value)}>
                  {["fast", "balanced", "accurate", "best"].map((v) => <MenuItem key={v} value={v}>{v}</MenuItem>)}
                </Select>
              </FormControl>
              <TextField size="small" fullWidth label="ASR model override" value={draft.asr_model || ""} onChange={(e) => patch("asr_model", e.target.value || null)} />
            </Stack>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <FormControl size="small" fullWidth>
                <InputLabel>Alignment</InputLabel>
                <Select value={draft.alignment_mode || "whisperx"} label="Alignment" onChange={(e) => patch("alignment_mode", e.target.value)}>
                  <MenuItem value="off">Off</MenuItem>
                  <MenuItem value="whisperx">WhisperX</MenuItem>
                </Select>
              </FormControl>
              <FormControl size="small" fullWidth>
                <InputLabel>Diarization</InputLabel>
                <Select value={draft.diarization_mode || "auto"} label="Diarization" onChange={(e) => patch("diarization_mode", e.target.value)}>
                  <MenuItem value="off">Off</MenuItem>
                  <MenuItem value="auto">Auto</MenuItem>
                  <MenuItem value="whisperx">WhisperX / pyannote</MenuItem>
                </Select>
              </FormControl>
              <TextField size="small" type="number" fullWidth label="Expected speakers" value={draft.diarization_speakers ?? ""} onChange={(e) => patch("diarization_speakers", e.target.value === "" ? null : Number(e.target.value))} inputProps={{ min: 1, max: 16 }} />
            </Stack>
            <FormControlLabel
              control={<Checkbox checked={!!draft.semantic_segmentation} onChange={(e) => patch("semantic_segmentation", e.target.checked)} />}
              label="AI semantic segmentation"
            />
          </Stack>
        </AccordionDetails>
      </Accordion>

      <Accordion disableGutters>
        <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Audio / Render / QC</Typography>
            <Typography variant="caption" color="text.secondary">Output defaults, not provider credentials</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <FormControl size="small" fullWidth>
                <InputLabel>Audio mode</InputLabel>
                <Select value={draft.audio_mode || "hq_clean"} label="Audio mode" onChange={(e) => patch("audio_mode", e.target.value)}>
                  <MenuItem value="hq_clean">HQ clean background</MenuItem>
                  <MenuItem value="fast_duck">Fast duck</MenuItem>
                </Select>
              </FormControl>
              <FormControl size="small" fullWidth>
                <InputLabel>Render profile</InputLabel>
                <Select value={draft.render_profile || "hq"} label="Render profile" onChange={(e) => patch("render_profile", e.target.value)}>
                  <MenuItem value="draft">Draft</MenuItem>
                  <MenuItem value="balanced">Balanced</MenuItem>
                  <MenuItem value="hq">HQ</MenuItem>
                </Select>
              </FormControl>
              <FormControl size="small" fullWidth>
                <InputLabel>QC profile</InputLabel>
                <Select value={draft.qc_profile || "strict"} label="QC profile" onChange={(e) => patch("qc_profile", e.target.value)}>
                  <MenuItem value="standard">Standard</MenuItem>
                  <MenuItem value="strict">Strict</MenuItem>
                </Select>
              </FormControl>
            </Stack>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <TextField size="small" fullWidth label="Caption style" value={draft.caption_style || "viral"} onChange={(e) => patch("caption_style", e.target.value)} />
              <FormControl size="small" fullWidth>
                <InputLabel>Target aspect</InputLabel>
                <Select value={draft.target_aspect || "9:16"} label="Target aspect" onChange={(e) => patch("target_aspect", e.target.value)}>
                  {["source", "9:16", "16:9", "1:1"].map((value) => <MenuItem key={value} value={value}>{value}</MenuItem>)}
                </Select>
              </FormControl>
            </Stack>
            <FormControlLabel
              control={<Checkbox checked={draft.preserve_background !== false} onChange={(e) => patch("preserve_background", e.target.checked)} />}
              label="Preserve separated background / music"
            />
          </Stack>
        </AccordionDetails>
      </Accordion>

      <Typography variant="caption" color="text.secondary">{modelSummary}</Typography>

      <Divider />
      <Stack direction="row" spacing={1} justifyContent="flex-end" flexWrap="wrap" useFlexGap>
        {!isDefault && <Button variant="text" onClick={makeDefault} disabled={busy}>Make default</Button>}
        {store.profiles.length > 1 && <Button color="error" variant="outlined" startIcon={<DeleteOutlineOutlinedIcon />} onClick={remove} disabled={busy}>Delete</Button>}
        <Button variant="contained" startIcon={<SaveOutlinedIcon />} onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save profile"}
        </Button>
      </Stack>
    </Stack>
  )
}
