import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8")
const studio = read("./pages/StudioLocalize.jsx")
const expert = read("./components/tools/LocalizationExpertPanel.jsx")
const dialog = read("./components/tools/ProviderBindingRerun.jsx")
const helper = read("./providerBinding.js")

test("provider binding drift is a compact chip that renders only on mismatch", () => {
  assert.ok(helper.includes("Provider đã thay đổi từ lúc job được tạo"))
  assert.ok(dialog.includes("ProviderBindingChip"))
  assert.ok(dialog.includes("binding_mismatch"))
  assert.ok(dialog.includes("return null"))
})

test("the rerun-with-drift modal defaults to the job snapshot and offers an explicit rebind", () => {
  assert.ok(dialog.includes("PROVIDER_BINDING_KEEP_LABEL"))
  assert.ok(dialog.includes("PROVIDER_BINDING_CURRENT_LABEL"))
  assert.ok(dialog.includes("PROVIDER_BINDING_CURRENT_UNAVAILABLE_LABEL"))
  assert.ok(dialog.includes("Hủy"))
  assert.ok(dialog.includes("onKeepSnapshot"))
  assert.ok(dialog.includes("onUseCurrent"))
  assert.ok(dialog.includes("onCancel"))
  assert.ok(dialog.includes("Dialog"), "small modal, not a new screen")
  // Keeping the job snapshot is the default (contained + focused) action.
  assert.ok(dialog.includes('variant="contained" autoFocus onClick={onKeepSnapshot}'))
  assert.ok(dialog.includes('variant="outlined"'))
})

test("rebind-providers is the only explicit adoption path and reports its own state", () => {
  assert.ok(studio.includes("/rebind-providers"))
  assert.ok(studio.includes("rebindProvidersPayload"))
  assert.ok(expert.includes("/rebind-providers"))
  assert.ok(expert.includes("rebindProvidersPayload"))
  assert.ok(studio.includes("awaiting_translation_review"))
})

test("studio recovery no longer silently rebinds TTS on a failed dubbing stage", () => {
  assert.ok(!studio.includes("/rebind-tts"), "silent auto-TTS rebind must be gone")
  assert.ok(!studio.includes("Dùng TTS hiện tại & chạy lại bước lỗi"))
  assert.ok(studio.includes("Thử lại đúng bước lỗi"))
})

test("the drift chip sits next to the rerun controls on both surfaces", () => {
  // Studio: chip is a sibling of the failed-stage retry button.
  const studioChip = studio.indexOf("<ProviderBindingChip")
  const studioRetry = studio.indexOf("Thử lại đúng bước lỗi")
  assert.ok(studioChip > 0 && studioRetry > studioChip, "chip must precede the retry button in the failure alert")
  assert.ok(studioRetry - studioChip < 400, "chip must be adjacent to the retry control, not elsewhere on the page")

  // Expert: chip is the first child of the rerun button row.
  const expertChip = expert.indexOf("<ProviderBindingChip")
  const expertRerun = expert.indexOf('"Chạy lại từ " + STAGE_LABELS[stage]')
  assert.ok(expertChip > 0 && expertRerun > expertChip, "chip must sit in the rerun row")
  assert.ok(expertRerun - expertChip < 400, "chip must be adjacent to the rerun buttons")
})

test("both Studio recovery and Expert rerun reuse the shared binding controls", () => {
  assert.ok(studio.includes("ProviderBindingRerun"))
  assert.ok(studio.includes("<ProviderBindingChip"))
  assert.ok(studio.includes("<ProviderBindingRerunDialog"))
  assert.ok(expert.includes("ProviderBindingRerun"))
  assert.ok(expert.includes("<ProviderBindingChip"))
  assert.ok(expert.includes("<ProviderBindingRerunDialog"))
})

test("the existing rerun path and payload stay untouched", () => {
  assert.ok(studio.includes("/api/localization/jobs/${jobId}/rerun`, {})"))
  assert.ok(expert.includes('+ "/rerun", { from_stage: stage }'))
  assert.ok(!studio.includes("regenerate_chunk_ids"))
})

test("legacy TTS drift warning cannot contradict canonical provider verdict", () => {
  assert.ok(expert.includes("ttsBinding && !detail?.provider_binding"))
})

test("a no-op rebind never pretends to dispatch a job", () => {
  for (const source of [studio, expert]) {
    assert.ok(source.includes('else if (!data?.from_stage)'), "handle unchanged binding explicitly")
    assert.ok(!source.includes('data?.from_stage || "dubbing"'), "do not invent a dispatched stage")
  }
})

test("modal explains separate snapshot and rebind start stages", () => {
  assert.ok(dialog.includes("providerBindingFromStage(binding)"))
  assert.ok(dialog.includes("Dùng cấu hình hiện tại sẽ chạy lại từ"))
  assert.ok(!dialog.includes("requestedStage || providerBindingFromStage(binding)"))
})

test("no frontend provider resolution or secret comparison leaks into the binding helper", () => {
  assert.ok(!helper.includes("api_key"))
  assert.ok(!helper.includes("secret"))
  assert.ok(!helper.includes("=== current"))
  // base_url may be read, but only to render a stripped identity label. Any
  // read left over after removing the safeEndpointLabel() call sites would put
  // raw credentials, query secrets or fragments back on screen.
  const withoutSanitisers = helper.replace(/safeEndpointLabel\([^)]*\)/g, "")
  assert.ok(!withoutSanitisers.includes("base_url"),
    "every base_url read must go through safeEndpointLabel")
})
