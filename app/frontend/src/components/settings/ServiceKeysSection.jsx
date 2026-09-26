import { useState, useEffect } from "react"
import {
  Box, Stack, Typography, Alert, TextField, Button, IconButton, InputAdornment,
  Chip, Dialog, DialogTitle, DialogContent, DialogActions, Link,
} from "@mui/material"
import VisibilityIcon from "@mui/icons-material/VisibilityOutlined"
import VisibilityOffIcon from "@mui/icons-material/VisibilityOffOutlined"
import SaveIcon from "@mui/icons-material/SaveOutlined"
import EditIcon from "@mui/icons-material/EditOutlined"
import DeleteIcon from "@mui/icons-material/DeleteOutlineOutlined"
import CheckCircleIcon from "@mui/icons-material/CheckCircleOutline"
import YouTubeIcon from "@mui/icons-material/YouTube"
import HubOutlinedIcon from "@mui/icons-material/HubOutlined"
import useAppStore from "../../store/appStore"
import http from "../../api/http"
import DownloadSessionSection from "./DownloadSessionSection"

/**
 * Generic per-service BYOK key card. Drop more entries into SERVICES below to
 * add Pexels / TikHub / etc. — the row keeps its own form state.
 */
const SERVICES = [
  {
    id: "youtube",
    label: "YouTube Data API",
    description: "Dùng cho quét YouTube, đọc kênh và phân tích bình luận",
    icon: <YouTubeIcon sx={{ color: "#FF0000", fontSize: 28 }} />,
    settingsKey: "youtube_api_key",      // POST /api/settings field
    setFlag: "youtube_api_key_set",       // GET response flag (masked)
    placeholder: "AIzaSy...",
    docsHref: "https://console.cloud.google.com/apis/credentials",
    docsHint: "Get a free key at Google Cloud Console → Credentials → Create API key (10K units/day free)",
  },
  {
    id: "huggingface",
    label: "Hugging Face",
    description: "Phụ thuộc tùy chọn của Phase C cho pyannote/WhisperX tách người nói; không cần cho tải xuống",
    icon: <HubOutlinedIcon sx={{ color: "#f59e0b", fontSize: 28 }} />,
    settingsKey: "huggingface_token",
    setFlag: "huggingface_token_set",
    placeholder: "hf_...",
    docsHref: "https://huggingface.co/settings/tokens",
    docsHint: "Use a read token with access to the diarization model licenses required by WhisperX/pyannote",
  },
  // To add more services later, append:
  // { id: "pexels", label: "Pexels API", description: "Stock footage", ...,
  //   settingsKey: "pexels_api_key", setFlag: "pexels_api_key_set", ... }
]

