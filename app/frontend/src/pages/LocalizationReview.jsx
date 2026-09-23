import { useCallback, useEffect, useState } from "react"
import {
  Alert, Box, Button, Chip, CircularProgress, Divider, Stack, Typography,
} from "@mui/material"
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined"
import RefreshOutlinedIcon from "@mui/icons-material/RefreshOutlined"
import http from "../api/http"
import PageHero from "../components/PageHero"
import LocalizationExpertPanel from "../components/tools/LocalizationExpertPanel"
import useDocumentTitle from "../hooks/useDocumentTitle"

export default function LocalizationReview() {
  useDocumentTitle("Localization Review")
  const [items, setItems] = useState([])
  const [selected, setSelected] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await http.get("/api/localization/exceptions?limit=100")
      const next = data?.items || []
      setItems(next)
      if (!selected && next.length) setSelected(next[0].job_id)
      if (selected && !next.some((x) => x.job_id === selected)) setSelected(next[0]?.job_id || null)
    } finally {
      setLoading(false)
    }
  }, [selected])

  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <PageHero
        backTo="/tools"
        icon={<FactCheckOutlinedIcon sx={{ fontSize: 22 }} />}
        title="Localization Review"
        subtitle="QC exceptions and targeted expert reruns — same persisted pipeline artifacts"
        actions={
          <Button variant="outlined" startIcon={<RefreshOutlinedIcon />} onClick={load}>
            Refresh
          </Button>
        }
      />
      <Box sx={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: { xs: "1fr", md: "340px 1fr" } }}>
        <Box sx={{ overflow: "auto", p: 2, borderRight: { md: 1 }, borderColor: "divider" }}>
          {loading ? (
            <Stack alignItems="center" sx={{ py: 5 }}><CircularProgress size={28} /></Stack>
          ) : items.length === 0 ? (
            <Alert severity="success">No localization jobs currently need review.</Alert>
          ) : (
            <Stack spacing={1}>
              {items.map((item) => (
                <Button
                  key={item.job_id}
                  variant={selected === item.job_id ? "contained" : "outlined"}
                  color={item.qc_status === "FAILED" ? "error" : "warning"}
                  onClick={() => setSelected(item.job_id)}
                  sx={{ justifyContent: "flex-start", textAlign: "left", py: 1.25 }}
                >
                  <Box sx={{ minWidth: 0 }}>
                    <Stack direction="row" spacing={0.75} alignItems="center">
                      <Typography variant="body2" sx={{ fontWeight: 700 }} noWrap>
                        {item.request?.target_language || "Localization"}
                      </Typography>
                      {item.qc_status && <Chip size="small" label={item.qc_status} />}
                    </Stack>
                    <Typography variant="caption" sx={{ opacity: 0.85 }} noWrap>
                      {item.job_id}
                    </Typography>
                  </Box>
                </Button>
              ))}
            </Stack>
          )}
        </Box>
        <Box sx={{ overflow: "auto", p: 2.5 }}>
          {selected ? (
            <LocalizationExpertPanel jobId={selected} />
          ) : (
            <Stack spacing={1} alignItems="center" justifyContent="center" sx={{ minHeight: 240 }}>
              <FactCheckOutlinedIcon color="disabled" sx={{ fontSize: 44 }} />
              <Typography variant="body2" color="text.secondary">
                Select a QC exception to inspect and rerun.
              </Typography>
            </Stack>
          )}
        </Box>
      </Box>
    </Box>
  )
}
