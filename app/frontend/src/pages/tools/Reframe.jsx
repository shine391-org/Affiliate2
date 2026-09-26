import { Typography } from "@mui/material"
import AspectRatioOutlinedIcon from "@mui/icons-material/AspectRatioOutlined"
import ToolRunner from "../../components/tools/ToolRunner"
import useDocumentTitle from "../../hooks/useDocumentTitle"

export default function ToolReframe() {
  useDocumentTitle("Đổi khung dọc")
  return (
    <ToolRunner
      title="Đổi khung dọc"
      description="Đổi video ngang 16:9 sang dọc 9:16 với bám khuôn mặt"
      icon={<AspectRatioOutlinedIcon fontSize="large" />}
      endpoint="/api/tools/reframe"
    >
      <Typography variant="body2" sx={{ color: "text.secondary" }}>
        Fits the whole frame into 9:16 over a blurred, zoomed copy of itself —
        nothing is cropped away. Sources that are already 9:16 or narrower are
        returned unchanged; square and 4:5 clips are reframed.
      </Typography>
    </ToolRunner>
  )
}
