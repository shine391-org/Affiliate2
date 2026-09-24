export const PRIMARY_NAV_ITEMS = [
  { to: "/discover/spy", label: "Discover", icon: "discover", activeRoot: "/discover" },
  { to: "/library", label: "Library", icon: "library" },
  { to: "/studio", label: "Studio", icon: "studio" },
  { to: "/review", label: "Review", icon: "review" },
  { to: "/publish", label: "Publish", icon: "publish" },
  { to: "/performance", label: "Performance", icon: "performance" },
  { to: "/utilities", label: "Utilities", icon: "utilities" },
]

export const BOTTOM_NAV_ITEMS = [
  { to: "/settings", label: "Settings", icon: "settings" },
]

export const LEGACY_ROUTE_TARGETS = {
  "/scout": "/discover/spy",
  "/channels": "/discover/channels",
  "/clips": "/studio",
  "/tools": "/utilities",
  "/tools/localization-review": "/review",
  "/videos": "/library",
}

export const DISCOVER_SECTIONS = [
  { value: "spy", label: "Spy", to: "/discover/spy" },
  { value: "channels", label: "Channels", to: "/discover/channels" },
  { value: "candidates", label: "Candidates", to: "/discover/candidates" },
]

export function discoverSectionForPath(pathname) {
  const section = pathname.split("/").filter(Boolean)[1]
  return DISCOVER_SECTIONS.some((item) => item.value === section) ? section : "spy"
}
