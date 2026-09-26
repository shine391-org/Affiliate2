import { useCallback, useEffect, useMemo, useState } from "react"
import { Alert, Box, Button, CircularProgress, Stack, TablePagination } from "@mui/material"
import RefreshIcon from "@mui/icons-material/Refresh"
import Inventory2OutlinedIcon from "@mui/icons-material/Inventory2Outlined"
import { useNavigate } from "react-router-dom"
import http from "../api/http"
import PageHero from "../components/PageHero"
import ScoutResults from "../components/scout/ScoutResults"
import { downloadedByScoutResult } from "../discoverModel"
import useAppStore from "../store/appStore"
import useDocumentTitle from "../hooks/useDocumentTitle"

const DEFAULT_ROWS = 50

export default function Candidates() {
  useDocumentTitle("Khám phá / Ứng viên")
  const navigate = useNavigate()
  const showSnackbar = useAppStore((state) => state.showSnackbar)
  const [results, setResults] = useState([])
  const [downloads, setDownloads] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [rowsPerPage, setRowsPerPage] = useState(DEFAULT_ROWS)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (offset = 0, limit = rowsPerPage) => {
    setLoading(true)
    try {
      const [candidateResponse, downloadResponse] = await Promise.all([
        http.get("/api/scout/results", { params: { offset, limit } }),
        http.get("/api/downloaded", { params: { offset: 0, limit: 200 } }),
      ])
      setResults(candidateResponse.data?.results || [])
      setTotal(candidateResponse.data?.total || 0)
      setDownloads(downloadResponse.data?.videos || [])
    } catch (error) {
      showSnackbar(error.response?.data?.detail || "Không thể tải danh sách ứng viên", "error")
    } finally {
      setLoading(false)
    }
  }, [rowsPerPage, showSnackbar])

  useEffect(() => { load(0, DEFAULT_ROWS) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const localByCandidate = useMemo(() => downloadedByScoutResult(downloads), [downloads])

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <PageHero
        icon={<Inventory2OutlinedIcon sx={{ fontSize: 22 }} />}
        title="Khám phá / Ứng viên"
        subtitle="Các lead đã lưu từ Scout và Radar. Trạng thái đã lưu/danh sách rút gọn chưa khả dụng nên màn hình này không giả lập dữ liệu."
        accentColor="#7c3aed"
        actions={
          <Button size="small" variant="outlined" startIcon={<RefreshIcon />}
            onClick={() => { setPage(0); load(0, rowsPerPage) }}>
            Refresh
          </Button>
        }
      />
      <Box sx={{ flex: 1, overflow: "auto", p: { xs: 2, md: 3 } }}>
        {loading ? (
          <Stack alignItems="center" sx={{ py: 8 }}><CircularProgress size={32} /></Stack>
        ) : results.length === 0 ? (
          <Alert severity="info">Chưa có ứng viên nào được lưu. Hãy chạy Khám phá / Theo dõi để thu thập dữ liệu nguồn.</Alert>
        ) : (
          <>
            <ScoutResults
              results={results}
              onRefresh={() => load(page * rowsPerPage, rowsPerPage)}
              downloadedIndex={localByCandidate}
              showCandidateStatus
              onOpenStudio={(candidate) => {
                const sourceId = localByCandidate.get(candidate.id)
                navigate(sourceId ? `/studio?source=${encodeURIComponent(sourceId)}` : "/studio")
              }}
            />
            <TablePagination
              component="div"
              count={total}
              page={page}
              onPageChange={(_, nextPage) => {
                setPage(nextPage)
                load(nextPage * rowsPerPage, rowsPerPage)
              }}
              rowsPerPage={rowsPerPage}
              onRowsPerPageChange={(event) => {
                const next = parseInt(event.target.value, 10)
                setRowsPerPage(next)
                setPage(0)
                load(0, next)
              }}
              rowsPerPageOptions={[20, 50, 100]}
              sx={{ borderTop: 1, borderColor: "divider", mt: 1 }}
            />
          </>
        )}
      </Box>
    </Box>
  )
}
