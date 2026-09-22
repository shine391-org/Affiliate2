import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material"
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined"
import RefreshIcon from "@mui/icons-material/RefreshOutlined"
import OpenInNewIcon from "@mui/icons-material/OpenInNew"
import http from "../../api/http"
import ActionControls from "./ActionControls"
import PublishGate from "./PublishGate"

const FILTERS = [
  "ALL",
  "REVIEW_PENDING",
  "APPROVED_FOR_REMAKE",
  "REMAKE_REVIEW",
  "APPROVED",
  "REWORK",
  "REJECTED",
]

const statusLabel = (value) => (value || "").replaceAll("_", " ")

const statusColor = (status) => {
  if (status === "APPROVED" || status === "APPROVED_FOR_REMAKE") return "success"
  if (status === "REJECTED") return "error"
  if (status === "REWORK") return "warning"
  if (status === "REMAKE_REVIEW") return "info"
  return "default"
}

function SummaryTile({ label, value }) {
  return (
    <Card variant="outlined" sx={{ minWidth: 130, flex: "1 1 130px" }}>
      <CardContent sx={{ py: 1.4, "&:last-child": { pb: 1.4 } }}>
        <Typography variant="caption" color="text.secondary">{label}</Typography>
        <Typography variant="h5" sx={{ fontWeight: 800, mt: 0.25 }}>{value ?? 0}</Typography>
      </CardContent>
    </Card>
  )
}

function InfoBlock({ title, children }) {
  return (
    <Box>
      <Typography variant="subtitle2" sx={{ fontWeight: 800, mb: 0.75 }}>{title}</Typography>
      <Box sx={{ color: "text.secondary", fontSize: "0.9rem", lineHeight: 1.55 }}>{children}</Box>
    </Box>
  )
}

function RiskChips({ risks }) {
  if (!risks?.length) return <Chip size="small" label="No elevated risk" />
  return (
    <Stack direction="row" spacing={0.75} useFlexGap flexWrap="wrap">
      {risks.map((r) => <Chip key={r} size="small" label={r} color="warning" variant="outlined" />)}
    </Stack>
  )
}

function CandidateList({ candidates, selectedKey, onSelect }) {
  if (!candidates.length) {
    return <Alert severity="info">No candidates match this filter.</Alert>
  }
  return (
    <Stack spacing={1}>
      {candidates.map((c) => {
        const key = `${c.platform}:${c.content_id}`
        const selected = key === selectedKey
        return (
          <Card
            key={key}
            variant="outlined"
            onClick={() => onSelect(c)}
            sx={{
              cursor: "pointer",
              borderColor: selected ? "primary.main" : "divider",
              bgcolor: selected ? "action.selected" : "background.paper",
              transition: "120ms ease",
              "&:hover": { borderColor: "primary.light", transform: "translateY(-1px)" },
            }}
          >
            <CardContent sx={{ p: 1.5, "&:last-child": { pb: 1.5 } }}>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.75 }}>
                <Typography variant="caption" sx={{ fontWeight: 800 }}>#{c.rank}</Typography>
                <Chip size="small" label={c.platform} variant="outlined" />
                <Chip size="small" label={statusLabel(c.review_status)} color={statusColor(c.review_status)} />
                {c.remake_exists && <Chip size="small" label="FINAL" color="secondary" variant="outlined" />}
              </Stack>
              <Typography sx={{ fontWeight: 700, fontSize: "0.9rem", lineHeight: 1.35 }}>
                {c.title}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {c.creator || "Unknown creator"} · Radar {c.radar_score ?? "-"}
              </Typography>
              <Stack direction="row" spacing={0.75} sx={{ mt: 1 }} useFlexGap flexWrap="wrap">
                <Chip size="small" label={`Localization ${c.localization_potential || "-"}`} />
                <Chip size="small" label={`Difficulty ${c.remake_difficulty || "-"}`} />
              </Stack>
            </CardContent>
          </Card>
        )
      })}
    </Stack>
  )
}

function VideoPanel({ title, src, available }) {
  return (
    <Box sx={{ flex: "1 1 320px", minWidth: 0 }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 800, mb: 1 }}>{title}</Typography>
      {available ? (
        <Box
          component="video"
          src={src}
          controls
          preload="metadata"
          sx={{ width: "100%", maxHeight: 520, bgcolor: "#000", borderRadius: 2 }}
        />
      ) : (
        <Alert severity="info">Not available locally.</Alert>
      )}
    </Box>
  )
}

