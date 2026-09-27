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

const DOUYIN_BROWSERS = [
  ["edge", "Microsoft Edge"],
  ["chrome", "Google Chrome"],
  ["brave", "Brave"],
]

const COOKIE_BROWSERS = [
  ["auto", "Tự động (khuyến nghị)"],
  ["firefox", "Firefox"],
  ["edge", "Microsoft Edge"],
  ["chrome", "Google Chrome"],
  ["brave", "Brave"],
]

const reasonText = {
  browser_not_found: "Không tìm thấy trình duyệt",
  cdp_unreachable: "Đang chờ trình duyệt",
  cdp_websocket_unavailable: "Chưa thể kết nối DevTools WebSocket",
  douyin_page_unreachable: "Chưa thể mở trang Douyin",
  douyin_login_required: "Cần đăng nhập Douyin",
  profile_not_found: "Không tìm thấy hồ sơ trình duyệt",
  database_unreadable: "Không đọc được cơ sở dữ liệu cookie",
  decryption_failed: "Windows không cho phép giải mã cookie của trình duyệt này",
  extraction_failed: "Trích xuất cookie thất bại",
  tiktok_login_required: "Cần đăng nhập TikTok",
}

export default function DownloadSessionSection({ settings, updateSettings }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [douyinBrowser, setDouyinBrowser] = useState(settings?.douyin_browser || "edge")
  const [cookieBrowser, setCookieBrowser] = useState(settings?.download_cookie_browser || "auto")
  const [cookieProfile, setCookieProfile] = useState(settings?.download_cookie_profile || "")
  const [cdpUrl, setCdpUrl] = useState(settings?.douyin_cdp_url || "http://127.0.0.1:9222")
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [opening, setOpening] = useState(false)
  const [openingTikTok, setOpeningTikTok] = useState(false)
  const [repairing, setRepairing] = useState("")
  const [resetting, setResetting] = useState(false)
  const [probe, setProbe] = useState(null)
  const [launchResult, setLaunchResult] = useState(null)

  useEffect(() => {
    setDouyinBrowser(settings?.douyin_browser || "edge")
    setCookieBrowser(settings?.download_cookie_browser || "auto")
    setCookieProfile(settings?.download_cookie_profile || "")
    setCdpUrl(settings?.douyin_cdp_url || "http://127.0.0.1:9222")
  }, [settings?.douyin_browser, settings?.download_cookie_browser, settings?.download_cookie_profile, settings?.douyin_cdp_url])

  const save = async () => {
    setSaving(true)
    try {
      await updateSettings({
        douyin_browser: douyinBrowser,
        download_cookie_browser: cookieBrowser,
        download_cookie_profile: cookieProfile,
        douyin_cdp_url: cdpUrl,
      })
      showSnackbar("Đã lưu hai cơ chế xác thực tải xuống", "success")
    } finally {
      setSaving(false)
    }
  }

  const test = async ({ notify = true, cdp_url: overrideUrl } = {}) => {
    const effectiveCdpUrl = overrideUrl || cdpUrl
    setTesting(true)
    try {
      const { data } = await http.post("/api/settings/download-session/health", {
        douyin_browser: douyinBrowser,
        cookie_browser: cookieBrowser,
        cookie_profile: cookieProfile,
        douyin_cdp_url: effectiveCdpUrl,
      })
      setProbe(data)
      setCdpUrl(data?.douyin_session?.cdp_url || effectiveCdpUrl)
      if (notify) {
        showSnackbar(
          `Douyin ${data.douyin_session?.ready ? "READY" : "chưa sẵn sàng"} · TikTok ${data.cookie_source?.tiktok_ready ? "READY" : "chưa sẵn sàng"}`,
          data.douyin_session?.ready || data.cookie_source?.tiktok_ready ? "success" : "warning",
        )
      }
      return data
    } catch (err) {
      const message = err?.response?.data?.detail || "Kiểm tra xác thực tải xuống thất bại"
      setProbe({ ok: false, error: message })
      if (notify) showSnackbar(message, "error")
      return null
    } finally {
      setTesting(false)
    }
  }

  const openDouyin = async () => {
    setOpening(true)
    setLaunchResult(null)
    try {
      const { data } = await http.post("/api/settings/download-session/open-douyin", {
        douyin_browser: douyinBrowser,
        douyin_cdp_url: cdpUrl,
      })
      setLaunchResult(data)
      if (data.cdp_url) setCdpUrl(data.cdp_url)
      if (data.connected) await test({ notify: false, cdp_url: data.cdp_url || cdpUrl })
      if (data.authenticated) showSnackbar("Phiên trình duyệt Douyin đã xác thực và sẵn sàng.", "success")
      else if (data.auth_required) showSnackbar("Cần đăng nhập Douyin trong cửa sổ Edge vừa mở.", "info")
      else if (!data.ok) showSnackbar(data.message || "Không thể mở phiên Douyin", "error")
    } catch (err) {
      const message = err?.response?.data?.detail || "Không thể mở phiên Douyin"
      setLaunchResult({ ok: false, status: "error", message })
      showSnackbar(message, "error")
    } finally {
      setOpening(false)
    }
  }

  const repair = async (scope = "all") => {
    setRepairing(scope)
    try {
      const { data } = await http.post("/api/settings/download-session/repair", { scope })
      if (data?.douyin_repair?.cdp_url) setCdpUrl(data.douyin_repair.cdp_url)
      if (data?.cookie_repair?.resolved_browser) setCookieBrowser(data.cookie_repair.resolved_browser)
      if (data?.cookie_repair?.profile) setCookieProfile(data.cookie_repair.profile)
      await test({ notify: false, cdp_url: data?.douyin_repair?.cdp_url || cdpUrl })
      if (data.douyin_login_required) showSnackbar("Cần đăng nhập Douyin", "info")
      else if (data.tiktok_login_required) showSnackbar("Cần đăng nhập TikTok", "info")
      else showSnackbar(data.ok ? "Tự sửa hoàn tất" : "Tự sửa chưa xử lý được toàn bộ lỗi", data.ok ? "success" : "warning")
    } catch (err) {
      showSnackbar(err?.response?.data?.detail || "Tự sửa thất bại", "error")
    } finally {
      setRepairing("")
    }
  }

  const openTikTok = async () => {
    setOpeningTikTok(true)
    try {
      const { data } = await http.post("/api/settings/download-session/open-tiktok", {
        cookie_browser: cookieBrowser,
        cookie_profile: cookieProfile,
      })
      if (!data.ok) showSnackbar(data.message || data.error || "Không thể mở TikTok", "error")
      else showSnackbar("Đã mở TikTok bằng đúng trình duyệt/hồ sơ cookie.", "info")
    } catch (err) {
      showSnackbar(err?.response?.data?.detail || "Không thể mở TikTok", "error")
    } finally {
      setOpeningTikTok(false)
    }
  }

  const selectExecutable = async (target, browser) => {
    try {
      const { data } = await http.post("/api/settings/download-session/select-executable", { target, browser })
      if (data.ok) {
        showSnackbar("Đã lưu file trình duyệt hợp lệ", "success")
        await test({ notify: false })
      }
    } catch (err) {
      showSnackbar(err?.response?.data?.detail || "Không thể chọn file trình duyệt", "error")
    }
  }

  const resetDouyin = async () => {
    if (!window.confirm("Chỉ reset hồ sơ Douyin do Affiliate2 quản lý. Tiếp tục?")) return
    setResetting(true)
    try {
      const { data } = await http.post("/api/settings/download-session/reset-douyin", { confirm: true, clear_profile: true })
      showSnackbar(data.ok ? "Đã reset phiên Douyin của Affiliate2" : data.message || "Không thể reset an toàn", data.ok ? "success" : "warning")
      await test({ notify: false })
    } finally {
      setResetting(false)
    }
  }

  const douyin = probe?.douyin_session || null
  const cookie = probe?.cookie_source || null
  const douyinReason = douyin?.blocking_reason
  const cookieReason = cookie?.blocking_reason

  return (
    <Box sx={{ p: 2, border: 1, borderColor: "divider", borderRadius: 2, mb: 2 }}>
      <Stack spacing={2}>
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Xác thực tải xuống</Typography>
          <Typography variant="caption" color="text.secondary">
            Douyin dùng phiên Edge/CDP riêng của Affiliate2. TikTok và các nguồn yt-dlp dùng nguồn cookie trình duyệt độc lập; hai cơ chế có thể dùng hai trình duyệt khác nhau.
          </Typography>
        </Box>

        <Box sx={{ p: 1.5, border: 1, borderColor: "divider", borderRadius: 1.5 }}>
          <Stack spacing={1.25}>
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Phiên trình duyệt Douyin</Typography>
            <FormControl size="small" fullWidth>
              <InputLabel>Trình duyệt Douyin</InputLabel>
              <Select value={douyinBrowser} label="Trình duyệt Douyin" onChange={(event) => { setDouyinBrowser(event.target.value); setProbe(null) }}>
                {DOUYIN_BROWSERS.map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
              </Select>
            </FormControl>

            <Stack spacing={0.5}>
              <Typography variant="caption" sx={{ overflowWrap: "anywhere" }}>
                Executable: {douyin?.executable_path || "Chưa xác định"}
              </Typography>
              <Typography variant="caption" sx={{ overflowWrap: "anywhere" }}>
                Hồ sơ Affiliate2: {douyin?.profile_path || "Sẽ tự tạo khi mở phiên"}
              </Typography>
              <Typography variant="caption" sx={{ overflowWrap: "anywhere" }}>
                CDP đang hoạt động: {douyin?.cdp_url || cdpUrl}
              </Typography>
            </Stack>

            {douyin && (
              <Stack spacing={0.5}>
                <Chip size="small" color={douyin.executable_found ? "success" : "error"} label={douyin.executable_found ? "Microsoft Edge: Đã tìm thấy" : "Microsoft Edge: Không tìm thấy"} />
                <Chip size="small" color={douyin.profile_ready ? "success" : "default"} label={douyin.profile_ready ? "Hồ sơ Affiliate2: Sẵn sàng" : "Hồ sơ Affiliate2: Chưa tạo"} />
                <Chip size="small" color={douyin.cdp_http_reachable ? "success" : "default"} label={douyin.cdp_http_reachable ? "CDP: Đã kết nối" : "CDP: Đang chờ trình duyệt"} />
                <Chip size="small" color={douyin.page_reachable ? "success" : "default"} label={douyin.page_reachable ? "Trang Douyin: Đã mở" : douyin.cdp_websocket_connected ? "Trang Douyin: Chưa mở" : "Trang Douyin: Chưa thể kiểm tra"} />
                <Chip size="small" color={douyin.authenticated ? "success" : "default"} label={douyin.authenticated ? "Douyin: Đã đăng nhập" : douyin.cdp_websocket_connected ? "Douyin: Cần đăng nhập" : "Douyin: Chưa thể kiểm tra cho tới khi trình duyệt chạy"} />
                {douyinReason && <Typography variant="caption" color="warning.main">Nguyên nhân chính: {reasonText[douyinReason] || douyinReason}</Typography>}
              </Stack>
            )}

            {launchResult && !launchResult.authenticated && (
              <Alert severity={launchResult.auth_required ? "info" : "warning"}>
                {launchResult.auth_required ? "Cần đăng nhập Douyin trong cửa sổ vừa mở." : launchResult.message || launchResult.cdp_error}
              </Alert>
            )}

            <Stack direction={{ xs: "column", sm: "row" }} spacing={1} flexWrap="wrap" useFlexGap>
              <Button variant="contained" startIcon={<LoginOutlinedIcon />} onClick={openDouyin} disabled={opening || testing}>
                {opening ? "Đang mở…" : "Mở Douyin để đăng nhập"}
              </Button>
              <Button variant="outlined" startIcon={<RefreshOutlinedIcon />} onClick={() => repair("douyin")} disabled={Boolean(repairing)}>
                {repairing === "douyin" ? "Đang tự sửa…" : "Tự sửa"}
              </Button>
              {!douyin?.executable_found && <Button variant="text" onClick={() => selectExecutable("douyin", douyinBrowser)}>Chọn file trình duyệt</Button>}
              <Button color="warning" variant="text" onClick={resetDouyin} disabled={resetting}>Reset phiên Douyin</Button>
            </Stack>
          </Stack>
        </Box>

        <Box sx={{ p: 1.5, border: 1, borderColor: "divider", borderRadius: 1.5 }}>
          <Stack spacing={1.25}>
            <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Cookie tải xuống / TikTok</Typography>
            <Typography variant="caption" color="text.secondary">
              Auto sẽ kiểm tra nguồn cookie thực sự đọc được. Trên Windows, Firefox được ưu tiên trước khi thử Chromium có thể bị App-Bound/DPAPI chặn.
            </Typography>
            <FormControl size="small" fullWidth>
              <InputLabel>Nguồn cookie</InputLabel>
              <Select value={cookieBrowser} label="Nguồn cookie" onChange={(event) => { setCookieBrowser(event.target.value); setProbe(null) }}>
                {COOKIE_BROWSERS.map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
              </Select>
            </FormControl>
            <TextField
              size="small"
              fullWidth
              label="Hồ sơ cookie"
              value={cookieProfile}
              onChange={(event) => { setCookieProfile(event.target.value); setProbe(null) }}
              placeholder="Tự phát hiện từ profiles.ini"
              helperText="Không cần tự tìm hồ sơ Firefox khi dùng Auto."
            />

            {cookie && (
              <Stack spacing={0.5}>
                <Typography variant="caption" sx={{ overflowWrap: "anywhere" }}>
                  Nguồn đã chọn: <strong>{cookie.browser || cookie.selected_browser}</strong>{cookie.profile ? ` · ${cookie.profile}` : ""}
                </Typography>
                <Chip size="small" color={cookie.extraction_ok ? "success" : "warning"} label={cookie.extraction_ok ? "Cookie store: Ready" : "Cookie extraction: Failed"} />
                <Chip size="small" color={cookie.tiktok_ready ? "success" : "warning"} label={cookie.tiktok_ready ? "TikTok: Ready" : cookie.extraction_ok ? "TikTok: Cần đăng nhập" : "TikTok: Chưa thể kiểm tra"} />
                <Typography variant="caption" color="text.secondary">
                  {cookie.cookie_count || 0} cookie đã đọc · TikTok {cookie.tiktok_cookie_count || 0}
                </Typography>
                {cookieReason && <Typography variant="caption" color="warning.main">{reasonText[cookieReason] || cookieReason}</Typography>}
                {!cookie.extraction_ok && douyin?.ready && (
                  <Typography variant="caption" color="text.secondary">Cookie store FAIL không chặn Douyin khi CDP đã xác thực.</Typography>
                )}
              </Stack>
            )}

            <Stack direction={{ xs: "column", sm: "row" }} spacing={1} flexWrap="wrap" useFlexGap>
              <Button variant="contained" onClick={openTikTok} disabled={openingTikTok || testing}>
                {openingTikTok ? "Đang mở…" : "Mở TikTok để đăng nhập"}
              </Button>
              <Button variant="outlined" startIcon={<RefreshOutlinedIcon />} onClick={() => repair("cookie")} disabled={Boolean(repairing)}>
                {repairing === "cookie" ? "Đang tự sửa…" : "Tự sửa"}
              </Button>
              {cookieBrowser !== "auto" && cookie && !cookie.browser_found && (
                <Button variant="text" onClick={() => selectExecutable("cookie", cookieBrowser)}>Chọn file trình duyệt</Button>
              )}
            </Stack>
          </Stack>
        </Box>

        {probe?.error && <Alert severity="error">{probe.error}</Alert>}

        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} flexWrap="wrap" useFlexGap>
          <Button variant="contained" startIcon={<RefreshOutlinedIcon />} onClick={() => repair("all")} disabled={Boolean(repairing)}>
            {repairing === "all" ? "Đang tự sửa tất cả…" : "Tự sửa tất cả"}
          </Button>
          <Button variant="outlined" startIcon={<RefreshOutlinedIcon />} onClick={test} disabled={testing || Boolean(repairing)}>
            {testing ? "Đang kiểm tra…" : "Kiểm tra lại"}
          </Button>
          <Button variant="text" startIcon={<SaveOutlinedIcon />} onClick={save} disabled={saving}>
            {saving ? "Đang lưu…" : "Lưu lựa chọn nâng cao"}
          </Button>
        </Stack>
      </Stack>
    </Box>
  )
}
