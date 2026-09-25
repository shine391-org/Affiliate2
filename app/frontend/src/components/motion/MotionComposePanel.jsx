// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { useState, useEffect, useRef } from "react"
import {
  Box, Stack, Typography, TextField, Button, ToggleButton, ToggleButtonGroup,
  LinearProgress, Alert, IconButton, Divider, Chip, Tooltip,
} from "@mui/material"
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesomeOutlined"
import CloseIcon from "@mui/icons-material/CloseOutlined"
import AttachFileIcon from "@mui/icons-material/AttachFileOutlined"
import http from "../../api/http"
import useAppStore from "../../store/appStore"

const ASPECTS = ["9:16", "16:9", "1:1"]
const DURATIONS = [5, 10, 15, 30]

// Openers for the empty state. Static and local: an endpoint would be a network
// call to answer a question that has the same answer every time.
const STARTERS = [
  "Hook kinetic typography mạnh cho video về thói quen buổi sáng",
  "Thẻ số liệu xoay quanh “73% người xem rời đi trong 3 giây”",
  "Teaser ra mắt ứng dụng Northwind — tối và điện ảnh",
  "Listicle 3 ý về vì sao phần lớn dự án phụ bị đình trệ",
]

/**
 * AI Compose — describe a video, the configured model writes the composition
 * into the studio project, and the studio reloads onto it.
 *
 * This deliberately does NOT hold a conversation. Composing rewrites one file,
 * so the useful unit is a brief and a result; the refine box edits what is
 * currently live rather than replaying a thread. Iterating is safe because
 * every composition it replaces is archived rather than overwritten.
 */