export default function ReviewQueue() {
  const [filter, setFilter] = useState("ALL")
  const [payload, setPayload] = useState({ summary: {}, candidates: [] })
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState("")
  const [error, setError] = useState("")
  const [actionType, setActionType] = useState("remake")
  const [voiceId, setVoiceId] = useState("vi-VN-NamMinhNeural")
  const [targetDuration, setTargetDuration] = useState("20-35s")
  const [translateMode, setTranslateMode] = useState("full_dub")
  const [actionJob, setActionJob] = useState(null)
  const [actionBusy, setActionBusy] = useState(false)

  useEffect(() => {
    document.title = "Review Queue · ViralMint"
  }, [])

  const loadQueue = useCallback(async (nextFilter = filter) => {
    setLoading(true)
    setError("")
    try {
      const params = nextFilter === "ALL" ? {} : { status: nextFilter }
      const { data } = await http.get("/api/review/candidates", { params })
      setPayload(data)
      return data
    } catch (e) {
      setError(e.response?.data?.detail || "Could not load review queue.")
      return null
    } finally {
      setLoading(false)
    }
  }, [filter])

  const loadActionStatus = useCallback(async (candidate) => {
    if (!candidate) {
      setActionJob(null)
      return null
    }
    try {
      const { data } = await http.get(
        `/api/review/candidates/${encodeURIComponent(candidate.platform)}/${encodeURIComponent(candidate.content_id)}/action`
      )
      setActionJob(data.action || null)
      return data.action || null
    } catch {
      setActionJob(null)
      return null
    }
  }, [])

  const loadDetail = useCallback(async (candidate) => {
    if (!candidate) return
    setSelected(candidate)
    setDetailLoading(true)
    setError("")
    try {
      const { data } = await http.get(
        `/api/review/candidates/${encodeURIComponent(candidate.platform)}/${encodeURIComponent(candidate.content_id)}`
      )
      setDetail(data)
      const activeReview = data.remake_exists ? data.remake_review : data.candidate_review
      setNote(activeReview?.note || "")

      const manifest = data.action_manifest || {}
      const params = manifest.params || {}
      if (manifest.action === "translate" || manifest.action === "remake") setActionType(manifest.action)
      if (params.voice_id) setVoiceId(params.voice_id)
      if (params.target_duration) setTargetDuration(params.target_duration)
      if (params.translate_mode) setTranslateMode(params.translate_mode)

      await loadActionStatus(candidate)
    } catch (e) {
      setError(e.response?.data?.detail || "Could not load candidate detail.")
    } finally {
      setDetailLoading(false)
    }
  }, [loadActionStatus])

  useEffect(() => {
    loadQueue(filter).then((data) => {
      if (!data?.candidates?.length) {
        setSelected(null)
        setDetail(null)
        return
      }
      const currentKey = selected ? `${selected.platform}:${selected.content_id}` : null
      const stillVisible = data.candidates.find((c) => `${c.platform}:${c.content_id}` === currentKey)
      loadDetail(stillVisible || data.candidates[0])
    })
    // selected intentionally excluded: changing a decision refreshes explicitly.
  }, [filter]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!detail || !["pending", "running"].includes(actionJob?.status)) return

    let cancelled = false
    const timer = setInterval(async () => {
      const job = await loadActionStatus(detail)
      if (cancelled || !job || ["pending", "running"].includes(job.status)) return

      const data = await loadQueue(filter)
      const refreshed = data?.candidates?.find(
        (c) => c.platform === detail.platform && c.content_id === detail.content_id,
      )
      await loadDetail(refreshed || {
        platform: detail.platform,
        content_id: detail.content_id,
        title: detail.title,
      })
    }, 2000)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [
    actionJob?.status,
    detail?.platform,
    detail?.content_id,
    filter,
    loadActionStatus,
    loadDetail,
    loadQueue,
  ])

  const selectedKey = selected ? `${selected.platform}:${selected.content_id}` : null

  const summaryTiles = useMemo(() => [
    ["Analyzed", payload.summary?.candidates_analyzed],
    ["Pending", payload.summary?.pending_review],
    ["Approved for remake", payload.summary?.approved_for_remake],
    ["Rendered", payload.summary?.rendered],
    ["Remake review", payload.summary?.awaiting_remake_review],
    ["Approved", payload.summary?.approved],
    ["Rework", payload.summary?.rework],
    ["Rejected", payload.summary?.rejected],
  ], [payload.summary])

  const saveDecision = async (stage, status) => {
    if (!detail) return
    if (stage === "remake" && status === "REWORK" && !note.trim()) {
      setError("REWORK requires a note explaining what needs to change.")
      return
    }
    setSaving(true)
    setError("")
    try {
      await http.post(
        `/api/review/candidates/${encodeURIComponent(detail.platform)}/${encodeURIComponent(detail.content_id)}/decision`,
        { stage, status, note: note.trim() || null },
      )
      const data = await loadQueue(filter)
      const refreshed = data?.candidates?.find(
        (c) => c.platform === detail.platform && c.content_id === detail.content_id,
      )
      if (refreshed) await loadDetail(refreshed)
      else {
        setSelected(null)
        setDetail(null)
      }
    } catch (e) {
      setError(e.response?.data?.detail || "Could not save review decision.")
    } finally {
      setSaving(false)
    }
  }

  const runAction = async (requestedAction) => {
    if (!detail) return
    setActionBusy(true)
    setError("")
    try {
      const { data } = await http.post(
        `/api/review/candidates/${encodeURIComponent(detail.platform)}/${encodeURIComponent(detail.content_id)}/action`,
        {
          action: requestedAction,
          voice_id: voiceId,
          target_duration: targetDuration,
          translate_mode: translateMode,
        },
      )
      setActionJob(data)
    } catch (e) {
      setError(e.response?.data?.detail || "Could not start production action.")
    } finally {
      setActionBusy(false)
    }
  }

  const cancelAction = async () => {
    if (!actionJob?.job_id) return
    setActionBusy(true)
    setError("")
    try {
      await http.delete(`/api/jobs/${encodeURIComponent(actionJob.job_id)}`)
      await loadActionStatus(detail)
    } catch (e) {
      setError(e.response?.data?.detail || "Could not cancel action.")
    } finally {
      setActionBusy(false)
    }
  }

  const mediaSrc = (kind) => detail
    ? `/api/review/media/${kind}/${encodeURIComponent(detail.platform)}/${encodeURIComponent(detail.content_id)}`
    : ""

  const intel = detail?.intelligence || {}
  const breakdown = detail?.radar_breakdown || {}
  const adaptation = detail?.adaptation || {}
  const va = adaptation?.vietnamese_adaptation || {}
  const actionManifest = detail?.action_manifest || {}
  const productionMode = actionManifest.action || (detail?.remake_exists ? "remake" : null)
  const actionActive = ["pending", "running"].includes(actionJob?.status)

  return (
    <Box sx={{ p: { xs: 2, md: 3 }, maxWidth: 1700, mx: "auto" }}>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" gap={2} sx={{ mb: 2 }}>
        <Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <FactCheckOutlinedIcon color="primary" />
            <Typography variant="h5" sx={{ fontWeight: 800 }}>Review Queue</Typography>
          </Stack>
          <Typography color="text.secondary" sx={{ mt: 0.5 }}>
            Human decisions between Radar, Vietnamese adaptation, and publishing.
          </Typography>
        </Box>
        <Button
          variant="outlined"
          startIcon={<RefreshIcon />}
          onClick={() => loadQueue(filter)}
          disabled={loading}
        >
          Refresh
        </Button>
      </Stack>

      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mb: 2 }}>
        {summaryTiles.map(([label, value]) => <SummaryTile key={label} label={label} value={value} />)}
      </Stack>

      <Stack direction={{ xs: "column", lg: "row" }} gap={2} alignItems="stretch">
        <Box sx={{ width: { xs: "100%", lg: 420 }, flexShrink: 0 }}>
          <FormControl size="small" fullWidth sx={{ mb: 1.5 }}>
            <InputLabel id="review-filter-label">Status</InputLabel>
            <Select
              labelId="review-filter-label"
              value={filter}
              label="Status"
              onChange={(e) => setFilter(e.target.value)}
            >
              {FILTERS.map((f) => <MenuItem key={f} value={f}>{statusLabel(f)}</MenuItem>)}
            </Select>
          </FormControl>
          {loading ? (
            <Box sx={{ py: 5, textAlign: "center" }}><CircularProgress size={28} /></Box>
          ) : (
            <CandidateList
              candidates={payload.candidates || []}
              selectedKey={selectedKey}
              onSelect={loadDetail}
            />
          )}
        </Box>

        <Card variant="outlined" sx={{ flex: 1, minWidth: 0 }}>
          <CardContent sx={{ p: { xs: 2, md: 2.5 } }}>
            {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
            {!selected ? (
              <Alert severity="info">Select a candidate to review.</Alert>
            ) : detailLoading || !detail ? (
              <Box sx={{ py: 8, textAlign: "center" }}><CircularProgress /></Box>
            ) : (
              <Stack spacing={2.5}>
                <Box>
                  <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mb: 1 }}>
                    <Chip label={`#${detail.rank}`} size="small" />
                    <Chip label={detail.platform} size="small" variant="outlined" />
                    <Chip
                      label={statusLabel((detail.remake_exists ? detail.remake_review : detail.candidate_review)?.status)}
                      size="small"
                      color={statusColor((detail.remake_exists ? detail.remake_review : detail.candidate_review)?.status)}
                    />
                  </Stack>
                  <Typography variant="h6" sx={{ fontWeight: 800, lineHeight: 1.35 }}>{detail.title}</Typography>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 0.75 }} useFlexGap flexWrap="wrap">
                    <Typography variant="body2" color="text.secondary">{detail.creator}</Typography>
                    {detail.url && (
                      <Button
                        component="a"
                        href={detail.url}
                        target="_blank"
                        rel="noreferrer"
                        size="small"
                        endIcon={<OpenInNewIcon fontSize="small" />}
                      >
                        Source
                      </Button>
                    )}
                  </Stack>
                </Box>

                <Stack direction={{ xs: "column", md: "row" }} gap={2}>
                  <VideoPanel title="Original" src={mediaSrc("source")} available={detail.source_exists} />
                  {detail.remake_exists && (
                    <VideoPanel title="Vietnamese remake" src={mediaSrc("final")} available={detail.remake_exists} />
                  )}
                </Stack>

                <Divider />

                <Stack direction={{ xs: "column", md: "row" }} gap={3}>
                  <Box sx={{ flex: 1 }}>
                    <InfoBlock title="Radar">
                      <div>Total score: <strong>{detail.radar_score ?? "-"}</strong> · confidence {detail.radar_confidence ?? "-"}</div>
                      <div>
                        {Object.entries(breakdown).map(([k, v]) => `${k}=${v}`).join(" · ") || "No breakdown"}
                      </div>
                    </InfoBlock>
                  </Box>
                  <Box sx={{ flex: 1 }}>
                    <InfoBlock title="Intelligence">
                      <div><strong>Topic:</strong> {intel.topic || "-"}</div>
                      <div><strong>Format:</strong> {intel.content_format?.type || "-"}</div>
                      <div><strong>Localization:</strong> {intel.localization_potential || "-"}</div>
                      <div><strong>Difficulty:</strong> {intel.remake_difficulty || "-"}</div>
                    </InfoBlock>
                  </Box>
                </Stack>

                <InfoBlock title="Hook">
                  {intel.hook?.text || "-"}
                </InfoBlock>

                <InfoBlock title="Why it may work">
                  {(intel.why_it_may_work || []).length
                    ? <ul style={{ marginTop: 4 }}>{intel.why_it_may_work.map((x) => <li key={x}>{x}</li>)}</ul>
                    : "-"
                  }
                </InfoBlock>

                <InfoBlock title="Vietnamese angle">
                  <div>{intel.vietnamese_angle?.concept || "-"}</div>
                  {intel.vietnamese_angle?.suggested_hook && (
                    <Box sx={{ mt: 0.75 }}><strong>Suggested hook:</strong> {intel.vietnamese_angle.suggested_hook}</Box>
                  )}
                </InfoBlock>

                <InfoBlock title="Risk">
                  <RiskChips risks={
                    Object.entries(intel.risks || {})
                      .filter(([, v]) => String(v).toUpperCase() !== "LOW")
                      .map(([k, v]) => `${k.replace("_dependency", "")}:${v}`)
                  } />
                </InfoBlock>

                {detail.remake_exists && (
                  <>
                    <Divider />
                    <Stack direction="row" spacing={1} alignItems="center" useFlexGap flexWrap="wrap">
                      <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>Rendered output</Typography>
                      <Chip
                        size="small"
                        label={productionMode === "translate" ? "Translate / Dub" : "Creative Remake"}
                        color="secondary"
                        variant="outlined"
                      />
                    </Stack>

                    {productionMode === "translate" ? (
                      <>
                        <InfoBlock title="Translation mode">
                          {(actionManifest.params?.translate_mode || "full_dub") === "captions_only"
                            ? "Vietnamese captions only — original audio preserved."
                            : "Full Vietnamese dub + Vietnamese captions."}
                        </InfoBlock>
                        <InfoBlock title="Voice">
                          {actionManifest.params?.voice_id || "Vietnamese Edge TTS"}
                        </InfoBlock>
                      </>
                    ) : (
                      <>
                        <InfoBlock title="Vietnamese hook">{va.hook || "-"}</InfoBlock>
                        <InfoBlock title="Vietnamese script">
                          <Box sx={{ whiteSpace: "pre-wrap" }}>{va.script || "-"}</Box>
                        </InfoBlock>
                        <InfoBlock title="Localization changes">
                          <Box component="pre" sx={{ whiteSpace: "pre-wrap", fontFamily: "inherit", m: 0 }}>
                            {JSON.stringify(adaptation.localization_changes || {}, null, 2)}
                          </Box>
                        </InfoBlock>
                        <InfoBlock title="Segment plan">
                          <Box component="pre" sx={{ whiteSpace: "pre-wrap", fontFamily: "monospace", fontSize: "0.78rem", m: 0 }}>
                            {JSON.stringify(detail.segment_plan || {}, null, 2)}
                          </Box>
                        </InfoBlock>
                      </>
                    )}
                  </>
                )}

                <details>
                  <summary style={{ cursor: "pointer", fontWeight: 700 }}>Transcript</summary>
                  <Box sx={{ mt: 1, whiteSpace: "pre-wrap", color: "text.secondary", fontSize: "0.86rem", maxHeight: 260, overflow: "auto" }}>
                    {detail.transcript?.full_transcript || "Transcript unavailable"}
                  </Box>
                </details>

                <Divider />

                {!detail.remake_exists && detail.candidate_review?.status === "APPROVED_FOR_REMAKE" && (
                  <ActionControls
                    actionType={actionType}
                    onActionType={setActionType}
                    voiceId={voiceId}
                    onVoiceId={setVoiceId}
                    targetDuration={targetDuration}
                    onTargetDuration={setTargetDuration}
                    translateMode={translateMode}
                    onTranslateMode={setTranslateMode}
                    job={actionJob}
                    busy={actionBusy}
                    onRun={() => runAction(actionType)}
                    onCancel={cancelAction}
                  />
                )}

                {detail.remake_exists && detail.remake_review?.status === "REWORK" && (
                  <ActionControls
                    rerender
                    previousAction={detail.action_manifest?.action || "remake"}
                    actionType={actionType}
                    onActionType={setActionType}
                    voiceId={voiceId}
                    onVoiceId={setVoiceId}
                    targetDuration={targetDuration}
                    onTargetDuration={setTargetDuration}
                    translateMode={translateMode}
                    onTranslateMode={setTranslateMode}
                    job={actionJob}
                    busy={actionBusy}
                    onRun={() => runAction("rerender")}
                    onCancel={cancelAction}
                  />
                )}

                {detail.remake_exists && detail.remake_review?.status === "APPROVED" && (
                  <PublishGate detail={detail} />
                )}

                <TextField
                  label={detail.remake_exists ? "Review note (required for REWORK)" : "Review note"}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  multiline
                  minRows={2}
                  fullWidth
                />

                {detail.remake_exists ? (
                  <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                    <Button disabled={saving || actionActive} variant="contained" color="success" onClick={() => saveDecision("remake", "APPROVED")}>
                      Approve
                    </Button>
                    <Button disabled={saving || actionActive} variant="contained" color="warning" onClick={() => saveDecision("remake", "REWORK")}>
                      Rework
                    </Button>
                    <Button disabled={saving || actionActive} variant="outlined" color="error" onClick={() => saveDecision("remake", "REJECTED")}>
                      Reject
                    </Button>
                  </Stack>
                ) : (
                  <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                    <Button disabled={saving || actionActive} variant="contained" color="success" onClick={() => saveDecision("candidate", "APPROVED_FOR_REMAKE")}>
                      Approve for remake
                    </Button>
                    <Button disabled={saving || actionActive} variant="outlined" color="error" onClick={() => saveDecision("candidate", "REJECTED")}>
                      Reject
                    </Button>
                  </Stack>
                )}
              </Stack>
            )}
          </CardContent>
        </Card>
      </Stack>
    </Box>
  )
}
