import test from "node:test"
import assert from "node:assert/strict"

import {
  buildLocalizationRenderPayload,
  canRerenderLocalization,
  captionPreviewStyle,
  hydrateLocalizationJobConfig,
  localizationJobFailure,
  maskPreset,
  pickPreviewReframeMethod,
  previewObjectFit,
  resolveAiStageSelection,
  resolveProfileTargetAspect,
} from "./localizationPreview.js"

test("preview reframe mirrors backend auto geometry", () => {
  assert.equal(pickPreviewReframeMethod(1920, 1080, "9:16"), "blur_fill")
  assert.equal(previewObjectFit("blur_fill"), "contain")
  assert.equal(pickPreviewReframeMethod(1080, 1920, "16:9"), "crop")
  assert.equal(previewObjectFit("crop"), "cover")
  assert.equal(pickPreviewReframeMethod(1080, 1080, "9:16"), "blur_fill")
  assert.equal(pickPreviewReframeMethod(1080, 1920, "1:1"), "crop")
  assert.equal(pickPreviewReframeMethod(1920, 1080, "1:1"), "blur_fill")
  assert.equal(pickPreviewReframeMethod(1080, 1920, "source"), "source")
})

test("aspect reset uses a fresh bottom mask preset", () => {
  const first = maskPreset("bottom")
  first.x = 0.9
  assert.deepEqual(maskPreset("bottom"), { x: 0.05, y: 0.72, width: 0.9, height: 0.15 })
})

test("mask-region caption preview uses normalized mask geometry and offset", () => {
  assert.deepEqual(
    captionPreviewStyle(
      { x: 0.05, y: 0.72, width: 0.9, height: 0.15 },
      { mode: "mask_region", vertical_offset: -0.25 },
    ),
    {
      left: "50%",
      top: "75.75%",
      width: "90%",
      transform: "translate(-50%, -50%)",
    },
  )
})

test("mask-region caption preview clamps inside mask safe padding", () => {
  const mask = { x: 0.05, y: 0.72, width: 0.9, height: 0.15 }
  assert.equal(captionPreviewStyle(mask, { mode: "mask_region", vertical_offset: -0.25 }).top, "75.75%")
  assert.equal(captionPreviewStyle(mask, { mode: "mask_region", vertical_offset: 0.25 }).top, "83.25%")
})

test("existing job hydration remains source of truth after delayed profile defaults", () => {
  const hydrated = hydrateLocalizationJobConfig({
    manifest: {
      job_id: "job-16x9",
      request: {
        profile_id: "profile-existing",
        auto_repair: true,
        target_aspect: "16:9",
        mask_box: { x: 0.05, y: 0.72, width: 0.9, height: 0.15, mode: "solid" },
        caption_placement: { mode: "mask_region", vertical_offset: 0.1 },
      },
      effective_profile: { target_aspect: "9:16" },
    },
  })

  assert.equal(hydrated.profileId, "profile-existing")
  assert.equal(hydrated.autoRepair, true)
  assert.equal(hydrated.targetAspect, "16:9")
  assert.equal(resolveProfileTargetAspect({
    currentAspect: hydrated.targetAspect,
    selectedProfile: { target_aspect: "9:16" },
    aspectOverridden: false,
    jobConfigHydrated: true,
  }), "16:9")
})

test("rerender readiness mirrors canonical stage prerequisites", () => {
  const complete = { status: "complete" }
  const pending = { status: "pending" }
  assert.equal(canRerenderLocalization({
    manifest: { request: { mode: "full_dub" }, stages: { translation: complete, dubbing: complete, audio_mix: complete } },
    translation_review_pending: false,
  }), true)
  assert.equal(canRerenderLocalization({
    manifest: { request: { mode: "full_dub" }, stages: { translation: complete, dubbing: pending, audio_mix: complete } },
    translation_review_pending: false,
  }), false)
  assert.equal(canRerenderLocalization({
    manifest: { request: { mode: "captions_only" }, stages: { translation: complete, dubbing: complete, audio_mix: complete } },
    translation_review_pending: false,
  }), true)
  assert.equal(canRerenderLocalization({
    manifest: { request: { mode: "full_dub" }, stages: { translation: complete, dubbing: complete, audio_mix: complete } },
    translation_review_pending: true,
  }), false)
})

