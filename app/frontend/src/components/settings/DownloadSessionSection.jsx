import { useEffect, useState } from "react"
import {
  Alert, Box, Button, Chip, FormControl, InputLabel, MenuItem, Select, Stack,
  TextField, Typography,
} from "@mui/material"
import LoginOutlinedIcon from "@mui/icons-material/LoginOutlined"
import RefreshOutlinedIcon from "@mui/icons-material/RefreshOutlined"
import SaveOutlinedIcon from "@mui/icons-material/SaveOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

const BROWSERS = [
  ["auto", "Auto detect"],
  ["edge", "Microsoft Edge"],
  ["chrome", "Google Chrome"],
  ["brave", "Brave"],
  ["firefox", "Firefox"],
]

export default function DownloadSessionSection({ settings, updateSettings }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [browser, setBrowser] = useState(settings?.download_cookie_browser || "auto")
  const [profile, setProfile] = useState(settings?.download_cookie_profile || "")
  const [cdpUrl, setCdpUrl] = useState(settings?.douyin_cdp_url || "http://127.0.0.1:9222")
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [probe, setProbe] = useState(null)

  useEffect(() => {
    setBrowser(settings?.download_cookie_browser || "auto")
    setProfile(settings?.download_cookie_profile || "")
    setCdpUrl(settings?.douyin_cdp_url || "http://127.0.0.1:9222")
  }, [settings?.download_cookie_browser, settings?.download_cookie_profile, settings?.douyin_cdp_url])

  const save = async () => {
    setSaving(true)
    try {
      await updateSettings({
        download_cookie_browser: browser,
        download_cookie_profile: profile,
        douyin_cdp_url: cdpUrl,
      })
      showSnackbar("Đã lưu phiên trình duyệt để tải xuống", "success")
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setProbe(null)
    try {
      const { data } = await http.post("/api/settings/download-session/health", {
        browser,
        profile,
        douyin_cdp_url: cdpUrl,
      })
      setProbe(data)
      if (data.douyin_ready || data.tiktok_ready) {
        showSnackbar(
          `Douyin ${data.douyin_ready ? "READY" : "chưa sẵn sàng"} · TikTok ${data.tiktok_ready ? "READY" : "chưa sẵn sàng"}`,
          data.douyin_ready ? "success" : "warning",
        )
      } else {
        showSnackbar(
          data.douyin_cdp?.error || data.cookie?.error || "Chưa có phiên đăng nhập dùng được",
          "warning",
        )
      }
    } catch (err) {
      const message = err?.response?.data?.detail || "Kiểm tra phiên trình duyệt thất bại"
      setProbe({ ok: false, error: message })
      showSnackbar(message, "error")
    } finally {
      setTesting(false)
    }
  }

  const cookieProbe = probe?.cookie || null
  const cdpProbe = probe?.douyin_cdp || null
  const byteDanceCookies = (cookieProbe?.douyin_cookie_count || 0) + (cookieProbe?.tiktok_cookie_count || 0)

  return (
    <Box sx={{ p: 2, border: 1, borderColor: "divider", borderRadius: 2, mb: 2 }}>
      <Stack spacing={1.5}>
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
            Download Browser Session
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Used by the canonical yt-dlp downloader for login-gated sites. TikTok depends on this cookie path.
            Douyin prefers it when readable, but can fall back to the already-authenticated CDP browser if Windows blocks cookie decryption.
            Affiliate2 stores only the browser/profile choice and never exports an all-sites cookie file.
          </Typography>
        </Box>

        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
          <FormControl size="small" fullWidth>
            <InputLabel>Trình duyệt</InputLabel>
            <Select value={browser} label="Trình duyệt" onChange={(event) => { setBrowser(event.target.value); setProbe(null) }}>
              {BROWSERS.map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField
            size="small"
            fullWidth
            label="Hồ sơ trình duyệt (tùy chọn)"
            value={profile}
            onChange={(event) => { setProfile(event.target.value); setProbe(null) }}
            placeholder="Default"
            helperText="Để trống để yt-dlp dùng hồ sơ trình duyệt được truy cập gần nhất. Nhập Default hoặc Profile 1 nếu cần đúng hồ sơ đã đăng nhập."
          />
        </Stack>

        <TextField
          size="small"
          fullWidth
          label="Douyin CDP URL"
          value={cdpUrl}
          onChange={(event) => { setCdpUrl(event.target.value); setProbe(null) }}
          placeholder="http://127.0.0.1:9222"
          helperText="Endpoint DevTools local của Edge/Chrome đã mở bằng --remote-debugging-port. Chỉ localhost được chấp nhận."
        />

        <Alert severity="info" variant="outlined">
          Kiểm tra đầy đủ tách riêng browser-cookie, kết nối CDP và trạng thái đăng nhập Douyin.
          TikTok vẫn cần cookie đúng domain; Douyin dùng cookie hoặc CDP authenticated fallback.
        </Alert>

        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} flexWrap="wrap" useFlexGap>
          <Button variant="contained" startIcon={<SaveOutlinedIcon />} onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
          <Button variant="outlined" startIcon={<RefreshOutlinedIcon />} onClick={test} disabled={testing}>
            {testing ? "Đang kiểm tra…" : "Kiểm tra đầy đủ"}
          </Button>
          <Button variant="text" startIcon={<LoginOutlinedIcon />} href="https://www.douyin.com/" target="_blank" rel="noreferrer">
            Open Douyin
          </Button>
          <Button variant="text" href="https://www.tiktok.com/" target="_blank" rel="noreferrer">
            Open TikTok
          </Button>
        </Stack>

        {probe && (
          <Alert severity={probe.douyin_ready ? "success" : "warning"}>
            <Stack spacing={1}>
              <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap alignItems="center">
                <Chip
                  size="small"
                  color={cookieProbe?.ok ? "success" : "warning"}
                  label={cookieProbe?.ok ? "Cookie store: OK" : "Cookie store: FAIL"}
                />
                <Chip
                  size="small"
                  color={cdpProbe?.connected ? "success" : "warning"}
                  label={cdpProbe?.connected ? "CDP: Connected" : "CDP: Disconnected"}
                />
                <Chip
                  size="small"
                  color={cdpProbe?.authenticated ? "success" : "warning"}
                  label={cdpProbe?.authenticated ? "Douyin: Authenticated" : "Douyin: Login required"}
                />
                <Chip size="small" variant="outlined" label={probe.douyin_ready ? "Douyin READY" : "Douyin chưa sẵn sàng"} />
                <Chip size="small" variant="outlined" label={probe.tiktok_ready ? "TikTok READY" : "TikTok chưa sẵn sàng"} />
              </Stack>

              {cookieProbe?.ok ? (
                <Typography variant="caption">
                  Cookie: <strong>{cookieProbe.resolved_browser || browser}</strong>
                  {cookieProbe.profile ? " / " + cookieProbe.profile : ""}
                  {" · " + byteDanceCookies + " ByteDance cookie(s) · Douyin " + (cookieProbe.douyin_cookie_count || 0) + " · TikTok " + (cookieProbe.tiktok_cookie_count || 0)}
                </Typography>
              ) : (
                <Typography variant="caption" color="text.secondary">
                  Cookie path: {cookieProbe?.error || "không đọc được cookie store"}
                </Typography>
              )}

              <Typography variant="caption" color="text.secondary">
                CDP: {cdpProbe?.url || cdpUrl}
                {cdpProbe?.authenticated
                  ? " · đăng nhập hợp lệ · " + (cdpProbe.cookie_count || 0) + " cookie(s)"
                  : " · " + (cdpProbe?.error || "chưa xác nhận đăng nhập")}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                Media resolver chỉ được xác nhận cuối cùng khi tải một URL Douyin thật; health check không giả vờ rằng chỉ đăng nhập là đủ.
              </Typography>
            </Stack>
          </Alert>
        )}
      </Stack>
    </Box>
  )
}
