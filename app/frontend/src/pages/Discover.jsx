import { Box, Tab, Tabs } from "@mui/material"
import { Outlet, useLocation, useNavigate } from "react-router-dom"
import { DISCOVER_SECTIONS, discoverSectionForPath } from "../navigation"

export default function Discover() {
  const location = useLocation()
  const navigate = useNavigate()
  const section = discoverSectionForPath(location.pathname)

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <Box sx={{ px: { xs: 2, md: 3 }, borderBottom: 1, borderColor: "divider", bgcolor: "background.paper", flexShrink: 0 }}>
        <Tabs
          value={section}
          onChange={(_, value) => navigate(DISCOVER_SECTIONS.find((item) => item.value === value)?.to || "/discover/spy")}
          variant="scrollable"
          scrollButtons="auto"
          aria-label="Discover sections"
        >
          {DISCOVER_SECTIONS.map((item) => (
            <Tab key={item.value} value={item.value} label={item.label} sx={{ textTransform: "none", fontWeight: 700 }} />
          ))}
        </Tabs>
      </Box>
      <Box sx={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        <Outlet />
      </Box>
    </Box>
  )
}
