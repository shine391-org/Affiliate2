// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2025-2026 ViralMint Contributors
import { useState } from "react"
import {
  Box, Typography, FormControl, InputLabel, Select, MenuItem, Stack,
} from "@mui/material"
import CompressOutlinedIcon from "@mui/icons-material/CompressOutlined"
import ToolRunner from "../../components/tools/ToolRunner"
import { useToolInput } from "../../components/tools/ToolInputContext"
import useDocumentTitle from "../../hooks/useDocumentTitle"

// Mirrors _COMPRESS_HEIGHTS / _COMPRESS_LEVELS in backend/core/tool_runners.py.
// The backend is the authority (it validates the Literal); these carry the
// human wording and the target height used for the "already smaller" hint.
const RESOLUTIONS = [
  { value: "original", label: "Gốc — giữ kích thước nguồn", height: 0 },
  { value: "1280x720", label: "HD — 720p", height: 720 },
  { value: "854x480", label: "SD — 480p", height: 480 },
  { value: "640x360", label: "Thấp — 360p", height: 360 },
  { value: "426x240", label: "Rất thấp — 240p", height: 240 },
]

const LEVELS = [
  { value: "maximum", label: "Tối đa — tệp nhỏ nhất" },
  { value: "high", label: "Cao — cân bằng tốt" },
  { value: "medium", label: "Trung bình — chất lượng tốt hơn" },
  { value: "low", label: "Thấp — chất lượng cao" },
  { value: "minimal", label: "Tối thiểu — chất lượng tốt nhất" },
]

// What the two dials will actually do to THIS file. Reads the real source
// dimensions off the loaded input (ToolInputContext) rather than guessing.
//
// Deliberately no predicted byte count: output size depends on how much
// motion the footage has, and a confident "≈ 4.2 MB" that lands at 11 MB is
// worse than no number at all.
function OutcomePreview({ resolution, level }) {
  const { width: srcW, height: srcH } = useToolInput()
  const target = RESOLUTIONS.find((r) => r.value === resolution)
  const known = srcW > 0 && srcH > 0

  if (!known) {
    return (
      <Typography variant="caption" sx={{ color: "text.secondary" }}>
        Add a video to see what these settings will do to it.
      </Typography>
    )
  }

  const wouldUpscale = target.height > 0 && target.height >= srcH
  const outH = wouldUpscale || !target.height ? srcH : target.height
  const outW = Math.round((srcW * outH) / srcH / 2) * 2

  return (
    <Stack spacing={0.5}>
      <Typography variant="caption" sx={{ color: "text.secondary" }}>
        <strong>Nguồn</strong> {srcW} × {srcH} → <strong>đầu ra</strong> {outW} × {outH}
      </Typography>
      {wouldUpscale && (
        <Typography variant="caption" sx={{ color: "warning.main" }}>
          Video này đã nhỏ hơn {target.label.split("—")[1]?.trim() || resolution}.
          It'll keep its size — scaling up would only make the file bigger.
        </Typography>
      )}
      <Typography variant="caption" sx={{ color: "text.secondary" }}>
        {level === "maximum"
          ? "Maximum squeezes hardest — expect visible softening on detailed footage."
          : level === "minimal"
            ? "Mức Tối giản gần như không ảnh hưởng chất lượng nên dung lượng giảm không nhiều."
            : "Quality holds up well at this level for most footage."}
      </Typography>
    </Stack>
  )
}

export default function ToolCompress() {
  useDocumentTitle("Nén video")
  const [resolution, setResolution] = useState("original")
  const [level, setLevel] = useState("high")

  return (
    <ToolRunner
      title="Nén video"
      description="Giảm dung lượng video cho email, ứng dụng chat hoặc giới hạn tải lên — không cần cắt lại."
      icon={<CompressOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/compress"
      processLabel="Compress"
      downloadLabel="Tải bản đã nén"
      fieldBuilder={() => ({ resolution, level })}
      costNote={
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          Runs locally with FFmpeg — the file never leaves your machine.
          Resolution and strength are independent: drop the resolution when the
          video will be watched small, raise the strength when it just needs to
          fit under a limit. Footage that's already been compressed once won't
          shrink much further.
        </Typography>
      }
    >
      <Stack spacing={2}>
        <FormControl size="small" fullWidth>
          <InputLabel>Độ phân giải</InputLabel>
          <Select
            value={resolution}
            label="Độ phân giải"
            onChange={(e) => setResolution(e.target.value)}
          >
            {RESOLUTIONS.map((r) => (
              <MenuItem key={r.value} value={r.value}>{r.label}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <FormControl size="small" fullWidth>
          <InputLabel>Mức nén</InputLabel>
          <Select
            value={level}
            label="Mức nén"
            onChange={(e) => setLevel(e.target.value)}
          >
            {LEVELS.map((l) => (
              <MenuItem key={l.value} value={l.value}>{l.label}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <Box>
          <OutcomePreview resolution={resolution} level={level} />
        </Box>
      </Stack>
    </ToolRunner>
  )
}
