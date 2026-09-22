import { useState, useEffect, useCallback } from "react"
import {
  Box, Typography, Button, Stack, Card, CardContent, Chip, TextField,
  CircularProgress, Divider, Alert, Link, Dialog, DialogTitle, DialogContent,
  DialogActions, IconButton, Grid,
} from "@mui/material"
import CloseIcon from "@mui/icons-material/CloseOutlined"
import TelegramIcon from "@mui/icons-material/Telegram"
import WhatsAppIcon from "@mui/icons-material/WhatsApp"
import CheckCircleIcon from "@mui/icons-material/CheckCircleOutline"
import SendIcon from "@mui/icons-material/SendOutlined"
import LogoutIcon from "@mui/icons-material/LogoutOutlined"
import QrCodeIcon from "@mui/icons-material/QrCode2Outlined"
import ChatBubbleIcon from "@mui/icons-material/ChatBubbleOutlined"
import TagIcon from "@mui/icons-material/TagOutlined"
import PhoneIphoneIcon from "@mui/icons-material/PhoneIphoneOutlined"
import { QRCodeCanvas } from "qrcode.react"
import http from "../api/http"
import { ws } from "../api/websocket"
import useAppStore from "../store/appStore"
import useDocumentTitle from "../hooks/useDocumentTitle"

const STATE = {
  DISCONNECTED: "disconnected",
  CONNECTING: "connecting",
  AWAITING_START: "awaiting_start",
  CONNECTED: "connected",
}

const WA_STATE = {
  DISCONNECTED: "disconnected",
  PAIRING: "pairing",
  CONNECTED: "connected",
  UNAVAILABLE: "unavailable",
}

const DC_STATE = {
  DISCONNECTED: "disconnected",
  CONNECTING: "connecting",
  AWAITING_DM: "awaiting_dm",
  CONNECTED: "connected",
  UNAVAILABLE: "unavailable",
}

const SL_STATE = {
  DISCONNECTED: "disconnected",
  CONNECTING: "connecting",
  AWAITING_DM: "awaiting_dm",
  CONNECTED: "connected",
  UNAVAILABLE: "unavailable",
}