export default function MotionComposePanel({ aspect, setAspect, onComposed, onNeedInstall, onClose }) {
  const showSnackbar = useAppStore((s) => s.showSnackbar)
  const [topic, setTopic] = useState("")
  const [duration, setDuration] = useState(10)
  const [accent, setAccent] = useState("#ffd60a")
  const [job, setJob] = useState(null)        // {status, step, pct, error}
  const [composed, setComposed] = useState(false)
  const [staged, setStaged] = useState([])
  const fileRef = useRef(null)
  const pollRef = useRef(null)

  useEffect(() => () => { if (pollRef.current) clearTimeout(pollRef.current) }, [])

  const poll = (id) => {
    const tick = async () => {
      try {
        const { data } = await http.get(`/api/jobs/${id}`)
        setJob({ status: data.status, step: data.current_step,
                 pct: data.progress_pct, error: data.error_message })
        if (["success", "failed", "cancelled"].includes(data.status)) {
          pollRef.current = null
          if (data.status === "success") {
            setComposed(true)
            showSnackbar("Bố cục đã sẵn sàng — studio đang tải lại nội dung.", "success")
            onComposed?.()
          }
          return
        }
      } catch { /* a blip shouldn't abandon a job that is still running */ }
      pollRef.current = setTimeout(tick, 2000)
    }
    pollRef.current = setTimeout(tick, 1500)
  }

  const compose = async (instruction) => {
    const brief = instruction
      ? { instruction, aspect_ratio: aspect, duration_seconds: duration, accent }
      : { topic: topic.trim(), aspect_ratio: aspect, duration_seconds: duration, accent }
    if (!instruction && !brief.topic) return
    setJob({ status: "pending", step: "Đang bắt đầu…", pct: 0 })
    setComposed(false)
    try {
      const { data } = await http.post("/api/generate/motion/studio/author", brief)
      if (data?.error_code === "hyperframes_not_installed") { onNeedInstall?.(); return }
      poll(data.job_id)
    } catch (e) {
      setJob({ status: "failed", error: e?.response?.data?.detail || e.message })
    }
  }

  const attach = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ""
    if (!file) return
    const form = new FormData()
    form.append("file", file)
    try {
      const { data } = await http.post("/api/generate/motion/studio/assets", form)
      setStaged((s) => [...s, data])
      showSnackbar(`${file.name} đã được chuẩn bị — sẽ được đưa vào bố cục tiếp theo.`, "success")
    } catch (err) {
      showSnackbar(err?.response?.data?.detail || "Không thể chuẩn bị tệp này", "error")
    }
  }

  const running = !!job && !["success", "failed", "cancelled"].includes(job.status)

  return (
    <Box sx={{
      height: "100%", display: "flex", flexDirection: "column",
      bgcolor: "background.paper", borderLeft: "1px solid", borderColor: "divider",
    }}>
      <Stack direction="row" alignItems="center" spacing={1}
        sx={{ px: 2, py: 1.25, borderBottom: "1px solid", borderColor: "divider" }}>
        <AutoAwesomeIcon sx={{ fontSize: 18, color: "primary.main" }} />
        <Typography variant="subtitle2" sx={{ fontWeight: 700, flexGrow: 1 }}>Dựng bằng AI</Typography>
        <IconButton size="small" onClick={onClose} aria-label="Đóng Dựng bằng AI">
          <CloseIcon sx={{ fontSize: 18 }} />
        </IconButton>
      </Stack>

      <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
        <Typography variant="caption" sx={{ display: "block", color: "text.secondary", mb: 1.5 }}>
          Mô tả video. Mô hình AI đã cấu hình sẽ tạo bố cục và
          studio sẽ mở nó — sau đó có thể chỉnh thủ công.
        </Typography>

        <TextField
          fullWidth multiline minRows={3} size="small" value={topic}
          onChange={(e) => setTopic(e.target.value)} disabled={running}
          placeholder="ví dụ: hook mạnh về vì sao phần lớn dự án phụ bị đình trệ"
          sx={{ mb: 1.5 }}
        />

        {!topic && !job && (
          <Stack spacing={0.75} sx={{ mb: 2 }}>
            {STARTERS.map((s) => (
              <Chip key={s} label={s} size="small" variant="outlined"
                onClick={() => setTopic(s)}
                sx={{
                  height: "auto", justifyContent: "flex-start",
                  "& .MuiChip-label": { whiteSpace: "normal", py: 0.6, fontSize: "0.74rem" },
                }} />
            ))}
          </Stack>
        )}

        <Typography variant="caption" sx={{ color: "text.secondary" }}>Định dạng</Typography>
        <ToggleButtonGroup exclusive size="small" value={aspect} disabled={running}
          onChange={(_, v) => v && setAspect(v)} sx={{ display: "flex", mb: 1.5, mt: 0.5 }}>
          {ASPECTS.map((a) => (
            <ToggleButton key={a} value={a} sx={{ flex: 1, textTransform: "none" }}>{a}</ToggleButton>
          ))}
        </ToggleButtonGroup>

        <Typography variant="caption" sx={{ color: "text.secondary" }}>Thời lượng</Typography>
        <ToggleButtonGroup exclusive size="small" value={duration} disabled={running}
          onChange={(_, v) => v && setDuration(v)} sx={{ display: "flex", mb: 1.5, mt: 0.5 }}>
          {DURATIONS.map((d) => (
            <ToggleButton key={d} value={d} sx={{ flex: 1, textTransform: "none" }}>{d}s</ToggleButton>
          ))}
        </ToggleButtonGroup>

        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
          <Typography variant="caption" sx={{ color: "text.secondary" }}>Màu nhấn</Typography>
          <input type="color" value={accent} disabled={running} aria-label="Màu nhấn"
            onChange={(e) => setAccent(e.target.value)}
            style={{ width: 38, height: 26, border: 0, background: "none", cursor: "pointer" }} />
          <Box sx={{ flexGrow: 1 }} />
          <Tooltip title="Đính kèm ảnh, video hoặc âm thanh để đưa vào">
            <span>
              <Button size="small" startIcon={<AttachFileIcon sx={{ fontSize: 16 }} />}
                disabled={running} onClick={() => fileRef.current?.click()}>
                Attach
              </Button>
            </span>
          </Tooltip>
          <input ref={fileRef} type="file" hidden onChange={attach}
            accept="image/*,video/*,audio/*" />
        </Stack>

        {staged.length > 0 && (
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
            {staged.map((a) => (
              <Chip key={a.file} size="small" variant="outlined" label={`${a.file} · ${a.type}`} />
            ))}
          </Stack>
        )}

        <Button fullWidth variant="contained" startIcon={<AutoAwesomeIcon />}
          disabled={running || !topic.trim()} onClick={() => compose(null)}>
          {running ? "Đang tạo…" : "Tạo composition"}
        </Button>

        {running && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              {job.step || "Đang xử lý…"}
            </Typography>
            <LinearProgress variant={job.pct ? "determinate" : "indeterminate"}
              value={job.pct || 0} sx={{ mt: 0.5, height: 6, borderRadius: 3 }} />
          </Box>
        )}

        {job?.status === "failed" && (
          <Alert severity="error" sx={{ mt: 2, fontSize: "0.78rem", whiteSpace: "pre-wrap" }}>
            {job.error || "Tạo composition thất bại."}
          </Alert>
        )}

        {composed && (
          <>
            <Divider sx={{ my: 2.5 }} />
            <Typography variant="caption" sx={{ display: "block", color: "text.secondary", mb: 1 }}>
              Tinh chỉnh — thao tác này viết lại composition đang chạy. Bản trước
              vẫn được giữ trong mục Comps của studio.
            </Typography>
            <RefineBox onSubmit={compose} disabled={running} />
          </>
        )}
      </Box>
    </Box>
  )
}

function RefineBox({ onSubmit, disabled }) {
  const [text, setText] = useState("")
  const send = () => {
    if (!text.trim()) return
    onSubmit(text.trim())
    setText("")
  }
  return (
    <Stack spacing={1}>
      <TextField fullWidth multiline minRows={2} size="small" value={text} disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        placeholder="ví dụ: làm tiêu đề lớn hơn và chậm phần kết" />
      <Button variant="outlined" size="small" disabled={disabled || !text.trim()} onClick={send}>
        Apply change
      </Button>
    </Stack>
  )
}
