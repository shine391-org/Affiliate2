import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Alert, Box, Button, Chip, CircularProgress, Paper, Stack, ToggleButton,
  ToggleButtonGroup, Typography,
} from "@mui/material"
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined"
import RefreshOutlinedIcon from "@mui/icons-material/RefreshOutlined"
import { useSearchParams } from "react-router-dom"
import http from "../api/http"
import PageHero from "../components/PageHero"
import LocalizationExpertPanel from "../components/tools/LocalizationExpertPanel"
import useDocumentTitle from "../hooks/useDocumentTitle"
import {
  REVIEW_FILTERS,
  reviewItemMatches,
  reviewSelectionForItems,
  reviewSelectionId,
} from "../reviewModel"
import { loadReviewItems } from "../reviewSources"

export default function LocalizationReview() {
  useDocumentTitle("Review")
  const [params, setParams] = useSearchParams()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const filter = params.get("filter") || "needs-review"
  // ?job= remains a legacy deep link for Localize; ?item= is the generic
  // Review selection contract Phase D/E can reuse.
  const selected = params.get("item") || params.get("job") || ""

  const reconcileSelection = useCallback((next, current, currentFilter, setParamsFn) => {
    const resolved = reviewSelectionForItems(next, currentFilter, current)
    setParamsFn((previous) => {
      const value = new URLSearchParams(previous)
      if (resolved) value.set("item", resolved)
      else value.delete("item")
      return value
    }, { replace: true })
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const next = await loadReviewItems(http)
      setItems(next)
      reconcileSelection(next, selected, filter, setParams)
    } finally {
      setLoading(false)
    }
  }, [filter, selected, setParams, reconcileSelection])

  useEffect(() => {
    load()
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [load])

  const visible = useMemo(() => items.filter((item) => reviewItemMatches(item, filter)), [items, filter])
  const selectedItem = useMemo(
    () => items.find((item) => reviewSelectionId(item) === selected) || null,
    [items, selected],
  )
  const choose = (item) => setParams((previous) => {
    const value = new URLSearchParams(previous)
    value.set("item", reviewSelectionId(item))
    value.delete("job")
    return value
  })
  const changeFilter = (_, value) => {
    if (!value) return
    setParams((previous) => {
      const next = new URLSearchParams(previous)
      next.set("filter", value)
      next.delete("job")
      next.delete("item")
      return next
    })
  }

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <PageHero
        icon={<FactCheckOutlinedIcon sx={{ fontSize: 22 }} />}
        title="Review"
        subtitle="One exception workspace. Current live items come from the canonical localization QC queue."
        actions={<Button variant="outlined" startIcon={<RefreshOutlinedIcon />} onClick={load}>Refresh</Button>}
      />
      <Box sx={{ px: { xs: 1.5, md: 2.5 }, py: 1.25, borderBottom: 1, borderColor: "divider", overflowX: "auto", flexShrink: 0 }}>
        <ToggleButtonGroup exclusive value={filter} onChange={changeFilter} size="small" sx={{ "& .MuiToggleButton-root": { textTransform: "none", whiteSpace: "nowrap" } }}>
          {REVIEW_FILTERS.map((item) => {
            const count = item.enabled ? items.filter((reviewItem) => reviewItemMatches(reviewItem, item.value)).length : null
            return <ToggleButton key={item.value} value={item.value} disabled={!item.enabled}>{item.label}{count !== null && <Chip size="small" label={count} sx={{ ml: 0.75, height: 18, fontSize: "0.62rem" }} />}</ToggleButton>
          })}
        </ToggleButtonGroup>
      </Box>
      <Box sx={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: { xs: "1fr", md: "360px minmax(0, 1fr)" } }}>
        <Box sx={{ overflow: "auto", p: 2, borderRight: { md: 1 }, borderColor: "divider" }}>
          {loading ? <Stack alignItems="center" sx={{ py: 5 }}><CircularProgress size={28} /></Stack> : visible.length === 0 ? <Alert severity="success">No real items match this review filter.</Alert> : <Stack spacing={1}>{visible.map((item) => <Paper key={item.id} variant="outlined" onClick={() => choose(item)} sx={{ p: 1.5, borderRadius: 2, cursor: "pointer", borderColor: selected === reviewSelectionId(item) ? "primary.main" : "divider", bgcolor: selected === reviewSelectionId(item) ? "action.selected" : "background.paper" }}><Stack direction="row" spacing={0.75} alignItems="center"><Typography variant="body2" sx={{ fontWeight: 800, flex: 1 }} noWrap>{item.source}</Typography><Chip size="small" color={item.severity} label={item.status} /></Stack><Typography variant="caption" color="text.secondary">{item.workflow} · {item.project || reviewSelectionId(item)}</Typography><Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 1 }}>{item.categories.map((category) => <Chip key={category} size="small" variant="outlined" label={category} />)}</Stack>{item.reasons.map((reason, index) => <Typography key={index} variant="caption" color="error.main" sx={{ display: "block", mt: 0.5 }}>{reason.stage}: {reason.error || "failed"}</Typography>)}</Paper>)}</Stack>}
        </Box>
        <Box sx={{ overflow: "auto", p: { xs: 1.5, md: 2.5 } }}>
          {selectedItem?.detail_kind === "localization-expert" && selectedItem.job_id ? (
            <LocalizationExpertPanel jobId={selectedItem.job_id} />
          ) : selectedItem ? (
            <Stack spacing={1.5}>
              <Typography variant="h6" sx={{ fontWeight: 800 }}>{selectedItem.workflow}</Typography>
              <Alert severity={selectedItem.severity || "warning"}>
                {selectedItem.source} · {selectedItem.status}
              </Alert>
              <Typography variant="body2" color="text.secondary">
                This workflow uses the generic Review envelope. Its Phase-specific detail editor will plug into this surface without creating another Review app.
              </Typography>
              {selectedItem.action_target && <Button variant="outlined" href={selectedItem.action_target}>Open workflow</Button>}
            </Stack>
          ) : (
            <Stack spacing={1} alignItems="center" justifyContent="center" sx={{ minHeight: 240 }}>
              <FactCheckOutlinedIcon color="disabled" sx={{ fontSize: 44 }} />
              <Typography variant="body2" color="text.secondary">Select an exception to inspect and resolve it in its canonical workflow.</Typography>
            </Stack>
          )}
        </Box>
      </Box>
    </Box>
  )
}
