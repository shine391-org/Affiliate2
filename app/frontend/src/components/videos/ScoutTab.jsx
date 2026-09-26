import { useState } from "react"
import { Box, Typography, Chip, Stack, TablePagination } from "@mui/material"
import ScoutResults from "../scout/ScoutResults"

export default function ScoutTab({ jobs, scoutResults, scoutTotal = 0, onFetchResults, page = 0, rowsPerPage = 50, onPageChange, onRowsPerPageChange }) {
  const [selectedJobId, setSelectedJobId] = useState(null)
  const scoutJobs = jobs.filter(j => j.job_type === "scout")

  return (
    <Box>
      {scoutJobs.length > 0 && (
        <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mb: 2 }}>
          <Chip
            label="Tất cả kết quả"
            onClick={() => { setSelectedJobId(null); onFetchResults(null, 0, rowsPerPage) }}
            color={!selectedJobId ? "primary" : "default"}
            variant={!selectedJobId ? "filled" : "outlined"}
            size="small"
          />
          {scoutJobs.slice(0, 8).map(j => (
            <Chip
              key={j.id}
              label={`${j.created_at ? new Date(j.created_at).toLocaleString("vi-VN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : j.id.slice(0, 8)}${j.status === "success" ? " ✓" : j.status === "failed" ? " ✗" : ""}`}
              onClick={() => { setSelectedJobId(j.id); onFetchResults(j.id, 0, rowsPerPage) }}
              color={selectedJobId === j.id ? "primary" : "default"}
              variant={selectedJobId === j.id ? "filled" : "outlined"}
              size="small"
            />
          ))}
        </Stack>
      )}

      {scoutResults.length > 0 ? (
        <>
          <ScoutResults results={scoutResults} onRefresh={() => onFetchResults(selectedJobId, 0, rowsPerPage)} />
          <TablePagination
            component="div"
            count={scoutTotal}
            page={page}
            onPageChange={onPageChange}
            rowsPerPage={rowsPerPage}
            onRowsPerPageChange={onRowsPerPageChange}
            rowsPerPageOptions={[20, 50, 100]}
            sx={{ borderTop: 1, borderColor: "divider", mt: 1 }}
          />
        </>
      ) : (
        <Box sx={{ textAlign: "center", py: 8, color: "text.secondary" }}>
          <Typography variant="h6" sx={{ mb: 0.5 }}>Chưa có kết quả quét</Typography>
          <Typography variant="body2">Hãy nhờ trợ lý quét video đang lên trong ngách của bạn.</Typography>
        </Box>
      )}
    </Box>
  )
}
