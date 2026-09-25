import test from "node:test"
import assert from "node:assert/strict"

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
      ["Công cụ", "/utilities"],
    ],
  )
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
