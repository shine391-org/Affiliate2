// CONTENT_ENGINE_REVIEW_OVERLAY
import { lazy } from "react"
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined"
import InsightsOutlinedIcon from "@mui/icons-material/InsightsOutlined"

const ReviewQueue = lazy(() => import("./review/ReviewQueue"))
const Performance = lazy(() => import("./performance/Performance"))

export const pluginRoutes = [
  { path: "review", element: <ReviewQueue /> },
  { path: "performance", element: <Performance /> },
]

export const pluginNavItems = [
  { to: "/review", icon: <FactCheckOutlinedIcon />, label: "Review Queue" },
  { to: "/performance", icon: <InsightsOutlinedIcon />, label: "Performance" },
]
