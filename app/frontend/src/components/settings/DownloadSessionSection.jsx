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
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [probe, setProbe] = useState(null)

  useEffect(() => {
    setBrowser(settings?.download_cookie_browser || "auto")
    setProfile(settings?.download_cookie_profile || "")
  }, [settings?.download_cookie_browser, settings?.download_cookie_profile])

  const save = async () => {
    setSaving(true)
    try {
      await updateSettings({
        download_cookie_browser: browser,
        download_cookie_profile: profile,
      })
      showSnackbar("Download browser session saved", "success")
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setProbe(null)
    try {
      const { data } = await http.post("/api/settings/download-session/test", {
        browser,
        profile,
      })
      setProbe(data)
      if (data.ok) {
        showSnackbar(`Browser session readable via ${data.resolved_browser || browser}`, "success")
      } else {
        showSnackbar(data.error || "Browser session test failed", "error")
      }
    } catch (err) {
      const message = err?.response?.data?.detail || "Browser session test failed"
      setProbe({ ok: false, error: message })
      showSnackbar(message, "error")
    } finally {
      setTesting(false)
    }
  }

  const byteDanceCookies = (probe?.douyin_cookie_count || 0) + (probe?.tiktok_cookie_count || 0)

  return (
    <Box sx={{ p: 2, border: 1, borderColor: "divider", borderRadius: 2, mb: 2 }}>
      <Stack spacing={1.5}>
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
            Download Browser Session
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Used by the canonical yt-dlp downloader for sites that require login, including Douyin/TikTok.
            Affiliate2 stores only the browser/profile choice; it reads cookies locally through yt-dlp and does not export an all-sites cookie file.
          </Typography>
        </Box>

        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
          <FormControl size="small" fullWidth>
            <InputLabel>Browser</InputLabel>
            <Select value={browser} label="Browser" onChange={(event) => { setBrowser(event.target.value); setProbe(null) }}>
              {BROWSERS.map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField
            size="small"
            fullWidth
            label="Browser profile (optional)"
            value={profile}
            onChange={(event) => { setProfile(event.target.value); setProbe(null) }}
            placeholder="Default"
            helperText="Leave blank to let yt-dlp use the browser's most recently accessed profile. Enter e.g. Default or Profile 1 when you need an exact login profile."
          />
        </Stack>

        <Alert severity="info" variant="outlined">
          Log in to Douyin/TikTok in the selected browser profile first, then refresh that site shortly before downloading.
          Test session checks whether yt-dlp can read the local cookie store; the real URL download is the final login check.
        </Alert>

        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} flexWrap="wrap" useFlexGap>
          <Button variant="contained" startIcon={<SaveOutlinedIcon />} onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
          <Button variant="outlined" startIcon={<RefreshOutlinedIcon />} onClick={test} disabled={testing}>
            {testing ? "Testing…" : "Test session"}
          </Button>
          <Button variant="text" startIcon={<LoginOutlinedIcon />} href="https://www.douyin.com/" target="_blank" rel="noreferrer">
            Open Douyin
          </Button>
          <Button variant="text" href="https://www.tiktok.com/" target="_blank" rel="noreferrer">
            Open TikTok
          </Button>
        </Stack>

        {probe && probe.ok && (
          <Alert severity={byteDanceCookies > 0 ? "success" : "warning"}>
            <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap alignItems="center">
              <span>
                Cookie store is readable from <strong>{probe.resolved_browser}</strong>
                {probe.profile ? ` / ${probe.profile}` : ""}.
              </span>
              <Chip size="small" label={`${probe.cookie_count || 0} total`} />
              <Chip size="small" label={`Douyin ${probe.douyin_cookie_count || 0}`} />
              <Chip size="small" label={`TikTok ${probe.tiktok_cookie_count || 0}`} />
            </Stack>
            {byteDanceCookies === 0 && (
              <Typography variant="caption" sx={{ display: "block", mt: 0.75 }}>
                Browser access works, but no Douyin/TikTok-domain cookies were found in this profile. Log in there, refresh the site, then test again.
              </Typography>
            )}
            {byteDanceCookies > 0 && (
              <Typography variant="caption" sx={{ display: "block", mt: 0.75 }}>
                Domain cookies found does not guarantee that the login is still valid; test the actual Douyin/TikTok URL in Video Download next.
              </Typography>
            )}
          </Alert>
        )}

        {probe && !probe.ok && <Alert severity="error">{probe.error || "Browser session test failed"}</Alert>}
      </Stack>
    </Box>
  )
}
