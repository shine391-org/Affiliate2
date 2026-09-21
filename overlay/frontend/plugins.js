// CONTENT_ENGINE_REVIEW_OVERLAY
import { lazy } from "react"
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined"

const ReviewQueue = lazy(() => import("./review/ReviewQueue"))

export const pluginRoutes = [
  { path: "review", element: <ReviewQueue /> },
]

export const pluginNavItems = [
  { to: "/review", icon: <FactCheckOutlinedIcon />, label: "Review Queue" },
]
