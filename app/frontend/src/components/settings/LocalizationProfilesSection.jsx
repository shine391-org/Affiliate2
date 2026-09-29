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
  const currentTtsProvider = settings?.tts_provider || "edge_tts"
  const pinnedTtsProvider = draft?.tts_provider || null
  const edgeCrossProviderOverride = Boolean(
    pinnedTtsProvider === "edge_tts" && currentTtsProvider !== "edge_tts"
  )
  const ttsProviderMismatch = Boolean(
    pinnedTtsProvider &&
    pinnedTtsProvider !== currentTtsProvider &&
    pinnedTtsProvider !== "edge_tts"
  )
  const ttsVoicePlaceholder = edgeCrossProviderOverride
    ? "vi-VN-HoaiMyNeural (mặc định Edge theo ngôn ngữ)"
    : (settings?.preferred_tts_voice || "kế thừa mặc định ngôn ngữ/nhà cung cấp")
  const adoptCurrentTts = () => setDraft((prev) => {
    const providerChanged = Boolean(
      prev?.tts_provider && prev.tts_provider !== currentTtsProvider
    )
    return {
      ...prev,
      tts_provider: currentTtsProvider,
      tts_model: settings?.tts_model || null,
      tts_voice: settings?.preferred_tts_voice || null,
      tts_base_url: settings?.tts_base_url || null,
      tts_region: settings?.tts_region || null,
      // Provider-scoped speaker/clone state must never survive a provider
      // switch. _voice_for_chunk() prioritizes speaker_voices, so retaining an
      // ElevenLabs voice id while adopting Edge would bypass the default voice.
      ...(providerChanged ? {
        speaker_voices: {},
        speaker_reference_audio: {},
        tts_reference_audio: null,
        tts_reference_text: null,
        tts_reference_language: null,
      } : {}),
    }
  })

  const discoverModels = async () => {
    const provider = settings?.ai_provider || ""
    if (!provider || provider === "anthropic") {
      showSnackbar("Kết nối AI này không hỗ trợ dò tìm mô hình", "warning")
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
      showSnackbar(models.length ? `Tìm thấy ${models.length} mô hình trên kết nối đã cấu hình` : "Không có mô hình nào được trả về", models.length ? "success" : "warning")
    } catch (err) {
      showSnackbar(err.response?.data?.detail || "Dò tìm mô hình thất bại", "error")
    } finally {
      setDiscovering(false)
    }
  }

  const save = async () => {
    if (!draft) return
    if (!draft.translation_guardrails?.trim() || !draft.rewrite_guardrails?.trim()) {
      showSnackbar("Guardrail ngữ nghĩa C6 và C7 không được để trống", "error")
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

      {edgeCrossProviderOverride && (
        <Alert severity="info" variant="outlined">
          Hồ sơ này <strong>ghim Edge TTS</strong> dù Kết nối TTS dùng chung đang là <strong>{currentTtsProvider}</strong>. Đây là cấu hình hợp lệ: Edge tự chứa, không kế thừa API key, model, Base URL hoặc voice của provider dùng chung.
        </Alert>
      )}
      {ttsProviderMismatch && (
        <Alert
          severity="warning"
          action={<Button color="inherit" size="small" onClick={adoptCurrentTts}>Dùng TTS hiện tại cho hồ sơ</Button>}
        >
          Provider <strong>{draft.tts_provider}</strong> không phải kết nối TTS đang active (<strong>{currentTtsProvider}</strong>). Ứng dụng có nhớ cấu hình/credential của từng provider để khỏi nhập lại, nhưng job hiện chỉ dùng provider active hoặc Edge TTS tự chứa. Chọn “Dùng TTS hiện tại cho hồ sơ” hoặc chuyển kết nối TTS active trước khi chạy.
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
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
          <Box sx={{ flex: 1 }}>
            <Typography variant="subtitle2">Kết nối AI đã cấu hình</Typography>
            <Typography variant="caption" color="text.secondary">
              {globalProvider} · mô hình toàn cục/mặc định {globalModel}
            </Typography>
          </Box>
          <Button
            size="small"
            variant="outlined"
            startIcon={<RefreshOutlinedIcon />}
            onClick={discoverModels}
            disabled={discovering}
          >
            {discovering ? "Đang dò tìm…" : "Dò tìm mô hình"}
          </Button>
        </Stack>
        {discoveredModels.length > 0 && (
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>
            {discoveredModels.slice(0, 8).map((model) => <Chip key={model} label={model} size="small" variant="outlined" />)}
            {discoveredModels.length > 8 && <Chip label={`+${discoveredModels.length - 8} mô hình nữa`} size="small" />}
          </Stack>
        )}
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
            <FormControl size="small" fullWidth>
              <InputLabel>Phong cách dịch</InputLabel>
              <Select value={draft.translation_preset || "natural_vietnamese"} label="Phong cách dịch" onChange={(e) => patch("translation_preset", e.target.value)}>
                {PRESETS.map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
              </Select>
            </FormControl>
            <Autocomplete
              freeSolo
              size="small"
              fullWidth
              options={[...new Set([settings?.ai_model, ...discoveredModels].filter(Boolean))]}
              value={draft.translation_model || ""}
              onInputChange={(_, value) => patch("translation_model", value || null)}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Mô hình dịch C6"
                  placeholder={globalModel}
                  helperText="Chọn trực tiếp mô hình đã dò được, hoặc nhập ID tùy chỉnh. Để trống = dùng mô hình mặc định/toàn cục."
                />
              )}
            />
            <TextField
              size="small" fullWidth label="Ghi đè Base URL tương thích (tùy chọn)"
              value={draft.translation_base_url || ""}
              onChange={(e) => patch("translation_base_url", e.target.value)}
              placeholder={settings?.ai_base_url || "kế thừa endpoint trong Cài đặt"}
              helperText="Thông tin xác thực kết nối vẫn nằm trong Kết nối AI."
            />
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
            <Autocomplete
              freeSolo
              size="small"
              fullWidth
              options={[...new Set([settings?.ai_model, ...discoveredModels].filter(Boolean))]}
              value={draft.rewrite_model || ""}
              onInputChange={(_, value) => patch("rewrite_model", value || null)}
              renderInput={(params) => (
                <TextField
                  {...params}
                  label="Mô hình viết lại thời lượng C7"
                  placeholder={globalModel}
                  helperText="Chọn trực tiếp mô hình đã dò được hoặc nhập ID khác. Có thể dùng model nhỏ/nhanh hơn C6."
                />
              )}
            />
            <TextField
              size="small" fullWidth label="Ghi đè Base URL tương thích (tùy chọn)"
              value={draft.rewrite_base_url || ""}
              onChange={(e) => patch("rewrite_base_url", e.target.value)}
              placeholder={settings?.ai_base_url || "kế thừa endpoint trong Cài đặt"}
            />
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
                <Select value={draft.tts_provider || ""} label="Ghi đè TTS cho hồ sơ" onChange={(e) => patch("tts_provider", e.target.value || null)}>
                  <MenuItem value=""><em>Kế thừa kết nối TTS dùng chung</em></MenuItem>
                  {["edge_tts", "openai_tts", "openai_compatible_tts", "azure_tts", "elevenlabs", "gpt_sovits"].map((value) => <MenuItem key={value} value={value}>{value}</MenuItem>)}
                </Select>
              </FormControl>
              <TextField size="small" fullWidth label="ID giọng đọc" value={draft.tts_voice || ""} onChange={(e) => patch("tts_voice", e.target.value || null)} placeholder={ttsVoicePlaceholder} />
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
            <FormControlLabel
              control={<Checkbox checked={!!draft.semantic_segmentation} onChange={(e) => patch("semantic_segmentation", e.target.checked)} />}
              label="Phân đoạn ngữ nghĩa bằng AI"
            />
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