export default function Messaging() {
  useDocumentTitle("Messaging")
  const showSnackbar = useAppStore((s) => s.showSnackbar)

  const [loading, setLoading] = useState(true)
  const [tgState, setTgState] = useState(STATE.DISCONNECTED)
  const [tgInfo, setTgInfo] = useState({ bot_username: "", bot_url: "", chat_id: "" })
  const [botToken, setBotToken] = useState("")
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

  const [waState, setWaState] = useState(WA_STATE.DISCONNECTED)
  const [waInfo, setWaInfo] = useState({ chat_id: "", error: "" })
  const [waQr, setWaQr] = useState("")
  const [waSaving, setWaSaving] = useState(false)
  const [waTesting, setWaTesting] = useState(false)

  const [dcState, setDcState] = useState(DC_STATE.DISCONNECTED)
  const [dcInfo, setDcInfo] = useState({ bot_username: "", bot_invite_url: "", chat_id: "", error: "" })
  const [dcToken, setDcToken] = useState("")
  const [dcSaving, setDcSaving] = useState(false)
  const [dcTesting, setDcTesting] = useState(false)

  const [slState, setSlState] = useState(SL_STATE.DISCONNECTED)
  const [slInfo, setSlInfo] = useState({ bot_user_id: "", team_name: "", chat_id: "", error: "" })
  const [slBotToken, setSlBotToken] = useState("")
  const [slAppToken, setSlAppToken] = useState("")
  const [slSaving, setSlSaving] = useState(false)
  const [slTesting, setSlTesting] = useState(false)

  const fetchStatus = useCallback(async () => {
    try {
      const { data } = await http.get("/api/messaging/status")
      const tg = data.telegram || {}
      if (tg.connected && tg.chat_id) {
        setTgState(STATE.CONNECTED)
        setTgInfo({ bot_username: tg.bot_username || "", bot_url: tg.bot_url || "", chat_id: tg.chat_id || "" })
      } else if (tg.bot_username) {
        setTgState(STATE.AWAITING_START)
        setTgInfo({ bot_username: tg.bot_username || "", bot_url: tg.bot_url || "", chat_id: "" })
      } else {
        setTgState(STATE.DISCONNECTED)
        setTgInfo({ bot_username: "", bot_url: "", chat_id: "" })
      }

      const wa = data.whatsapp || {}
      if (wa.installed === false) {
        setWaState(WA_STATE.UNAVAILABLE)
        setWaInfo({ chat_id: "", error: wa.error || "neonize not installed" })
      } else if (wa.connected) {
        setWaState(WA_STATE.CONNECTED)
        setWaInfo({ chat_id: wa.chat_id || "", error: "" })
        setWaQr("")
      } else if (wa.pairing) {
        setWaState(WA_STATE.PAIRING)
      } else {
        setWaState(WA_STATE.DISCONNECTED)
        setWaInfo({ chat_id: "", error: "" })
        setWaQr("")
      }

      const dc = data.discord || {}
      if (dc.installed === false) {
        setDcState(DC_STATE.UNAVAILABLE)
        setDcInfo({ bot_username: "", bot_invite_url: "", chat_id: "", error: dc.error || "discord.py not installed" })
      } else if (dc.connected) {
        setDcState(DC_STATE.CONNECTED)
        setDcInfo({
          bot_username: dc.bot_username || "",
          bot_invite_url: dc.bot_invite_url || "",
          chat_id: dc.chat_id || "",
          error: "",
        })
      } else if (dc.bot_username) {
        setDcState(DC_STATE.AWAITING_DM)
        setDcInfo({
          bot_username: dc.bot_username || "",
          bot_invite_url: dc.bot_invite_url || "",
          chat_id: "",
          error: "",
        })
      } else {
        setDcState(DC_STATE.DISCONNECTED)
        setDcInfo({ bot_username: "", bot_invite_url: "", chat_id: "", error: "" })
      }

      const sl = data.slack || {}
      if (sl.installed === false) {
        setSlState(SL_STATE.UNAVAILABLE)
        setSlInfo({ bot_user_id: "", team_name: "", chat_id: "", error: sl.error || "slack-sdk not installed" })
      } else if (sl.connected) {
        setSlState(SL_STATE.CONNECTED)
        setSlInfo({
          bot_user_id: sl.bot_user_id || "",
          team_name: sl.team_name || "",
          chat_id: sl.chat_id || "",
          error: "",
        })
      } else if (sl.awaiting_dm) {
        setSlState(SL_STATE.AWAITING_DM)
        setSlInfo({
          bot_user_id: sl.bot_user_id || "",
          team_name: sl.team_name || "",
          chat_id: "",
          error: "",
        })
      } else {
        setSlState(SL_STATE.DISCONNECTED)
        setSlInfo({ bot_user_id: "", team_name: "", chat_id: "", error: "" })
      }
    } catch (e) {
      showSnackbar("Failed to load messaging status", "error")
    } finally {
      setLoading(false)
    }
  }, [showSnackbar])

  useEffect(() => { fetchStatus() }, [fetchStatus])

  useEffect(() => {
    const unsubs = [
      ws.on("telegram_connected", (msg) => {
        setTgState(STATE.CONNECTED)
        setTgInfo(prev => ({ ...prev, chat_id: msg.chat_id || prev.chat_id }))
        showSnackbar("Telegram connected — you'll receive notifications here", "success")
      }),
      ws.on("whatsapp_qr", (msg) => {
        setWaQr(msg.qr || "")
        setWaState(WA_STATE.PAIRING)
      }),
      ws.on("whatsapp_connected", (msg) => {
        setWaState(WA_STATE.CONNECTED)
        setWaInfo({ chat_id: msg.chat_id || "", error: "" })
        setWaQr("")
      }),
      ws.on("whatsapp_disconnected", () => {
        setWaState(WA_STATE.DISCONNECTED)
        setWaInfo({ chat_id: "", error: "" })
        setWaQr("")
      }),
      ws.on("discord_connected", (msg) => {
        setDcState(DC_STATE.CONNECTED)
        setDcInfo(prev => ({ ...prev, chat_id: msg.chat_id || prev.chat_id }))
        showSnackbar("Discord connected — you'll receive notifications here", "success")
      }),
      ws.on("slack_connected", (msg) => {
        setSlState(SL_STATE.CONNECTED)
        setSlInfo(prev => ({ ...prev, chat_id: msg.chat_id || prev.chat_id }))
        showSnackbar("Slack connected — you'll receive notifications here", "success")
      }),
    ]
    return () => { unsubs.forEach(fn => fn()) }
  }, [showSnackbar])

  const handleConnect = async () => {
    if (!botToken.trim()) {
      showSnackbar("Paste your bot token first", "warning")
      return
    }
    setSaving(true)
    setTgState(STATE.CONNECTING)
    try {
      const { data } = await http.post("/api/messaging/telegram/connect", { bot_token: botToken.trim() })
      setTgInfo({
        bot_username: data.bot_username || "",
        bot_url: data.bot_url || (data.bot_username ? `https://t.me/${data.bot_username}` : ""),
        chat_id: data.chat_id || "",
      })
      setTgState(data.chat_id ? STATE.CONNECTED : STATE.AWAITING_START)
      setBotToken("")
      showSnackbar(
        data.chat_id ? "Telegram connected" : "Bot configured — now send /start to your bot",
        "success",
      )
    } catch (e) {
      setTgState(STATE.DISCONNECTED)
      showSnackbar(e.response?.data?.detail || "Connect failed", "error")
    } finally {
      setSaving(false)
    }
  }

  const handleDisconnect = async () => {
    try {
      await http.post("/api/messaging/telegram/disconnect")
      setTgState(STATE.DISCONNECTED)
      setTgInfo({ bot_username: "", bot_url: "", chat_id: "" })
      showSnackbar("Telegram disconnected", "info")
    } catch (e) {
      showSnackbar(e.response?.data?.detail || "Disconnect failed", "error")
    }
  }

  const handleTest = async () => {
    setTesting(true)
    try {
      await http.post("/api/messaging/telegram/test")
      showSnackbar("Test message sent — check Telegram", "success")
    } catch (e) {
      showSnackbar(e.response?.data?.detail || "Test failed — did you send /start to the bot?", "error")
    } finally {
      setTesting(false)
    }
  }

  const handleWAConnect = async () => {
    setWaSaving(true)
    setWaQr("")
    try {
      await http.post("/api/messaging/whatsapp/connect")
      setWaState(WA_STATE.PAIRING)
      showSnackbar("Generating QR code — scan it from your phone", "info")
    } catch (e) {
      setWaState(WA_STATE.DISCONNECTED)
      showSnackbar(e.response?.data?.detail || "WhatsApp connect failed", "error")
    } finally {
      setWaSaving(false)
    }
  }

  const handleWADisconnect = async () => {
    try {
      await http.post("/api/messaging/whatsapp/disconnect")
      setWaState(WA_STATE.DISCONNECTED)
      setWaInfo({ chat_id: "", error: "" })
      setWaQr("")
      showSnackbar("WhatsApp disconnected", "info")
    } catch (e) {
      showSnackbar(e.response?.data?.detail || "Disconnect failed", "error")
    }
  }

  const handleWATest = async () => {
    setWaTesting(true)
    try {
      await http.post("/api/messaging/whatsapp/test")
      showSnackbar("Test message sent — check WhatsApp", "success")
    } catch (e) {
      showSnackbar(e.response?.data?.detail || "Test failed — is WhatsApp paired?", "error")
    } finally {
      setWaTesting(false)
    }
  }

  const handleDCConnect = async () => {
    if (!dcToken.trim()) {
      showSnackbar("Paste your Discord bot token first", "warning")
      return
    }
    setDcSaving(true)
    setDcState(DC_STATE.CONNECTING)
    try {
      const { data } = await http.post("/api/messaging/discord/connect", { bot_token: dcToken.trim() })
      setDcInfo({
        bot_username: data.bot_username || "",
        bot_invite_url: data.bot_invite_url || "",
        chat_id: data.chat_id || "",
        error: "",
      })
      setDcState(data.chat_id ? DC_STATE.CONNECTED : DC_STATE.AWAITING_DM)
      setDcToken("")
      showSnackbar(
        data.chat_id ? "Discord connected" : "Bot online — invite it and DM it to finish setup",
        "success",
      )
    } catch (e) {
      setDcState(DC_STATE.DISCONNECTED)
      showSnackbar(e.response?.data?.detail || "Discord connect failed", "error")
    } finally {
      setDcSaving(false)
    }
  }

  const handleDCDisconnect = async () => {
    try {
      await http.post("/api/messaging/discord/disconnect")
      setDcState(DC_STATE.DISCONNECTED)
      setDcInfo({ bot_username: "", bot_invite_url: "", chat_id: "", error: "" })
      showSnackbar("Discord disconnected", "info")
    } catch (e) {
      showSnackbar(e.response?.data?.detail || "Disconnect failed", "error")
    }
  }

  const handleDCTest = async () => {
    setDcTesting(true)
    try {
      await http.post("/api/messaging/discord/test")
      showSnackbar("Test message sent — check Discord", "success")
    } catch (e) {
      showSnackbar(e.response?.data?.detail || "Test failed — DM your bot first", "error")
    } finally {
      setDcTesting(false)
    }
  }

  const handleSLConnect = async () => {
    if (!slBotToken.trim() || !slAppToken.trim()) {
      showSnackbar("Paste both the bot token (xoxb-) and app-level token (xapp-)", "warning")
      return
    }
    setSlSaving(true)
    setSlState(SL_STATE.CONNECTING)
    try {
      const { data } = await http.post("/api/messaging/slack/connect", {
        bot_token: slBotToken.trim(),
        app_token: slAppToken.trim(),
      })
      setSlInfo({
        bot_user_id: data.bot_user_id || "",
        team_name: data.team_name || "",
        chat_id: data.chat_id || "",
        error: "",
      })
      setSlState(data.chat_id ? SL_STATE.CONNECTED : SL_STATE.AWAITING_DM)
      setSlBotToken("")
      setSlAppToken("")
      showSnackbar(
        data.chat_id ? "Slack connected" : "Bot online — DM it in Slack to finish setup",
        "success",
      )
    } catch (e) {
      setSlState(SL_STATE.DISCONNECTED)
      showSnackbar(e.response?.data?.detail || "Slack connect failed", "error")
    } finally {
      setSlSaving(false)
    }
  }

  const handleSLDisconnect = async () => {
    try {
      await http.post("/api/messaging/slack/disconnect")
      setSlState(SL_STATE.DISCONNECTED)
      setSlInfo({ bot_user_id: "", team_name: "", chat_id: "", error: "" })
      showSnackbar("Slack disconnected", "info")
    } catch (e) {
      showSnackbar(e.response?.data?.detail || "Disconnect failed", "error")
    }
  }

  const handleSLTest = async () => {
    setSlTesting(true)
    try {
      await http.post("/api/messaging/slack/test")
      showSnackbar("Test message sent — check Slack", "success")
    } catch (e) {
      showSnackbar(e.response?.data?.detail || "Test failed — DM your bot first", "error")
    } finally {
      setSlTesting(false)
    }
  }

  const statusChip = () => {
    if (tgState === STATE.CONNECTED) return <Chip icon={<CheckCircleIcon />} label="Connected" color="success" size="small" />
    if (tgState === STATE.AWAITING_START) return <Chip label="Awaiting /start" color="warning" size="small" />
    if (tgState === STATE.CONNECTING) return <Chip label="Connecting…" size="small" />
    return <Chip label="Not connected" size="small" />
  }

  const waStatusChip = () => {
    if (waState === WA_STATE.CONNECTED) return <Chip icon={<CheckCircleIcon />} label="Connected" color="success" size="small" />
    if (waState === WA_STATE.PAIRING) return <Chip label="Scan QR code" color="warning" size="small" />
    if (waState === WA_STATE.UNAVAILABLE) return <Chip label="Unavailable" size="small" />
    return <Chip label="Not connected" size="small" />
  }

  const dcStatusChip = () => {
    if (dcState === DC_STATE.CONNECTED) return <Chip icon={<CheckCircleIcon />} label="Connected" color="success" size="small" />
    if (dcState === DC_STATE.AWAITING_DM) return <Chip label="DM your bot" color="warning" size="small" />
    if (dcState === DC_STATE.CONNECTING) return <Chip label="Connecting…" size="small" />
    if (dcState === DC_STATE.UNAVAILABLE) return <Chip label="Unavailable" size="small" />
    return <Chip label="Not connected" size="small" />
  }

  const slStatusChip = () => {
    if (slState === SL_STATE.CONNECTED) return <Chip icon={<CheckCircleIcon />} label="Connected" color="success" size="small" />
    if (slState === SL_STATE.AWAITING_DM) return <Chip label="DM your bot" color="warning" size="small" />
    if (slState === SL_STATE.CONNECTING) return <Chip label="Connecting…" size="small" />
    if (slState === SL_STATE.UNAVAILABLE) return <Chip label="Unavailable" size="small" />
    return <Chip label="Not connected" size="small" />
  }

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* ── Header ────────────────────────────────────────── */}
      <Box sx={{
        px: 3, py: 2, flexShrink: 0,
        borderBottom: 1, borderColor: "divider",
        background: (t) => t.palette.mode === "dark"
          ? "linear-gradient(135deg, rgba(88,101,242,0.12) 0%, rgba(30,28,26,1) 100%)"
          : "linear-gradient(135deg, rgba(88,101,242,0.08) 0%, rgba(255,255,255,1) 100%)",
      }}>
        <Stack direction="row" spacing={1.5} alignItems="center">
          <PhoneIphoneIcon sx={{ color: "#5865F2", fontSize: 26 }} />
          <Box>
            <Typography variant="h5" sx={{ fontWeight: 700, letterSpacing: -0.3 }}>
              Messaging
            </Typography>
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              Connect a messaging app to control ViralMint from your phone and receive job notifications
            </Typography>
          </Box>
        </Stack>
      </Box>

      {/* ── Scrollable content ───────────────────────────── */}
      <Box sx={{ flex: 1, overflow: "auto", p: { xs: 2, md: 3 } }}>
      {loading ? (
        <Box sx={{ py: 8, display: "flex", justifyContent: "center" }}>
          <CircularProgress />
        </Box>
      ) : (
      <Grid container spacing={3} alignItems="stretch">
      {/* Telegram */}
      <Grid size={{ xs: 12, md: 6 }} sx={{ display: "flex" }}>
      <Card sx={{ width: "100%", display: "flex", flexDirection: "column" }}>
        <CardContent sx={{ flex: 1, display: "flex", flexDirection: "column" }}>
          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
            <TelegramIcon sx={{ fontSize: 32, color: "#229ED9" }} />
            <Box sx={{ flex: 1 }}>
              <Typography variant="h6" sx={{ fontWeight: 600 }}>Telegram</Typography>
              <Typography variant="caption" color="text.secondary">
                Two-way chat with your ViralMint planner and job notifications
              </Typography>
            </Box>
            {statusChip()}
          </Stack>

          <Divider sx={{ my: 2 }} />

          {tgState === STATE.CONNECTED && (
            <Stack spacing={2}>
              <Alert severity="success" variant="outlined">
                Connected to{" "}
                {tgInfo.bot_url ? (
                  <Link href={tgInfo.bot_url} target="_blank" rel="noreferrer" sx={{ fontWeight: 600 }}>
                    @{tgInfo.bot_username}
                  </Link>
                ) : (
                  <strong>@{tgInfo.bot_username}</strong>
                )}
                . Send any message to your bot to chat with the planner.
              </Alert>
              <Stack direction="row" spacing={1.5}>
                <Button
                  variant="contained"
                  startIcon={testing ? <CircularProgress size={16} color="inherit" /> : <SendIcon />}
                  onClick={handleTest}
                  disabled={testing}
                >
                  Send test message
                </Button>
                <Button
                  variant="outlined"
                  color="error"
                  startIcon={<LogoutIcon />}
                  onClick={handleDisconnect}
                >
                  Disconnect
                </Button>
              </Stack>
            </Stack>
          )}

          {tgState === STATE.AWAITING_START && (
            <Stack spacing={2}>
              <Alert severity="info" variant="outlined">
                Bot configured. Now open{" "}
                {tgInfo.bot_url ? (
                  <Link href={tgInfo.bot_url} target="_blank" rel="noreferrer" sx={{ fontWeight: 600 }}>
                    @{tgInfo.bot_username}
                  </Link>
                ) : (
                  <strong>your bot</strong>
                )}
                {" "}in Telegram and send <code>/start</code> to activate notifications.
              </Alert>
              <Stack direction="row" spacing={1.5}>
                <Button variant="outlined" onClick={fetchStatus}>Refresh status</Button>
                <Button variant="outlined" color="error" startIcon={<LogoutIcon />} onClick={handleDisconnect}>
                  Cancel
                </Button>
              </Stack>
            </Stack>
          )}

          {(tgState === STATE.DISCONNECTED || tgState === STATE.CONNECTING) && (
            <Stack spacing={2}>
              <Typography variant="body2" color="text.secondary">
                Create a bot via{" "}
                <Link href="https://t.me/BotFather" target="_blank" rel="noreferrer">@BotFather</Link>
                {" "}on Telegram (send <code>/newbot</code>), then paste the bot token below.
              </Typography>
              <TextField
                label="Bot token"
                type="password"
                fullWidth
                size="small"
                placeholder="123456789:ABCdef..."
                value={botToken}
                onChange={(e) => setBotToken(e.target.value)}
                disabled={saving}
                autoComplete="off"
              />
              <Box>
                <Button
                  variant="contained"
                  onClick={handleConnect}
                  disabled={saving || !botToken.trim()}
                  startIcon={saving ? <CircularProgress size={16} color="inherit" /> : null}
                >
                  {saving ? "Connecting…" : "Connect"}
                </Button>
              </Box>
            </Stack>
          )}
        </CardContent>
      </Card>
      </Grid>

      {/* WhatsApp */}
      <Grid size={{ xs: 12, md: 6 }} sx={{ display: "flex" }}>
      <Card sx={{ width: "100%", display: "flex", flexDirection: "column" }}>
        <CardContent sx={{ flex: 1, display: "flex", flexDirection: "column" }}>
          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
            <WhatsAppIcon sx={{ fontSize: 32, color: "#25D366" }} />
            <Box sx={{ flex: 1 }}>
              <Typography variant="h6" sx={{ fontWeight: 600 }}>WhatsApp</Typography>
              <Typography variant="caption" color="text.secondary">
                Two-way chat + notifications via QR-scan device pairing
              </Typography>
            </Box>
            {waStatusChip()}
          </Stack>

          <Divider sx={{ my: 2 }} />

          {waState === WA_STATE.UNAVAILABLE && (
            <Alert severity="warning" variant="outlined">
              WhatsApp support requires the <code>neonize</code> Python package, which ships a
              native library. Install dependencies with <code>pip install -r requirements.txt</code>,
              then restart the app.
              {waInfo.error && (
                <Typography variant="caption" display="block" sx={{ mt: 1, opacity: 0.8 }}>
                  Details: {waInfo.error}
                </Typography>
              )}
            </Alert>
          )}

          {waState === WA_STATE.CONNECTED && (
            <Stack spacing={2}>
              <Alert severity="success" variant="outlined">
                WhatsApp paired. Message yourself on WhatsApp to chat with the planner — job
                notifications will arrive in the same thread.
              </Alert>
              <Stack direction="row" spacing={1.5}>
                <Button
                  variant="contained"
                  startIcon={waTesting ? <CircularProgress size={16} color="inherit" /> : <SendIcon />}
                  onClick={handleWATest}
                  disabled={waTesting}
                  sx={{ bgcolor: "#25D366", "&:hover": { bgcolor: "#128C7E" } }}
                >
                  Send test message
                </Button>
                <Button
                  variant="outlined"
                  color="error"
                  startIcon={<LogoutIcon />}
                  onClick={handleWADisconnect}
                >
                  Disconnect
                </Button>
              </Stack>
            </Stack>
          )}

          {(waState === WA_STATE.DISCONNECTED || waState === WA_STATE.PAIRING) && (
            <Stack spacing={2}>
              <Typography variant="body2" color="text.secondary">
                Get scout, download, and upload alerts on WhatsApp — and chat with ViralMint
                straight from your phone. Pairs in seconds, just like WhatsApp Web.
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Click Connect to generate a QR code, then open WhatsApp → Settings → Linked
                Devices → Link a Device to scan it.
              </Typography>
              <Box>
                <Button
                  variant="contained"
                  startIcon={
                    (waSaving || waState === WA_STATE.PAIRING)
                      ? <CircularProgress size={16} color="inherit" />
                      : <QrCodeIcon />
                  }
                  onClick={handleWAConnect}
                  disabled={waSaving || waState === WA_STATE.PAIRING}
                  sx={{ bgcolor: "#25D366", "&:hover": { bgcolor: "#128C7E" } }}
                >
                  {waState === WA_STATE.PAIRING
                    ? "Waiting for scan…"
                    : waSaving ? "Generating QR…" : "Connect WhatsApp"}
                </Button>
              </Box>
            </Stack>
          )}
        </CardContent>
      </Card>
      </Grid>

      {/* Discord */}
      <Grid size={{ xs: 12, md: 6 }} sx={{ display: "flex" }}>
      <Card sx={{ width: "100%", display: "flex", flexDirection: "column" }}>
        <CardContent sx={{ flex: 1, display: "flex", flexDirection: "column" }}>
          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
            <ChatBubbleIcon sx={{ fontSize: 32, color: "#5865F2" }} />
            <Box sx={{ flex: 1 }}>
              <Typography variant="h6" sx={{ fontWeight: 600 }}>Discord</Typography>
              <Typography variant="caption" color="text.secondary">
                DM your bot to chat with the planner and get job notifications
              </Typography>
            </Box>
            {dcStatusChip()}
          </Stack>

          <Divider sx={{ my: 2 }} />

          {dcState === DC_STATE.UNAVAILABLE && (
            <Alert severity="warning" variant="outlined">
              Discord support requires the <code>discord.py</code> Python package. Install
              dependencies with <code>pip install -r requirements.txt</code>, then restart the app.
              {dcInfo.error && (
                <Typography variant="caption" display="block" sx={{ mt: 1, opacity: 0.8 }}>
                  Details: {dcInfo.error}
                </Typography>
              )}
            </Alert>
          )}

          {dcState === DC_STATE.CONNECTED && (
            <Stack spacing={2}>
              <Alert severity="success" variant="outlined">
                Connected as <strong>{dcInfo.bot_username}</strong>. DM your bot on Discord to
                chat with the planner — notifications arrive in the same thread.
              </Alert>
              <Stack direction="row" spacing={1.5}>
                <Button
                  variant="contained"
                  startIcon={dcTesting ? <CircularProgress size={16} color="inherit" /> : <SendIcon />}
                  onClick={handleDCTest}
                  disabled={dcTesting}
                  sx={{ bgcolor: "#5865F2", "&:hover": { bgcolor: "#4752C4" } }}
                >
                  Send test message
                </Button>
                <Button
                  variant="outlined"
                  color="error"
                  startIcon={<LogoutIcon />}
                  onClick={handleDCDisconnect}
                >
                  Disconnect
                </Button>
              </Stack>
            </Stack>
          )}

          {dcState === DC_STATE.AWAITING_DM && (
            <Stack spacing={2}>
              <Alert severity="info" variant="outlined">
                Bot online as <strong>{dcInfo.bot_username}</strong>.
                {dcInfo.bot_invite_url && (
                  <>
                    {" "}
                    <Link href={dcInfo.bot_invite_url} target="_blank" rel="noreferrer" sx={{ fontWeight: 600 }}>
                      Invite it to a server
                    </Link>
                    {" "}if you haven't yet, then
                  </>
                )}
                {" "}DM the bot from any Discord client to activate notifications.
              </Alert>
              <Stack direction="row" spacing={1.5}>
                <Button variant="outlined" onClick={fetchStatus}>Refresh status</Button>
                <Button variant="outlined" color="error" startIcon={<LogoutIcon />} onClick={handleDCDisconnect}>
                  Cancel
                </Button>
              </Stack>
            </Stack>
          )}

          {(dcState === DC_STATE.DISCONNECTED || dcState === DC_STATE.CONNECTING) && (
            <Stack spacing={2}>
              <Typography variant="body2" color="text.secondary">
                Create a bot at{" "}
                <Link href="https://discord.com/developers/applications" target="_blank" rel="noreferrer">
                  discord.com/developers/applications
                </Link>
                {" "}— click <strong>New Application</strong> → <strong>Bot</strong> → copy the token and paste below.
                DMs work without any privileged intents.
              </Typography>
              <TextField
                label="Bot token"
                type="password"
                fullWidth
                size="small"
                placeholder="MTI..."
                value={dcToken}
                onChange={(e) => setDcToken(e.target.value)}
                disabled={dcSaving}
                autoComplete="off"
              />
              <Box>
                <Button
                  variant="contained"
                  onClick={handleDCConnect}
                  disabled={dcSaving || !dcToken.trim()}
                  startIcon={dcSaving ? <CircularProgress size={16} color="inherit" /> : null}
                  sx={{ bgcolor: "#5865F2", "&:hover": { bgcolor: "#4752C4" } }}
                >
                  {dcSaving ? "Connecting…" : "Connect"}
                </Button>
              </Box>
            </Stack>
          )}
        </CardContent>
      </Card>
      </Grid>

      {/* Slack */}
      <Grid size={{ xs: 12, md: 6 }} sx={{ display: "flex" }}>
      <Card sx={{ width: "100%", display: "flex", flexDirection: "column" }}>
        <CardContent sx={{ flex: 1, display: "flex", flexDirection: "column" }}>
          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
            <TagIcon sx={{ fontSize: 32, color: "#611F69" }} />
            <Box sx={{ flex: 1 }}>
              <Typography variant="h6" sx={{ fontWeight: 600 }}>Slack</Typography>
              <Typography variant="caption" color="text.secondary">
                Socket Mode — no public URL needed. DM your bot to chat and get notifications.
              </Typography>
            </Box>
            {slStatusChip()}
          </Stack>

          <Divider sx={{ my: 2 }} />

          {slState === SL_STATE.UNAVAILABLE && (
            <Alert severity="warning" variant="outlined">
              Slack support requires the <code>slack-sdk</code> Python package. Install
              dependencies with <code>pip install -r requirements.txt</code>, then restart the app.
              {slInfo.error && (
                <Typography variant="caption" display="block" sx={{ mt: 1, opacity: 0.8 }}>
                  Details: {slInfo.error}
                </Typography>
              )}
            </Alert>
          )}

          {slState === SL_STATE.CONNECTED && (
            <Stack spacing={2}>
              <Alert severity="success" variant="outlined">
                Connected to <strong>{slInfo.team_name || "your Slack workspace"}</strong>. DM
                your bot in Slack to chat with the planner — notifications arrive in the same thread.
              </Alert>
              <Stack direction="row" spacing={1.5}>
                <Button
                  variant="contained"
                  startIcon={slTesting ? <CircularProgress size={16} color="inherit" /> : <SendIcon />}
                  onClick={handleSLTest}
                  disabled={slTesting}
                  sx={{ bgcolor: "#611F69", "&:hover": { bgcolor: "#4A154B" } }}
                >
                  Send test message
                </Button>
                <Button
                  variant="outlined"
                  color="error"
                  startIcon={<LogoutIcon />}
                  onClick={handleSLDisconnect}
                >
                  Disconnect
                </Button>
              </Stack>
            </Stack>
          )}

          {slState === SL_STATE.AWAITING_DM && (
            <Stack spacing={2}>
              <Alert severity="info" variant="outlined">
                Bot online{slInfo.team_name ? ` in ${slInfo.team_name}` : ""}. Open Slack and
                DM your bot (search its name in the sidebar) to activate notifications.
              </Alert>
              <Stack direction="row" spacing={1.5}>
                <Button variant="outlined" onClick={fetchStatus}>Refresh status</Button>
                <Button variant="outlined" color="error" startIcon={<LogoutIcon />} onClick={handleSLDisconnect}>
                  Cancel
                </Button>
              </Stack>
            </Stack>
          )}

          {(slState === SL_STATE.DISCONNECTED || slState === SL_STATE.CONNECTING) && (
            <Stack spacing={2}>
              <Typography variant="body2" color="text.secondary">
                Create a Slack app at{" "}
                <Link href="https://api.slack.com/apps" target="_blank" rel="noreferrer">api.slack.com/apps</Link>.
                Enable <strong>Socket Mode</strong>, generate an app-level token with{" "}
                <code>connections:write</code>, and install the bot to your workspace with scopes{" "}
                <code>chat:write</code>, <code>im:history</code>, <code>im:read</code>,{" "}
                <code>im:write</code>, <code>users:read</code>. Then subscribe to the{" "}
                <code>message.im</code> event.
              </Typography>
              <TextField
                label="Bot token (xoxb-…)"
                type="password"
                fullWidth
                size="small"
                placeholder="xoxb-..."
                value={slBotToken}
                onChange={(e) => setSlBotToken(e.target.value)}
                disabled={slSaving}
                autoComplete="off"
              />
              <TextField
                label="App-level token (xapp-…)"
                type="password"
                fullWidth
                size="small"
                placeholder="xapp-..."
                value={slAppToken}
                onChange={(e) => setSlAppToken(e.target.value)}
                disabled={slSaving}
                autoComplete="off"
              />
              <Box>
                <Button
                  variant="contained"
                  onClick={handleSLConnect}
                  disabled={slSaving || !slBotToken.trim() || !slAppToken.trim()}
                  startIcon={slSaving ? <CircularProgress size={16} color="inherit" /> : null}
                  sx={{ bgcolor: "#611F69", "&:hover": { bgcolor: "#4A154B" } }}
                >
                  {slSaving ? "Connecting…" : "Connect"}
                </Button>
              </Box>
            </Stack>
          )}
        </CardContent>
      </Card>
      </Grid>
      </Grid>
      )}
      </Box>{/* end scrollable content */}

      {/* QR code dialog — opens on top without altering the page layout */}
      <Dialog
        open={waState === WA_STATE.PAIRING}
        onClose={handleWADisconnect}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle sx={{ pr: 6 }}>
          Scan to pair WhatsApp
          <IconButton
            onClick={handleWADisconnect}
            size="small"
            sx={{ position: "absolute", right: 8, top: 8 }}
            aria-label="Close"
          >
            <CloseIcon fontSize="small" />
          </IconButton>
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} alignItems="center">
            <Typography variant="body2" color="text.secondary" sx={{ textAlign: "center" }}>
              On your phone, open <strong>WhatsApp → Settings → Linked Devices → Link a Device</strong>,
              then scan this code.
            </Typography>
            {waQr ? (
              <Box sx={{ p: 2, bgcolor: "#fff", borderRadius: 2, boxShadow: 1 }}>
                <QRCodeCanvas value={waQr} size={240} level="M" includeMargin={false} />
              </Box>
            ) : (
              <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, py: 6 }}>
                <CircularProgress size={20} />
                <Typography variant="body2" color="text.secondary">Waiting for QR code…</Typography>
              </Box>
            )}
            <Typography variant="caption" color="text.secondary" sx={{ textAlign: "center" }}>
              The code refreshes automatically. This dialog closes once pairing completes.
            </Typography>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleWADisconnect} color="error" startIcon={<LogoutIcon />}>
            Cancel pairing
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
