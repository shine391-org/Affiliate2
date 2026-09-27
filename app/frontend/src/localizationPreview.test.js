import test from "node:test"
import assert from "node:assert/strict"

import {
  buildLocalizationRenderPayload,
  captionPreviewStyle,
  maskPreset,
  pickPreviewReframeMethod,
  previewObjectFit,
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
      { mode: "mask_region", vertical_offset: -0.05 },
    ),
    {
      left: "50%",
      top: "74.5%",
      width: "90%",
      transform: "translate(-50%, -50%)",
    },
  )
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
