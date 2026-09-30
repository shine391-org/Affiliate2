import { useEffect, useMemo, useState } from "react"
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Autocomplete, Box, Button, Checkbox,
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
  const [discovering, setDiscovering] = useState("")
  const [discoveredByConnection, setDiscoveredByConnection] = useState({})

  const aiConnections = settings?.ai_connections || {}
  const activeAiId = settings?.ai_connection_id || Object.keys(aiConnections)[0] || ""
  const ttsConnections = settings?.tts_connections || {}
  const activeTtsId = settings?.tts_connection_id || Object.keys(ttsConnections)[0] || ""

  const legacyAiConnectionId = (provider) => {
    if (!provider) return activeAiId
    if (aiConnections[provider]) return provider
    const matches = Object.entries(aiConnections)
      .filter(([, cfg]) => cfg?.provider === provider)
      .map(([id]) => id)
    if (matches.length === 1) return matches[0]
    if (provider === settings?.ai_provider && activeAiId) return activeAiId
    return provider
  }

  const normalizeProfile = (profile) => {
    if (!profile) return null
    const next = clone(profile)
    if (!Array.isArray(next.translation_routes) || next.translation_routes.length === 0) {
      const primary = legacyAiConnectionId(next.translation_provider)
      const fallback = next.translation_fallback_provider
        ? legacyAiConnectionId(next.translation_fallback_provider)
        : ""
      next.translation_routes = primary ? [{
        connection_id: primary,
        model: next.translation_model || aiConnections[primary]?.model || null,
        base_url: next.translation_base_url || null,
      }] : []
      if (fallback) next.translation_routes.push({
        connection_id: fallback,
        model: next.translation_fallback_model || aiConnections[fallback]?.model || null,
        base_url: next.translation_fallback_base_url || null,
      })
    }
    if (!Array.isArray(next.rewrite_routes) || next.rewrite_routes.length === 0) {
      const primary = legacyAiConnectionId(next.rewrite_provider)
      const fallback = next.rewrite_fallback_provider
        ? legacyAiConnectionId(next.rewrite_fallback_provider)
        : ""
      next.rewrite_routes = primary ? [{
        connection_id: primary,
        model: next.rewrite_model || aiConnections[primary]?.model || null,
        base_url: next.rewrite_base_url || null,
      }] : []
      if (fallback) next.rewrite_routes.push({
        connection_id: fallback,
        model: next.rewrite_fallback_model || aiConnections[fallback]?.model || null,
        base_url: next.rewrite_fallback_base_url || null,
      })
    }
    if (!next.tts_connection_id && next.tts_provider) {
      const matches = Object.entries(ttsConnections)
        .filter(([, cfg]) => cfg?.provider === next.tts_provider)
        .map(([id]) => id)
      if (matches.length === 1) next.tts_connection_id = matches[0]
      else if (next.tts_provider === settings?.tts_provider && activeTtsId) next.tts_connection_id = activeTtsId
    }
    return next
  }

  const load = async (preferId = "") => {
    const { data } = await http.get("/api/settings/localization-profiles")
    setStore(data)
    const nextId = preferId || selectedId || data.default_profile_id || data.profiles?.[0]?.id || ""
    setSelectedId(nextId)
    const profile = data.profiles?.find((p) => p.id === nextId) || data.profiles?.[0] || null
    const normalized = normalizeProfile(profile)
    setDraft(normalized)
    setGlossaryText(glossaryToText(profile?.glossary))
  }

  useEffect(() => { load().catch(() => {}) }, [])

  useEffect(() => {
    const profile = store.profiles?.find((p) => p.id === selectedId)
    if (!profile) return
    setDraft(normalizeProfile(profile))
    setGlossaryText(glossaryToText(profile.glossary))
  }, [selectedId, settings?.ai_connections, settings?.tts_connections])

  const isDefault = selectedId === store.default_profile_id
  const patch = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }))

  const connectionLabel = (connectionId) => {
    const cfg = aiConnections[connectionId]
    return cfg ? `${cfg.label || connectionId} · ${cfg.provider}` : connectionId
  }
  const routeModels = (connectionId) => [
    ...new Set([
      aiConnections[connectionId]?.model,
      ...(discoveredByConnection[connectionId] || []),
    ].filter(Boolean)),
  ]

  const updateRoute = (stage, index, values) => setDraft((prev) => {
    const key = `${stage}_routes`
    const routes = [...(prev?.[key] || [])]
    const current = routes[index] || {}
    routes[index] = { ...current, ...values }
    return { ...prev, [key]: routes }
  })

  const addRoute = (stage) => setDraft((prev) => {
    const key = `${stage}_routes`
    const routes = [...(prev?.[key] || [])]
    const fallbackId = Object.keys(aiConnections).find(
      (id) => !routes.some((route) => route.connection_id === id)
    ) || activeAiId
    if (!fallbackId) return prev
    routes.push({
      connection_id: fallbackId,
      model: aiConnections[fallbackId]?.model || null,
      base_url: null,
    })
    return { ...prev, [key]: routes }
  })

  const removeRoute = (stage, index) => setDraft((prev) => {
    const key = `${stage}_routes`
    const routes = [...(prev?.[key] || [])]
    if (routes.length <= 1) return prev
    routes.splice(index, 1)
    return { ...prev, [key]: routes }
  })

  const moveRoute = (stage, index, direction) => setDraft((prev) => {
    const key = `${stage}_routes`
    const routes = [...(prev?.[key] || [])]
    const target = index + direction
    if (target < 0 || target >= routes.length) return prev
    ;[routes[index], routes[target]] = [routes[target], routes[index]]
    return { ...prev, [key]: routes }
  })

  const modelSummary = useMemo(() => {
    if (!draft) return ""
    const c6 = (draft.translation_routes || []).map((r) =>
      `${connectionLabel(r.connection_id)} / ${r.model || aiConnections[r.connection_id]?.model || "default"}`
    ).join(" → ")
    const c7 = (draft.rewrite_routes || []).map((r) =>
      `${connectionLabel(r.connection_id)} / ${r.model || aiConnections[r.connection_id]?.model || "default"}`
    ).join(" → ")
    return `C6: ${c6 || "chưa cấu hình"} · C7: ${c7 || "chưa cấu hình"}`
  }, [draft, settings?.ai_connections])

  const currentTtsProvider = settings?.tts_provider || "edge_tts"
  const pinnedTtsConnection = draft?.tts_connection_id || null
  const pinnedTtsConfig = pinnedTtsConnection ? ttsConnections[pinnedTtsConnection] : null
  const ttsConnectionMissing = Boolean(pinnedTtsConnection && !pinnedTtsConfig)
  const ttsVoicePlaceholder = pinnedTtsConfig?.voice
    || settings?.preferred_tts_voice
    || "kế thừa giọng mặc định của connection"

  const adoptCurrentTts = () => setDraft((prev) => {
    const connectionChanged = Boolean(
      prev?.tts_connection_id && prev.tts_connection_id !== activeTtsId
    )
    return {
      ...prev,
      tts_connection_id: activeTtsId || null,
      tts_provider: currentTtsProvider,
      tts_model: settings?.tts_model || null,
      tts_voice: settings?.preferred_tts_voice || null,
      tts_base_url: null,
      tts_region: null,
      ...(connectionChanged ? {
        speaker_voices: {},
        speaker_reference_audio: {},
        tts_reference_audio: null,
        tts_reference_text: null,
        tts_reference_language: null,
      } : {}),
    }
  })

  const discoverModels = async (connectionId) => {
    const cfg = aiConnections[connectionId]
    if (!cfg) {
      showSnackbar("Connection AI không tồn tại", "warning")
      return
    }
    if (cfg.provider === "anthropic") {
      showSnackbar("Connection này không hỗ trợ dò tìm mô hình", "warning")
      return
    }
    setDiscovering(connectionId)
    try {
      const { data } = await http.post("/api/settings/ai/models", {
        connection_id: connectionId,
      })
      const models = data?.models || []
      setDiscoveredByConnection((prev) => ({ ...prev, [connectionId]: models }))
      showSnackbar(
        models.length ? `Tìm thấy ${models.length} mô hình trên ${cfg.label || connectionId}` : "Không có mô hình nào được trả về",
        models.length ? "success" : "warning",
      )
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Dò tìm mô hình thất bại", "error")
    } finally {
      setDiscovering("")
    }
  }

  const renderRouteChain = (stage, title) => {
    const routes = draft?.[`${stage}_routes`] || []
    return (
      <Stack spacing={1.25}>
        <Typography variant="subtitle2">{title}</Typography>
        {routes.map((route, index) => {
          const cfg = aiConnections[route.connection_id] || {}
          const models = routeModels(route.connection_id)
          return (
            <Box key={`${stage}-${index}`} sx={{ p: 1.25, border: 1, borderColor: "divider", borderRadius: 1.5 }}>
              <Stack direction={{ xs: "column", md: "row" }} spacing={1}>
                <FormControl size="small" fullWidth>
                  <InputLabel>{index === 0 ? "AI chính" : `Fallback ${index}`}</InputLabel>
                  <Select
                    value={route.connection_id || ""}
                    label={index === 0 ? "AI chính" : `Fallback ${index}`}
                    onChange={(e) => {
                      const id = e.target.value
                      updateRoute(stage, index, {
                        connection_id: id,
                        model: aiConnections[id]?.model || null,
                        base_url: null,
                      })
                    }}
                  >
                    {Object.entries(aiConnections).map(([id, item]) => (
                      <MenuItem key={id} value={id}>
                        {item.label || id} · {item.provider}{id === activeAiId ? " · ACTIVE" : ""}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <Autocomplete
                  freeSolo
                  size="small"
                  fullWidth
                  options={models}
                  value={route.model || ""}
                  onInputChange={(_, value) => updateRoute(stage, index, { model: value || null })}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      label="Model"
                      placeholder={cfg.model || "model mặc định của connection"}
                    />
                  )}
                />
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<RefreshOutlinedIcon />}
                  onClick={() => discoverModels(route.connection_id)}
                  disabled={!route.connection_id || discovering === route.connection_id}
                >
                  {discovering === route.connection_id ? "Đang dò…" : "Models"}
                </Button>
              </Stack>
              <Stack direction="row" spacing={0.75} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
                <Button size="small" onClick={() => moveRoute(stage, index, -1)} disabled={index === 0}>Lên</Button>
                <Button size="small" onClick={() => moveRoute(stage, index, 1)} disabled={index === routes.length - 1}>Xuống</Button>
                <Button size="small" color="error" onClick={() => removeRoute(stage, index)} disabled={routes.length <= 1}>Bỏ route</Button>
                <Typography variant="caption" color="text.secondary" sx={{ alignSelf: "center" }}>
                  {cfg.base_url || "endpoint mặc định"}{cfg.api_key_set ? " · key đã lưu" : ""}
                </Typography>
              </Stack>
            </Box>
          )
        })}
        <Button size="small" variant="outlined" startIcon={<AddOutlinedIcon />} onClick={() => addRoute(stage)} disabled={!Object.keys(aiConnections).length}>
          Thêm fallback
        </Button>
      </Stack>
    )
  }


  const save = async () => {
    if (!draft) return
    if (!draft.translation_guardrails?.trim() || !draft.rewrite_guardrails?.trim()) {
      showSnackbar("Guardrail ngữ nghĩa C6 và C7 không được để trống", "error")
      return
    }
    setBusy(true)
    try {
      const translationRoutes = (draft.translation_routes || [])
        .filter((route) => route?.connection_id)
        .map((route) => ({
          connection_id: route.connection_id,
          model: route.model?.trim() || null,
          base_url: route.base_url?.trim() || null,
        }))
      const rewriteRoutes = (draft.rewrite_routes || [])
        .filter((route) => route?.connection_id)
        .map((route) => ({
          connection_id: route.connection_id,
          model: route.model?.trim() || null,
          base_url: route.base_url?.trim() || null,
        }))
      if (!translationRoutes.length || !rewriteRoutes.length) {
        throw new Error("C6 và C7 cần ít nhất một AI connection route")
      }
      const payload = {
        ...draft,
        translation_routes: translationRoutes,
        rewrite_routes: rewriteRoutes,
        // Keep the first two legacy slots synchronized for old manifests/tools.
        translation_provider: translationRoutes[0]?.connection_id || null,
        translation_model: translationRoutes[0]?.model || null,
        translation_base_url: translationRoutes[0]?.base_url || null,
        translation_fallback_provider: translationRoutes[1]?.connection_id || null,
        translation_fallback_model: translationRoutes[1]?.model || null,
        translation_fallback_base_url: translationRoutes[1]?.base_url || null,
        rewrite_provider: rewriteRoutes[0]?.connection_id || null,
        rewrite_model: rewriteRoutes[0]?.model || null,
        rewrite_base_url: rewriteRoutes[0]?.base_url || null,
        rewrite_fallback_provider: rewriteRoutes[1]?.connection_id || null,
        rewrite_fallback_model: rewriteRoutes[1]?.model || null,
        rewrite_fallback_base_url: rewriteRoutes[1]?.base_url || null,
        tts_connection_id: draft.tts_connection_id || null,
        translation_instructions: draft.translation_instructions || "",
        rewrite_instructions: draft.rewrite_instructions || "",
        translation_guardrails: draft.translation_guardrails.trim(),
        rewrite_guardrails: draft.rewrite_guardrails.trim(),
        glossary: textToGlossary(glossaryText),
      }
      const { data } = await http.post("/api/settings/localization-profiles", payload)
      setStore(data)
      setSelectedId(payload.id)
      setDraft(normalizeProfile(data.profiles.find((p) => p.id === payload.id) || payload))
      setGlossaryText(glossaryToText(payload.glossary))
      showSnackbar("Đã lưu hồ sơ bản địa hóa", "success")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || err.message || "Lưu hồ sơ thất bại", "error")
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
      showSnackbar("Đã cập nhật hồ sơ bản địa hóa mặc định", "success")
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
      translation_fallback_provider: null,
      translation_fallback_model: null,
      translation_fallback_base_url: null,
      translation_routes: activeAiId ? [{
        connection_id: activeAiId,
        model: aiConnections[activeAiId]?.model || null,
        base_url: null,
      }] : [],
      rewrite_provider: null,
      rewrite_model: null,
      rewrite_base_url: null,
      rewrite_fallback_provider: null,
      rewrite_fallback_model: null,
      rewrite_fallback_base_url: null,
      rewrite_routes: activeAiId ? [{
        connection_id: activeAiId,
        model: aiConnections[activeAiId]?.model || null,
        base_url: null,
      }] : [],
      tts_connection_id: activeTtsId || null,
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
      setDraft(normalizeProfile(profile))
      setGlossaryText(glossaryToText(profile?.glossary))
      showSnackbar("Đã xóa hồ sơ bản địa hóa", "info")
    } finally {
      setBusy(false)
    }
  }

  if (!draft) return <Typography variant="body2" color="text.secondary">Đang tải hồ sơ bản địa hóa…</Typography>

  return (
    <Stack spacing={2}>
      <Alert severity="info" variant="outlined">
        <Typography variant="body2" sx={{ fontWeight: 700, mb: 0.5 }}>
          Hồ sơ bản địa hóa ghép 4 lớp độc lập
        </Typography>
        <Typography variant="caption" color="text.secondary">
          C6 = AI dịch nội dung · C7 = AI viết lại cho vừa thời lượng · TTS = tạo giọng đọc · Audio/Render/QC = xử lý và kiểm tra đầu ra. API key/endpoint dùng chung vẫn thuộc Kết nối AI hoặc TTS; hồ sơ này chỉ lưu override và chính sách sản xuất.
        </Typography>
      </Alert>

      {pinnedTtsConnection && pinnedTtsConfig && pinnedTtsConnection !== activeTtsId && (
        <Alert severity="info" variant="outlined">
          Hồ sơ đang ghim TTS connection <strong>{pinnedTtsConfig.label || pinnedTtsConnection}</strong> ({pinnedTtsConfig.provider}).
          Đây là cấu hình hợp lệ và không phụ thuộc connection TTS đang active.
        </Alert>
      )}
      {ttsConnectionMissing && (
        <Alert
          severity="warning"
          action={<Button color="inherit" size="small" onClick={adoptCurrentTts}>Dùng TTS hiện tại cho hồ sơ</Button>}
        >
          TTS connection <strong>{pinnedTtsConnection}</strong> không còn trong danh sách đang dùng.
          Có thể là connection cũ đã archive; chọn connection hiện tại trước khi tạo job mới.
        </Alert>
      )}

      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <FormControl size="small" fullWidth>
          <InputLabel>Hồ sơ</InputLabel>
          <Select value={selectedId} label="Hồ sơ" onChange={(e) => setSelectedId(e.target.value)}>
            {store.profiles.map((p) => (
              <MenuItem key={p.id} value={p.id}>
                {p.name}{p.id === store.default_profile_id ? " · mặc định" : ""}
              </MenuItem>
            ))}
            {!store.profiles.some((p) => p.id === selectedId) && draft && (
              <MenuItem value={selectedId}>{draft.name} · chưa lưu</MenuItem>
            )}
          </Select>
        </FormControl>
        <Button startIcon={<AddOutlinedIcon />} variant="outlined" onClick={addProfile} disabled={busy}>Mới</Button>
      </Stack>

      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
        <TextField size="small" fullWidth label="ID hồ sơ" value={draft.id || ""} onChange={(e) => patch("id", e.target.value)} />
        <TextField size="small" fullWidth label="Tên" value={draft.name || ""} onChange={(e) => patch("name", e.target.value)} />
      </Stack>

      <Box sx={{ p: 1.5, border: 1, borderColor: "divider", borderRadius: 2 }}>
        <Typography variant="subtitle2">Connection Registry</Typography>
        <Typography variant="caption" color="text.secondary">
          {Object.keys(aiConnections).length} AI connection · {Object.keys(ttsConnections).length} TTS connection.
          C6/C7 chọn connection theo thứ tự fallback bên dưới; mỗi route tự dò model từ đúng endpoint.
        </Typography>
      </Box>

      <Accordion defaultExpanded disableGutters>
        <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Bản dịch (C6)</Typography>
            <Typography variant="caption" color="text.secondary">Mô hình dịch chính xác, phong cách, hướng dẫn và bảng thuật ngữ</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            {renderRouteChain("translation", "Chuỗi AI C6 / Semantic")}
            <Alert severity="info" variant="outlined">
              Chạy theo đúng thứ tự route. Khi một route lỗi và fallback thành công, route đó được giữ sticky cho phần còn lại của stage thay vì quay lại endpoint đang chết. Semantic dùng cùng chuỗi; hết toàn bộ budget thì chuyển deterministic.
            </Alert>
            <FormControlLabel
              control={
                <Checkbox
                  checked={draft.semantic_segmentation !== false}
                  onChange={(e) => patch("semantic_segmentation", e.target.checked)}
                />
              }
              label="Dùng AI để tinh chỉnh ranh giới câu/ngữ nghĩa (tắt = phân đoạn nhanh bằng timestamp/punctuation/speaker)"
            />
            <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
              <TextField
                size="small" type="number" fullWidth label="Semantic timeout / lần (giây)"
                value={draft.semantic_ai_attempt_timeout_s ?? 25}
                onChange={(e) => patch("semantic_ai_attempt_timeout_s", Number(e.target.value))}
                inputProps={{ min: 5, max: 120, step: 5 }}
              />
              <TextField
                size="small" type="number" fullWidth label="Semantic tổng ngân sách (giây)"
                value={draft.semantic_ai_total_timeout_s ?? 60}
                onChange={(e) => patch("semantic_ai_total_timeout_s", Number(e.target.value))}
                inputProps={{ min: 10, max: 240, step: 5 }}
              />
              <TextField
                size="small" type="number" fullWidth label="Semantic số lần thử/provider"
                value={draft.semantic_ai_retry_attempts ?? 2}
                onChange={(e) => patch("semantic_ai_retry_attempts", Number(e.target.value))}
                inputProps={{ min: 1, max: 3, step: 1 }}
              />
            </Stack>
            <FormControl size="small" fullWidth>
              <InputLabel>Phong cách dịch</InputLabel>
              <Select value={draft.translation_preset || "natural_vietnamese"} label="Phong cách dịch" onChange={(e) => patch("translation_preset", e.target.value)}>
                {PRESETS.map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
              </Select>
            </FormControl>
            <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
              <TextField
                size="small" type="number" fullWidth label="Dịch timeout / lần (giây)"
                value={draft.translation_attempt_timeout_s ?? 75}
                onChange={(e) => patch("translation_attempt_timeout_s", Number(e.target.value))}
                inputProps={{ min: 10, max: 300, step: 5 }}
              />
              <TextField
                size="small" type="number" fullWidth label="Dịch tổng ngân sách/batch (giây)"
                value={draft.translation_total_timeout_s ?? 180}
                onChange={(e) => patch("translation_total_timeout_s", Number(e.target.value))}
                inputProps={{ min: 20, max: 900, step: 10 }}
              />
              <TextField
                size="small" type="number" fullWidth label="Dịch số lần thử/provider"
                value={draft.translation_retry_attempts ?? 3}
                onChange={(e) => patch("translation_retry_attempts", Number(e.target.value))}
                inputProps={{ min: 1, max: 5, step: 1 }}
              />
            </Stack>
            <Alert severity="info" variant="outlined">
              Hướng dẫn dịch được bổ sung thêm. Quy tắc bảo vệ ngữ nghĩa có thể chỉnh ở phần bên dưới; giao thức parser (ID/thứ tự ổn định và cấu trúc JSON) vẫn do ứng dụng quản lý.
            </Alert>
            <TextField
              size="small" multiline minRows={4}
              label="Hướng dẫn dịch"
              value={draft.translation_instructions || ""}
              onChange={(e) => patch("translation_instructions", e.target.value)}
              helperText="Thêm ưu tiên về lĩnh vực/phong cách cho hồ sơ. Để trống để chỉ dùng guardrail ngữ nghĩa + preset."
            />
            <TextField
              size="small" multiline minRows={4}
              label="Bảng thuật ngữ"
              value={glossaryText}
              onChange={(e) => setGlossaryText(e.target.value)}
              placeholder={"产品 => sản phẩm\n免税 => miễn thuế"}
              helperText="Mỗi dòng một thuật ngữ: nguồn => đích"
            />
          </Stack>
        </AccordionDetails>
      </Accordion>

      <Accordion disableGutters>
        <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Viết lại thời lượng (C7) & TTS</Typography>
            <Typography variant="caption" color="text.secondary">C7 quyết định câu chữ/độ dài; TTS là bước sau, chỉ tạo audio từ câu đã chốt</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            {renderRouteChain("rewrite", "Chuỗi AI C7")}
            <Alert severity="info" variant="outlined">
              C7 cũng hỗ trợ N route theo thứ tự và sticky failover. Nếu toàn bộ AI rewrite lỗi/hết ngân sách, pipeline chuyển sang timing fit/video slowdown bounded thay vì treo.
            </Alert>
            <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
              <TextField
                size="small" type="number" fullWidth label="C7 timeout / lần (giây)"
                value={draft.rewrite_attempt_timeout_s ?? 45}
                onChange={(e) => patch("rewrite_attempt_timeout_s", Number(e.target.value))}
                inputProps={{ min: 10, max: 180, step: 5 }}
              />
              <TextField
                size="small" type="number" fullWidth label="C7 tổng ngân sách (giây)"
                value={draft.rewrite_total_timeout_s ?? 90}
                onChange={(e) => patch("rewrite_total_timeout_s", Number(e.target.value))}
                inputProps={{ min: 20, max: 360, step: 5 }}
              />
              <TextField
                size="small" type="number" fullWidth label="C7 số lần thử/provider"
                value={draft.rewrite_retry_attempts ?? 3}
                onChange={(e) => patch("rewrite_retry_attempts", Number(e.target.value))}
                inputProps={{ min: 1, max: 4, step: 1 }}
              />
            </Stack>
            <Alert severity="info" variant="outlined">
              Nếu AI viết lại chính và fallback đều lỗi/hết thời gian, C7 không treo job: pipeline tiếp tục bằng timing fit/video slowdown bounded và chỉ fail nếu cuối cùng vẫn không thể fit.
            </Alert>
            <Alert severity="info" variant="outlined">
              Hướng dẫn viết lại được bổ sung thêm. Quy tắc bảo vệ ngữ nghĩa có thể chỉnh ở phần bên dưới; giao thức parser (ID/thứ tự ổn định và cấu trúc JSON) vẫn do ứng dụng quản lý.
            </Alert>
            <TextField
              size="small" multiline minRows={4}
              label="Hướng dẫn viết lại C7"
              value={draft.rewrite_instructions || ""}
              onChange={(e) => patch("rewrite_instructions", e.target.value)}
              placeholder="Ví dụ: Ưu tiên tiếng Việt nói gọn; bỏ từ đệm trước dữ kiện; giữ nguyên tính năng sản phẩm, số lượng và quan hệ nhân quả."
              helperText="Chỉ áp dụng cho bước viết lại thời lượng C7 của hồ sơ này, sau guardrail ngữ nghĩa có thể chỉnh sửa."
            />
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <FormControl size="small" fullWidth>
                <InputLabel>Ghi đè TTS cho hồ sơ</InputLabel>
                <Select
                  value={draft.tts_connection_id || ""}
                  label="Ghi đè TTS cho hồ sơ"
                  helperText="Kết nối TTS cho hồ sơ: ghim một kết nối TTS cụ thể cho hồ sơ này. Để trống = kế thừa kết nối TTS đang active. Hồ sơ ghim connection ID (không phụ thuộc connection TTS đang active) nên job cũ luôn resolve đúng credential/giọng sau khi đổi kết nối."
                  onChange={(e) => {
                    const id = e.target.value
                    const cfg = id ? ttsConnections[id] : null
                    const nextId = id || activeTtsId || ""
                    const nextCfg = cfg || ttsConnections[nextId] || null
                    setDraft((prev) => {
                      const changed = Boolean(prev?.tts_connection_id && prev.tts_connection_id !== nextId)
                      return {
                        ...prev,
                        tts_connection_id: nextId || null,
                        tts_provider: nextCfg?.provider || settings?.tts_provider || null,
                        tts_model: nextCfg?.model || settings?.tts_model || null,
                        tts_voice: nextCfg?.voice || settings?.preferred_tts_voice || null,
                        tts_base_url: null,
                        tts_region: null,
                        ...(changed ? {
                          speaker_voices: {},
                          speaker_reference_audio: {},
                          tts_reference_audio: null,
                          tts_reference_text: null,
                          tts_reference_language: null,
                        } : {}),
                      }
                    })
                  }}
                >
                  <MenuItem value=""><em>Kế thừa TTS active ({activeTtsId || "chưa có"})</em></MenuItem>
                  {Object.entries(ttsConnections).map(([id, cfg]) => (
                    <MenuItem key={id} value={id}>
                      {cfg.label || id} · {cfg.provider}{id === activeTtsId ? " · ACTIVE" : ""}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <TextField
                size="small"
                fullWidth
                label="ID giọng đọc override"
                value={draft.tts_voice || ""}
                onChange={(e) => patch("tts_voice", e.target.value || null)}
                placeholder={ttsVoicePlaceholder}
              />
            </Stack>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <TextField size="small" type="number" fullWidth label="Giới hạn tốc độ ưu tiên" value={draft.dub_speed_accept ?? 1.15} onChange={(e) => patch("dub_speed_accept", Number(e.target.value))} inputProps={{ step: 0.01, min: 1, max: 2 }} />
              <TextField size="small" type="number" fullWidth label="Giới hạn tốc độ tối đa" value={draft.dub_speed_max ?? 1.3} onChange={(e) => patch("dub_speed_max", Number(e.target.value))} inputProps={{ step: 0.01, min: 1, max: 2 }} />
              <TextField size="small" type="number" fullWidth label="Số lần viết lại" value={draft.dub_rewrite_attempts ?? 2} onChange={(e) => patch("dub_rewrite_attempts", Number(e.target.value))} inputProps={{ step: 1, min: 0, max: 5 }} />
            </Stack>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <FormControl size="small" fullWidth>
                <InputLabel>Điều chỉnh tốc độ video</InputLabel>
                <Select value={draft.video_retime_mode || "fallback"} label="Điều chỉnh tốc độ video" onChange={(e) => patch("video_retime_mode", e.target.value)}>
                  <MenuItem value="off">Tắt</MenuItem>
                  <MenuItem value="fallback">Chỉ dự phòng</MenuItem>
                </Select>
              </FormControl>
              <TextField size="small" type="number" fullWidth label="Mức làm chậm video tối đa" value={draft.video_retime_max_slowdown ?? 1.12} onChange={(e) => patch("video_retime_max_slowdown", Number(e.target.value))} inputProps={{ step: 0.01, min: 1, max: 1.35 }} />
            </Stack>
          </Stack>
        </AccordionDetails>
      </Accordion>

      <Accordion disableGutters>
        <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Quy tắc bảo vệ prompt (Nâng cao)</Typography>
            <Typography variant="caption" color="text.secondary">Có thể chỉnh quy tắc ngữ nghĩa theo từng hồ sơ; ID/thứ tự ổn định và giao thức đầu ra JSON vẫn bị khóa để bảo vệ bộ phân tích</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            <Alert severity="warning" variant="outlined">
              Các quy tắc này ảnh hưởng trực tiếp đến độ trung thành của bản dịch. Chỉ sửa khi chính sách chưa đúng và luôn giữ cả hai trường có nội dung. Dùng Đặt lại để khôi phục mặc định do ứng dụng đề xuất.
            </Alert>

            <Box>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }} sx={{ mb: 1 }}>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="subtitle2">Quy tắc bảo vệ ngữ nghĩa bản dịch C6</Typography>
                  <Typography variant="caption" color="text.secondary">Quy tắc về ý nghĩa/độ trung thành được áp dụng trước phong cách, bảng thuật ngữ và hướng dẫn dịch tùy chỉnh.</Typography>
                </Box>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={!store.prompt_defaults?.translation_guardrails}
                  onClick={() => patch("translation_guardrails", store.prompt_defaults?.translation_guardrails || "")}
                >
                  Đặt lại mặc định C6
                </Button>
              </Stack>
              <TextField
                size="small"
                fullWidth
                multiline
                minRows={7}
                label="Quy tắc ngữ nghĩa C6"
                value={draft.translation_guardrails || ""}
                onChange={(e) => patch("translation_guardrails", e.target.value)}
                error={!draft.translation_guardrails?.trim()}
                helperText="Có thể chỉnh sửa. Quy tắc cấu trúc cho ID/thứ tự ổn định và đầu ra JSON không nằm trong trường này và vẫn bị khóa trong mã."
              />
            </Box>

            <Divider />

            <Box>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }} sx={{ mb: 1 }}>
                <Box sx={{ flex: 1 }}>
                  <Typography variant="subtitle2">Quy tắc bảo vệ ngữ nghĩa viết lại thời lượng C7</Typography>
                  <Typography variant="caption" color="text.secondary">Quy tắc giữ nguyên ý nghĩa được áp dụng trước hướng dẫn nén/phong cách riêng của hồ sơ.</Typography>
                </Box>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={!store.prompt_defaults?.rewrite_guardrails}
                  onClick={() => patch("rewrite_guardrails", store.prompt_defaults?.rewrite_guardrails || "")}
                >
                  Đặt lại mặc định C7
                </Button>
              </Stack>
              <TextField
                size="small"
                fullWidth
                multiline
                minRows={7}
                label="Quy tắc ngữ nghĩa C7"
                value={draft.rewrite_guardrails || ""}
                onChange={(e) => patch("rewrite_guardrails", e.target.value)}
                error={!draft.rewrite_guardrails?.trim()}
                helperText="Có thể chỉnh sửa. ID/thứ tự ổn định và đầu ra JSON vẫn bị khóa để C7 không phá vỡ hợp đồng pipeline đã lưu."
              />
            </Box>
          </Stack>
        </AccordionDetails>
      </Accordion>

      <Accordion disableGutters>
        <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>ASR / Căn chỉnh</Typography>
            <Typography variant="caption" color="text.secondary">Mặc định dùng lại cho chất lượng bản chép lời và phân tích người nói</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <FormControl size="small" fullWidth>
                <InputLabel>Chất lượng ASR</InputLabel>
                <Select value={draft.asr_quality || "best"} label="Chất lượng ASR" onChange={(e) => patch("asr_quality", e.target.value)}>
                  {["fast", "balanced", "accurate", "best"].map((v) => <MenuItem key={v} value={v}>{v}</MenuItem>)}
                </Select>
              </FormControl>
              <TextField size="small" fullWidth label="Ghi đè mô hình ASR" value={draft.asr_model || ""} onChange={(e) => patch("asr_model", e.target.value || null)} />
            </Stack>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <FormControl size="small" fullWidth>
                <InputLabel>Căn chỉnh</InputLabel>
                <Select value={draft.alignment_mode || "whisperx"} label="Căn chỉnh" onChange={(e) => patch("alignment_mode", e.target.value)}>
                  <MenuItem value="off">Tắt</MenuItem>
                  <MenuItem value="whisperx">WhisperX</MenuItem>
                </Select>
              </FormControl>
              <FormControl size="small" fullWidth>
                <InputLabel>Tách người nói</InputLabel>
                <Select value={draft.diarization_mode || "auto"} label="Tách người nói" onChange={(e) => patch("diarization_mode", e.target.value)}>
                  <MenuItem value="off">Tắt</MenuItem>
                  <MenuItem value="auto">Tự động</MenuItem>
                  <MenuItem value="whisperx">WhisperX / pyannote</MenuItem>
                </Select>
              </FormControl>
              <TextField size="small" type="number" fullWidth label="Số người nói dự kiến" value={draft.diarization_speakers ?? ""} onChange={(e) => patch("diarization_speakers", e.target.value === "" ? null : Number(e.target.value))} inputProps={{ min: 1, max: 16 }} />
            </Stack>
          </Stack>
        </AccordionDetails>
      </Accordion>

      <Accordion disableGutters>
        <AccordionSummary expandIcon={<ExpandMoreOutlinedIcon />}>
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Audio treatment / Kết xuất / QC</Typography>
            <Typography variant="caption" color="text.secondary">Xử lý sau TTS: nền/ducking, render và kiểm tra chất lượng; không đổi provider hay giọng đọc</Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <FormControl size="small" fullWidth>
                <InputLabel>Chế độ âm thanh</InputLabel>
                <Select value={draft.audio_mode || "hq_clean"} label="Chế độ âm thanh" onChange={(e) => patch("audio_mode", e.target.value)}>
                  <MenuItem value="hq_clean">Nền sạch HQ</MenuItem>
                  <MenuItem value="fast_duck">Hạ nền nhanh</MenuItem>
                </Select>
              </FormControl>
              <FormControl size="small" fullWidth>
                <InputLabel>Hồ sơ kết xuất</InputLabel>
                <Select value={draft.render_profile || "hq"} label="Hồ sơ kết xuất" onChange={(e) => patch("render_profile", e.target.value)}>
                  <MenuItem value="draft">Nháp</MenuItem>
                  <MenuItem value="balanced">Cân bằng</MenuItem>
                  <MenuItem value="hq">HQ</MenuItem>
                </Select>
              </FormControl>
              <FormControl size="small" fullWidth>
                <InputLabel>Hồ sơ QC</InputLabel>
                <Select value={draft.qc_profile || "strict"} label="Hồ sơ QC" onChange={(e) => patch("qc_profile", e.target.value)}>
                  <MenuItem value="standard">Tiêu chuẩn</MenuItem>
                  <MenuItem value="strict">Nghiêm ngặt</MenuItem>
                </Select>
              </FormControl>
            </Stack>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <TextField size="small" fullWidth label="Kiểu phụ đề" value={draft.caption_style || "viral"} onChange={(e) => patch("caption_style", e.target.value)} />
              <FormControl size="small" fullWidth>
                <InputLabel>Tỷ lệ khung hình đích</InputLabel>
                <Select value={draft.target_aspect || "9:16"} label="Tỷ lệ khung hình đích" onChange={(e) => patch("target_aspect", e.target.value)}>
                  {["source", "9:16", "16:9", "1:1"].map((value) => <MenuItem key={value} value={value}>{value}</MenuItem>)}
                </Select>
              </FormControl>
            </Stack>
            <FormControlLabel
              control={<Checkbox checked={draft.preserve_background !== false} onChange={(e) => patch("preserve_background", e.target.checked)} />}
              label="Giữ nền / nhạc đã tách"
            />
          </Stack>
        </AccordionDetails>
      </Accordion>

      <Typography variant="caption" color="text.secondary">{modelSummary}</Typography>

      <Divider />
      <Stack direction="row" spacing={1} justifyContent="flex-end" flexWrap="wrap" useFlexGap>
        {!isDefault && <Button variant="text" onClick={makeDefault} disabled={busy}>Đặt mặc định</Button>}
        {store.profiles.length > 1 && <Button color="error" variant="outlined" startIcon={<DeleteOutlineOutlinedIcon />} onClick={remove} disabled={busy}>Xóa</Button>}
        <Button variant="contained" startIcon={<SaveOutlinedIcon />} onClick={save} disabled={busy}>
          {busy ? "Đang lưu…" : "Lưu hồ sơ"}
        </Button>
      </Stack>
    </Stack>
  )
}
