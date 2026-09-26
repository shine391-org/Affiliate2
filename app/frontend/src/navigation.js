export const PRIMARY_NAV_ITEMS = [
  { to: "/discover/spy", label: "Khám phá", icon: "discover", activeRoot: "/discover" },
  { to: "/library", label: "Thư viện", icon: "library" },
  { to: "/studio", label: "Studio", icon: "studio" },
  { to: "/review", label: "Duyệt", icon: "review" },
  { to: "/publish", label: "Xuất bản", icon: "publish" },
  { to: "/performance", label: "Hiệu suất", icon: "performance" },
  { to: "/utilities", label: "Công cụ", icon: "utilities" },
]

export const BOTTOM_NAV_ITEMS = [
  { to: "/settings", label: "Cài đặt", icon: "settings" },
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
  { value: "spy", label: "Theo dõi", to: "/discover/spy" },
  { value: "channels", label: "Kênh", to: "/discover/channels" },
  { value: "candidates", label: "Ứng viên", to: "/discover/candidates" },
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
