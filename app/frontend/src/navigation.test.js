import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

import {
  PRIMARY_NAV_ITEMS,
  BOTTOM_NAV_ITEMS,
  LEGACY_ROUTE_TARGETS,
  discoverSectionForPath,
  legacyRedirectHref,
} from "./navigation.js"

test("primary navigation is workflow-first and ordered", () => {
  assert.deepEqual(
    PRIMARY_NAV_ITEMS.map(({ label, to }) => [label, to]),
    [
      ["Khám phá", "/discover/spy"],
      ["Thư viện", "/library"],
      ["Studio", "/studio"],
      ["Duyệt", "/review"],
      ["Xuất bản", "/publish"],
      ["Hiệu suất", "/performance"],
      ["Nhắn tin", "/messaging"],
      ["Công cụ", "/utilities"],
    ],
  )
})

test("messaging navigation has a sidebar icon mapping", () => {
  const layout = readFileSync(new URL("./components/Layout.jsx", import.meta.url), "utf8")
  const messaging = PRIMARY_NAV_ITEMS.find((item) => item.to === "/messaging")

  assert.deepEqual(messaging, {
    to: "/messaging",
    label: "Nhắn tin",
    icon: "messaging",
    activeRoot: "/messaging",
  })
  assert.match(layout, /messaging:\s*<ChatBubbleOutlineIcon\s*\/>/)
})

test("settings is the only bottom navigation destination", () => {
  assert.deepEqual(
    BOTTOM_NAV_ITEMS.map(({ label, to }) => [label, to]),
    [["Cài đặt", "/settings"]],
  )
})

test("legacy route ownership remains explicit", () => {
  assert.deepEqual(LEGACY_ROUTE_TARGETS, {
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
  })
})

test("discover routes resolve to the correct internal section", () => {
  assert.equal(discoverSectionForPath("/discover"), "spy")
  assert.equal(discoverSectionForPath("/discover/spy"), "spy")
  assert.equal(discoverSectionForPath("/discover/channels"), "channels")
  assert.equal(discoverSectionForPath("/discover/candidates"), "candidates")
  assert.equal(discoverSectionForPath("/discover/unknown"), "spy")
})

test("legacy redirects preserve query/hash while adding canonical parameters", () => {
  assert.equal(
    legacyRedirectHref("/studio/localize?batch=1", "?source=abc&step=source", "#files"),
    "/studio/localize?source=abc&step=source&batch=1#files",
  )
})

test("Studio active subroutes mount their real views, not redirect into Localize", () => {
  const routes = readFileSync(new URL("./App.jsx", import.meta.url), "utf8")
  const studio = readFileSync(new URL("./pages/Studio.jsx", import.meta.url), "utf8")
  for (const mode of ["manual", "smart-cut"]) {
    assert.ok(routes.includes(`<Route path="studio/${mode}" element={<Studio />} />`))
    assert.ok(studio.includes(`mode === "${mode}"`))
  }
  assert.ok(routes.includes('<Route path="clips" element={<LegacyRedirect to="/studio/manual" />} />'))
})