test("render payload explicitly marks normalized mask and caption placement", () => {
  assert.deepEqual(
    buildLocalizationRenderPayload({
      enableMask: true,
      maskBox: maskPreset("bottom-full"),
      maskMode: "solid",
      captionPlacementMode: "mask_region",
      captionVerticalOffset: 0.04,
      targetAspect: "16:9",
    }),
    {
      target_aspect: "16:9",
      mask_box: {
        x: 0,
        y: 0.72,
        width: 1,
        height: 0.22,
        mode: "solid",
        normalized: true,
      },
      caption_placement: { mode: "mask_region", vertical_offset: 0.04 },
    },
  )
})

// A job that died on a CUDA DLL load used to render as a healthy page: every
// stage chip said "đang chờ" and no error text was shown anywhere.
test("failed localization job surfaces the real backend error and step", () => {
  const failure = localizationJobFailure({
    job_status: "failed",
    job_current_step: "asr",
    job_error_message: "Library cublas64_12.dll is not found or cannot be loaded",
    manifest: { stages: { translation: { status: "pending" }, dubbing: { status: "pending" } } },
  })
  assert.equal(failure.step, "asr")
  assert.equal(failure.message, "Library cublas64_12.dll is not found or cannot be loaded")
})

test("running and successful jobs never produce a failure banner", () => {
  assert.equal(localizationJobFailure({ job_status: "running", job_current_step: "asr" }), null)
  assert.equal(localizationJobFailure({ job_status: "complete" }), null)
  assert.equal(localizationJobFailure(null), null)
  assert.equal(localizationJobFailure({}), null)
})

test("missing error message falls back to the failed manifest stage, then to stable text", () => {
  const fromStage = localizationJobFailure({
    job_status: "failed",
    manifest: { stages: { dubbing: { status: "failed", error: "TTS provider timeout" } } },
  })
  assert.equal(fromStage.stage, "dubbing")
  assert.equal(fromStage.step, "Lồng tiếng")
  assert.equal(fromStage.message, "TTS provider timeout")

  const bare = localizationJobFailure({ job_status: "failed", job_error_message: null })
  assert.equal(bare.step, "")
  assert.equal(bare.message, "Tác vụ bản địa hóa thất bại mà không có thông báo lỗi.")
  assert.ok(!bare.message.includes("undefined"))
  assert.ok(!bare.message.includes("null"))
})

test("canonical backend fields win over the manifest stage fallback", () => {
  const failure = localizationJobFailure({
    job_status: "failed",
    job_current_step: "final_render",
    job_error_message: "ffmpeg exit code 1",
    manifest: { stages: { asr: { status: "failed", error: "stale" } } },
  })
  assert.equal(failure.step, "final_render")
  assert.equal(failure.message, "ffmpeg exit code 1")
})


test("AI stage selection prefers actual fallback audit over configured primary", () => {
  const metadata = {
    translation_provider: "hermes-main",
    translation_model: "longcat",
    translation_routes: [
      { connection_id: "hermes-main", model: "longcat" },
      { connection_id: "lmstudio-qwen", model: "qwen3:8b" },
    ],
    translation_ai: {
      selected_connection_id: "lmstudio-qwen",
      failure_chain: [{ connection_id: "hermes-main", error_type: "TimeoutError" }],
    },
  }
  const selected = resolveAiStageSelection(metadata, {}, "translation")
  assert.equal(selected.connectionId, "lmstudio-qwen")
  assert.equal(selected.model, "qwen3:8b")
  assert.equal(selected.failureCount, 1)
})

test("C7 stage selection still resolves actual fallback on failed dubbing metadata", () => {
  const metadata = {
    rewrite_provider: "hermes-main",
    rewrite_model: "longcat",
    rewrite_routes: [
      { connection_id: "hermes-main", model: "longcat" },
      { connection_id: "lmstudio-gemma", model: "gemma-4-e4b" },
    ],
    rewrite_ai: {
      selected_connection_id: "lmstudio-gemma",
      sticky_route_index: 1,
      failure_chain: [{ connection_id: "hermes-main", error_type: "ServiceUnavailable" }],
    },
  }
  const selected = resolveAiStageSelection(metadata, {}, "rewrite")
  assert.equal(selected.connectionId, "lmstudio-gemma")
  assert.equal(selected.model, "gemma-4-e4b")
  assert.equal(selected.failureCount, 1)
})
