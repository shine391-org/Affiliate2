import test from "node:test"
import assert from "node:assert/strict"

import {
  PROVIDER_BINDING_MISMATCH_LABEL,
  PROVIDER_BINDING_KEEP_LABEL,
  PROVIDER_BINDING_CURRENT_LABEL,
  PROVIDER_BINDING_CURRENT_UNAVAILABLE_LABEL,
  bindingSideSummary,
  bindingRewriteRoutes,
  bindingTranslationRoutes,
  bindingTtsLabel,
  changedStages,
  changedStagesLabel,
  currentProvidersAvailable,
  providerBinding,
  providerBindingFromStage,
  providerBindingMismatch,
  rebindProvidersPayload,
} from "./providerBinding.js"

const drifted = {
  binding_mismatch: true,
  changed_stages: ["translation", "rewrite", "tts"],
  from_stage: "dubbing",
  current_error: null,
  job: {
    translation_routes: [{ connection_id: "hermes-main", model: "longcat" }],
    rewrite_routes: [{ connection_id: "hermes-main", model: "longcat" }],
    tts: { tts_provider: "elevenlabs", tts_voice: "voice-a" },
  },
  current: {
    translation_routes: [{ connection_id: "lmstudio-qwen", model: "qwen3:8b" }],
    rewrite_routes: [],
    tts: { tts_provider: "edge", tts_voice: "vi-VN-HoaiMyNeural" },
  },
}

test("provider binding is read verbatim from the job detail, never re-derived", () => {
  assert.deepEqual(providerBinding({ provider_binding: drifted }), drifted)
  assert.equal(providerBinding({}), null)
  assert.equal(providerBinding(null), null)
})

test("mismatch is only the backend boolean, not a frontend provider comparison", () => {
  assert.equal(providerBindingMismatch({ provider_binding: { binding_mismatch: true } }), true)
  assert.equal(providerBindingMismatch({ provider_binding: { binding_mismatch: false } }), false)
  assert.equal(providerBindingMismatch({ provider_binding: { current_error: "x" } }), false)
  assert.equal(providerBindingMismatch({}), false)
  assert.equal(providerBindingMismatch(null), false)
})

test("changed stages are normalised to a compact Vietnamese summary", () => {
  assert.deepEqual(changedStages(drifted), ["translation", "rewrite", "tts"])
  assert.deepEqual(changedStages({ changed_stages: null }), [])
  assert.deepEqual(changedStages(null), [])
  assert.equal(changedStagesLabel(drifted), "Bản dịch C6 · Viết lại C7 · TTS")
  assert.equal(changedStagesLabel({ changed_stages: ["tts"] }), "TTS")
})

test("rebind accepts the canonical semantic, translation and dubbing stages", () => {
  assert.equal(providerBindingFromStage({ from_stage: "semantic" }), "semantic")
  assert.equal(providerBindingFromStage({ from_stage: "translation" }), "translation")
  assert.equal(providerBindingFromStage({ from_stage: "dubbing" }), "dubbing")
  assert.equal(providerBindingFromStage({ from_stage: null }), null)
  assert.equal(providerBindingFromStage({ from_stage: "final_render" }), null)
  assert.equal(providerBindingFromStage(null), null)
})

test("job and current sides expose translation routes, rewrite routes and TTS binding", () => {
  assert.equal(bindingTranslationRoutes(drifted.job).length, 1)
  assert.equal(bindingRewriteRoutes(drifted.job).length, 1)
  assert.equal(bindingTranslationRoutes(drifted.current)[0].connection_id, "lmstudio-qwen")
  assert.equal(bindingRewriteRoutes(drifted.current).length, 0)
  assert.equal(bindingTtsLabel(drifted.job), "elevenlabs · voice-a")
  assert.equal(bindingTtsLabel(drifted.current), "edge · vi-VN-HoaiMyNeural")
  assert.equal(bindingTtsLabel({}), "")
})

test("current configuration is unavailable without a mapping or with a backend error", () => {
  assert.equal(currentProvidersAvailable(drifted), true)
  assert.equal(currentProvidersAvailable({ ...drifted, current_error: "Kết nối TTS đang lỗi" }), false)
  assert.equal(currentProvidersAvailable({ ...drifted, current: null }), false)
  assert.equal(currentProvidersAvailable(null), false)
})

test("rebind-providers posts source=settings and keeps stable Vietnamese copy", () => {
  assert.deepEqual(rebindProvidersPayload(), { source: "settings" })
  assert.deepEqual(rebindProvidersPayload("settings"), { source: "settings" })
  assert.equal(PROVIDER_BINDING_MISMATCH_LABEL, "Provider đã thay đổi từ lúc job được tạo")
  assert.equal(PROVIDER_BINDING_KEEP_LABEL, "Chạy lại với cấu hình của job")
  assert.equal(PROVIDER_BINDING_CURRENT_LABEL, "Dùng cấu hình hiện tại")
  assert.equal(PROVIDER_BINDING_CURRENT_UNAVAILABLE_LABEL, "Cấu hình hiện tại không khả dụng — không thể dùng cấu hình hiện tại.")
})

test("fallback-only drift displays the full translation and rewrite chains without credentials", () => {
  for (const field of ["translation_routes", "rewrite_routes"]) {
    const primary = { connection_id: "primary", model: "model-a", api_key: "HIDDEN_KEY" }
    const before = bindingSideSummary({ [field]: [primary, { connection_id: "old-fallback", model: "old" }] })
    const after = bindingSideSummary({ [field]: [primary,
      { connection_id: "new-fallback", model: "new", token: "HIDDEN_TOKEN" },
      { connection_id: "last-fallback", model: "last", secret: "HIDDEN_SECRET" }] })
    assert.match(before, /Primary: primary \/ model-a/)
    assert.match(after, /Primary: primary \/ model-a/)
    assert.match(before, /Fallback 1: old-fallback \/ old/)
    assert.match(after, /Fallback 1: new-fallback \/ new.*Fallback 2: last-fallback \/ last/)
    assert.notEqual(before, after)
    assert.doesNotMatch(before + after, /HIDDEN/)
  }
})
