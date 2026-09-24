import { useEffect, useMemo, useState } from "react"
import {
  Alert, Button, Checkbox, Divider, FormControl, FormControlLabel,
  InputLabel, MenuItem, Select, Stack, TextField, Typography,
} from "@mui/material"
import DeleteOutlineOutlinedIcon from "@mui/icons-material/DeleteOutlineOutlined"
import SaveOutlinedIcon from "@mui/icons-material/SaveOutlined"
import AddOutlinedIcon from "@mui/icons-material/AddOutlined"
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
  const [store, setStore] = useState({ profiles: [], default_profile_id: "" })
  const [selectedId, setSelectedId] = useState("")
  const [draft, setDraft] = useState(null)
  const [glossaryText, setGlossaryText] = useState("")
  const [busy, setBusy] = useState(false)

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

  const save = async () => {
    if (!draft) return
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
        C6 translation and C7 duration rewrite can use different model IDs on the same configured AI connection.
        Use a stronger model for translation and a smaller/faster model for rewrite if desired.
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

      <Divider />
      <Typography variant="subtitle2">Translation quality (C6)</Typography>

      <FormControl size="small" fullWidth>
        <InputLabel>Translation style</InputLabel>
        <Select value={draft.translation_preset || "natural_vietnamese"} label="Translation style" onChange={(e) => patch("translation_preset", e.target.value)}>
          {PRESETS.map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
        </Select>
      </FormControl>

      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <TextField
          size="small" fullWidth label="C6 translation model"
          value={draft.translation_model || ""}
          onChange={(e) => patch("translation_model", e.target.value)}
          placeholder={globalModel}
          helperText={`Blank = inherit Settings AI model. Provider: ${draft.translation_provider || globalProvider}`}
        />
        <TextField
          size="small" fullWidth label="C7 rewrite model"
          value={draft.rewrite_model || ""}
          onChange={(e) => patch("rewrite_model", e.target.value)}
          placeholder={globalModel}
          helperText="Shortening/timing model. Blank = inherit global model."
        />
      </Stack>

      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <TextField
          size="small" fullWidth label="C6 compatible Base URL (optional)"
          value={draft.translation_base_url || ""}
          onChange={(e) => patch("translation_base_url", e.target.value)}
          placeholder={settings?.ai_base_url || "inherit Settings endpoint"}
        />
        <TextField
          size="small" fullWidth label="C7 rewrite Base URL (optional)"
          value={draft.rewrite_base_url || ""}
          onChange={(e) => patch("rewrite_base_url", e.target.value)}
          placeholder={settings?.ai_base_url || "inherit Settings endpoint"}
        />
      </Stack>

      <Typography variant="caption" color="text.secondary">{modelSummary}</Typography>

      <TextField
        size="small" multiline minRows={4}
        label="Translation instructions"
        value={draft.translation_instructions || ""}
        onChange={(e) => patch("translation_instructions", e.target.value)}
        helperText="Extra style/domain rules. Fidelity rules remain non-negotiable."
      />

      <TextField
        size="small" multiline minRows={4}
        label="Glossary"
        value={glossaryText}
        onChange={(e) => setGlossaryText(e.target.value)}
        placeholder={"产品 => sản phẩm\n免税 => miễn thuế"}
        helperText="One term per line: source => target"
      />

      <Divider />
      <Typography variant="subtitle2">ASR / dubbing policy</Typography>

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
        <TextField size="small" type="number" fullWidth label="Preferred speed ceiling" value={draft.dub_speed_accept ?? 1.15} onChange={(e) => patch("dub_speed_accept", Number(e.target.value))} inputProps={{ step: 0.01, min: 1, max: 2 }} />
        <TextField size="small" type="number" fullWidth label="Hard speed ceiling" value={draft.dub_speed_max ?? 1.3} onChange={(e) => patch("dub_speed_max", Number(e.target.value))} inputProps={{ step: 0.01, min: 1, max: 2 }} />
        <TextField size="small" type="number" fullWidth label="Rewrite attempts" value={draft.dub_rewrite_attempts ?? 2} onChange={(e) => patch("dub_rewrite_attempts", Number(e.target.value))} inputProps={{ step: 1, min: 0, max: 5 }} />
      </Stack>

      <FormControlLabel
        control={<Checkbox checked={!!draft.semantic_segmentation} onChange={(e) => patch("semantic_segmentation", e.target.checked)} />}
        label="AI semantic segmentation"
      />

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
