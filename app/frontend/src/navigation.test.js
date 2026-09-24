import test from "node:test"
import assert from "node:assert/strict"

import {
  PRIMARY_NAV_ITEMS,
  BOTTOM_NAV_ITEMS,
  LEGACY_ROUTE_TARGETS,
  discoverSectionForPath,
} from "./navigation.js"

test("primary navigation is workflow-first and ordered", () => {
  assert.deepEqual(
    PRIMARY_NAV_ITEMS.map(({ label, to }) => [label, to]),
    [
      ["Discover", "/discover/spy"],
      ["Library", "/library"],
      ["Studio", "/studio"],
      ["Review", "/review"],
      ["Publish", "/publish"],
      ["Performance", "/performance"],
      ["Utilities", "/utilities"],
    ],
  )
})

test("settings is the only bottom navigation destination", () => {
  assert.deepEqual(
    BOTTOM_NAV_ITEMS.map(({ label, to }) => [label, to]),
    [["Settings", "/settings"]],
  )
})

test("legacy route ownership remains explicit", () => {
  assert.deepEqual(LEGACY_ROUTE_TARGETS, {
    "/scout": "/discover/spy",
    "/channels": "/discover/channels",
    "/clips": "/studio",
    "/tools": "/utilities",
    "/tools/localization-review": "/review",
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
