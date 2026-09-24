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
  "/clips": "/studio/manual",
  "/tools": "/utilities",
  "/tools/translate": "/studio/localize",
  "/tools/localization-batch": "/studio/localize?batch=1",
  "/tools/localization-review": "/review",
  "/review-queue": "/review",
  "/utilities/localization-review": "/review",
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

export function legacyRedirectHref(target, search = "", hash = "") {
  const [pathname, fixedSearch = ""] = target.split("?", 2)
  const params = new URLSearchParams(search)
  for (const [key, value] of new URLSearchParams(fixedSearch)) params.set(key, value)
  const query = params.toString()
  return `${pathname}${query ? `?${query}` : ""}${hash || ""}`
}
