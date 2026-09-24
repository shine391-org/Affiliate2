import test from "node:test"
import assert from "node:assert/strict"

import { SETTINGS_SECTIONS, normalizeSettingsSection } from "./settingsModel.js"

test("Settings sections reflect configuration ownership", () => {
  assert.deepEqual(
    SETTINGS_SECTIONS.map(({ value }) => value),
    ["ai", "localization", "tts", "services", "addons", "health"],
  )
})

test("Settings deep links fail safely to AI Connections", () => {
  assert.equal(normalizeSettingsSection("localization"), "localization")
  assert.equal(normalizeSettingsSection("unknown"), "ai")
  assert.equal(normalizeSettingsSection(null), "ai")
})