function ServiceKeyRow({ service, settings, updateSettings }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const keyIsSet = !!settings?.[service.setFlag]

  const [editing, setEditing] = useState(!keyIsSet)
  const [apiKey, setApiKey] = useState("")
  const [showKey, setShowKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [confirmRemove, setConfirmRemove] = useState(false)

  // When the parent's keyIsSet flips (e.g. another tab cleared the key),
  // jump back to edit mode so the form is shown. Don't yank the user OUT of
  // edit mode if a save just landed — handleSave() owns that transition.
  useEffect(() => {
    if (!keyIsSet) setEditing(true)
  }, [keyIsSet])

  const handleSave = async () => {
    const value = apiKey.trim()
    if (!value) {
      showSnackbar("Hãy dán API key trước", "warning")
      return
    }
    setSaving(true)
    try {
      await updateSettings({ [service.settingsKey]: value })
      setApiKey("")
      setEditing(false)
      showSnackbar(`Đã lưu khóa ${service.label}`, "success")
    } catch (e) {
      // updateSettings already shows a snackbar on error
    } finally {
      setSaving(false)
    }
  }

  const handleRemove = async () => {
    setConfirmRemove(false)
    setSaving(true)
    try {
      await updateSettings({ [service.settingsKey]: "" })
      setApiKey("")
      setEditing(true)
      showSnackbar(`Đã xóa khóa ${service.label} — sẽ dùng .env nếu có cấu hình`, "info")
    } catch (e) {
      // handled in updateSettings
    } finally {
      setSaving(false)
    }
  }

  return (
    <Box
      sx={{
        p: 2,
        border: 1,
        borderColor: "divider",
        borderRadius: 2,
        "&:not(:last-of-type)": { mb: 2 },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 1.5 }}>
        {service.icon}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
              {service.label}
            </Typography>
            {keyIsSet && (
              <Chip
                size="small"
                color="success"
                variant="outlined"
                icon={<CheckCircleIcon fontSize="small" />}
                label="đã có khóa"
                sx={{ fontWeight: 500 }}
              />
            )}
          </Stack>
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            {service.description}
          </Typography>
        </Box>
      </Stack>

      {!editing && keyIsSet ? (
        <Stack direction="row" spacing={1.5}>
          <Button
            variant="outlined"
            size="small"
            startIcon={<EditIcon />}
            onClick={() => { setEditing(true); setApiKey("") }}
            disabled={saving}
          >
            Update
          </Button>
          <Button
            variant="outlined"
            color="error"
            size="small"
            startIcon={<DeleteIcon />}
            onClick={() => setConfirmRemove(true)}
            disabled={saving}
          >
            Remove
          </Button>
        </Stack>
      ) : (
        <Stack spacing={1.5}>
          <TextField
            type={showKey ? "text" : "password"}
            size="small"
            fullWidth
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={service.placeholder}
            helperText={
              <>
                {service.docsHint}{" "}
                <Link href={service.docsHref} target="_blank" rel="noreferrer">
                  open docs
                </Link>
              </>
            }
            disabled={saving}
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
          <Stack direction="row" spacing={1.5}>
            <Button
              variant="contained"
              size="small"
              startIcon={<SaveIcon />}
              onClick={handleSave}
              disabled={saving || !apiKey.trim()}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
            {keyIsSet && (
              <Button
                variant="outlined"
                size="small"
                onClick={() => { setEditing(false); setApiKey("") }}
                disabled={saving}
              >
                Cancel
              </Button>
            )}
          </Stack>
        </Stack>
      )}

      <Dialog open={confirmRemove} onClose={() => setConfirmRemove(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Xóa khóa {service.label}?</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            This deletes the encrypted key from your local database. Affiliate2 will fall back to
            khóa tương ứng trong tệp <code>.env</code> (nếu có). Các tính năng phụ thuộc vào
            this service will stop working until you add a new key or restore the .env one.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmRemove(false)}>Hủy</Button>
          <Button color="error" variant="contained" onClick={handleRemove}>
            Remove key
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

function MetaPublishSection({ settings, updateSettings }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [pageId, setPageId] = useState(settings?.meta_page_id || "")
  const [token, setToken] = useState("")
  const [showToken, setShowToken] = useState(false)
  const [testing, setTesting] = useState(false)
  const [probe, setProbe] = useState(null)

  useEffect(() => {
    setPageId(settings?.meta_page_id || "")
  }, [settings?.meta_page_id])

  const verify = async () => {
    if (!pageId.trim()) {
      showSnackbar("Nhập Facebook Page ID trước", "warning")
      return
    }
    if (!token.trim() && !settings?.meta_page_access_token_set) {
      showSnackbar("Dán Page access token trước", "warning")
      return
    }

    setTesting(true)
    setProbe(null)
    try {
      const { data } = await http.post("/api/settings/meta/test", {
        page_id: pageId.trim(),
        ...(token.trim() ? { access_token: token.trim() } : {}),
      })
      setProbe(data)
      setToken("")
      showSnackbar(
        data.instagram_ready
          ? "Meta Page + Instagram Professional đã sẵn sàng"
          : "Facebook Page đã sẵn sàng; chưa có Instagram Professional liên kết",
        data.instagram_ready ? "success" : "info",
      )
    } catch (e) {
      const message = e.response?.data?.detail || "Kiểm tra Meta Publish thất bại"
      setProbe({ ok: false, error: message })
      showSnackbar(message, "error")
    } finally {
      setTesting(false)
    }
  }

  const clear = async () => {
    await updateSettings({
      meta_page_access_token: "",
      meta_page_id: "",
    })
    setToken("")
    setPageId("")
    setProbe(null)
    showSnackbar("Đã xóa kết nối Meta Publish", "info")
  }

  const ready = Boolean(
    settings?.facebook_page_connected
    || probe?.facebook_ready
    || settings?.meta_page_access_token_set
  )

  return (
    <Box sx={{ p: 2, border: 1, borderColor: "divider", borderRadius: 2, mb: 2 }}>
      <Stack spacing={1.5}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Meta Publish</Typography>
            {ready && <Chip size="small" color="success" variant="outlined" label="đã cấu hình" />}
          </Stack>
          <Typography variant="caption" color="text.secondary">
            Canonical scope: Facebook Page Reels + Instagram Professional (Business/Creator) gắn với Page.
            Không hỗ trợ Facebook profile cá nhân. Video được upload trực tiếp lên Meta, không qua file host tạm.
          </Typography>
        </Box>

        <TextField
          size="small"
          fullWidth
          label="Facebook Page ID"
          value={pageId}
          onChange={(e) => { setPageId(e.target.value); setProbe(null) }}
          placeholder="123456789012345"
          disabled={testing}
        />

        <TextField
          size="small"
          fullWidth
          label={settings?.meta_page_access_token_set ? "Page access token mới (để trống để giữ token hiện tại)" : "Page access token"}
          type={showToken ? "text" : "password"}
          value={token}
          onChange={(e) => { setToken(e.target.value); setProbe(null) }}
          disabled={testing}
          InputProps={{
            endAdornment: (
              <InputAdornment position="end">
                <IconButton onClick={() => setShowToken((v) => !v)} edge="end" size="small">
                  {showToken ? <VisibilityOffIcon /> : <VisibilityIcon />}
                </IconButton>
              </InputAdornment>
            ),
          }}
        />

        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          <Button variant="contained" onClick={verify} disabled={testing || !pageId.trim()}>
            {testing ? "Đang xác minh…" : "Lưu & xác minh Meta"}
          </Button>
          {settings?.meta_page_access_token_set && (
            <Button variant="outlined" color="error" onClick={clear} disabled={testing}>
              Xóa kết nối
            </Button>
          )}
        </Stack>

        {(probe || settings?.facebook_page_connected) && (
          <Alert severity={(probe?.facebook_ready || settings?.facebook_page_connected) ? "success" : "warning"} variant="outlined">
            {probe?.error ? probe.error : (
              <Stack spacing={0.5}>
                <Typography variant="body2">
                  Facebook Page: <strong>{probe?.page_name || settings?.meta_page_name || pageId || "đã xác minh"}</strong>
                </Typography>
                <Typography variant="caption">
                  Instagram Professional: {
                    probe?.instagram_ready || settings?.instagram_connected
                      ? `READY · ${probe?.instagram_user_id || settings?.instagram_user_id || ""}`
                      : "chưa phát hiện tài khoản Business/Creator liên kết"
                  }
                </Typography>
              </Stack>
            )}
          </Alert>
        )}
      </Stack>
    </Box>
  )
}


export default function ServiceKeysSection({ settings, updateSettings }) {
  return (
    <Stack spacing={0}>
      <DownloadSessionSection settings={settings} updateSettings={updateSettings} />
      <MetaPublishSection settings={settings} updateSettings={updateSettings} />
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Khóa dịch vụ / phụ thuộc tùy chọn</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        API keys below are separate from browser login. They are encrypted before storage.
        Hugging Face is only needed for gated multi-speaker diarization; it does not affect video download.
      </Typography>
      {SERVICES.map((service) => (
        <ServiceKeyRow
          key={service.id}
          service={service}
          settings={settings}
          updateSettings={updateSettings}
        />
      ))}
    </Stack>
  )
}
