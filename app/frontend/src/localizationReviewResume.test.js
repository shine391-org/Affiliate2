import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8")
const studio = read("./pages/StudioLocalize.jsx")
const studioShell = read("./pages/Studio.jsx")
const activity = read("./components/librarynext/ActivityPanel.jsx")
const assetModel = read("./components/librarynext/assetModel.js")
const websocket = read("./hooks/useWebSocket.js")

test("translation review is an explicit resumable Studio state", () => {
  assert.ok(studio.includes("/continue-after-translation"))
  assert.ok(studio.includes("useState(false)"), "translation review should be opt-in for a normal uninterrupted HQ run")
  assert.ok(studio.includes("workflowOccupied"))
  assert.ok(studio.includes("Duyệt xong · Tiếp tục lồng tiếng"))
  assert.ok(studio.includes("không chạy lại ASR hay dịch"))
  assert.ok(studio.includes('label="Chờ duyệt bản dịch"'))
})

test("review-paused jobs are action-needed instead of fake-complete", () => {
  assert.ok(assetModel.includes('awaiting_review: "attention"'))
  assert.ok(assetModel.includes("translation_review_pending"))
  assert.ok(assetModel.includes("action_href: actionHref"))
  assert.ok(activity.includes('Section label="Cần thao tác"'))
  assert.ok(read("./pages/Library.jsx").includes("Mở việc cần xử lý"))
  assert.ok(activity.includes('job.state === "attention"'))
  assert.ok(activity.includes("job.action_label"))
  assert.ok(assetModel.includes('"Duyệt & tiếp tục"'))
})

test("review pause has realtime navigation back to Studio", () => {
  assert.ok(websocket.includes('ws.on("job_attention"'))
  assert.ok(websocket.includes('label: "Mở bản dịch"'))
  assert.ok(studioShell.includes("/api/localization/jobs/"))
  assert.ok(studioShell.includes("downloaded_video_id"))
  assert.ok(studioShell.includes('next.set("source", recovered)'))
})

test("QC completion remains a real output but is surfaced as action-needed", () => {
  assert.ok(assetModel.includes('qcStatus === "FAILED"'))
  assert.ok(assetModel.includes('out?.qc_human_approved !== true'))
  assert.ok(assetModel.includes('attention_kind: attentionKind'))
  assert.ok(assetModel.includes('"Mở Duyệt QC"'))
  assert.ok(websocket.includes("result.qc_status"))
  assert.ok(websocket.includes('"Mở Duyệt QC"'))
  const expert = read("./components/tools/LocalizationExpertPanel.jsx")
  assert.ok(expert.includes("/qc-approval"))
  assert.ok(expert.includes("Chấp nhận cảnh báo QC"))
})

test("paused duplicate starts reopen review instead of spawning a second run", () => {
  assert.ok(studio.includes('data.status === "awaiting_review"'))
  assert.ok(studio.includes('next.set("step", reusedReview ? "translation" : "source")'))
  assert.ok(studio.includes("Tác vụ này đã dịch xong và đang chờ duyệt"))
})

test("skipped localization stages are not painted as completed work", () => {
  assert.ok(studio.includes('record?.metadata?.skipped'))
  assert.ok(studio.includes('"Bỏ qua"'))
})

test("review recovery uses persisted source and clears stale review preferences", () => {
  assert.ok(studio.includes("/api/localization/jobs/${jobId}/source"))
  assert.ok(studioShell.includes("recovered === sourceId"))
  assert.ok(studio.includes("setReviewTranslation(false)"))
})

test("jobs poll reconciles missed review and terminal websocket events", () => {
  const jobsHook = read("./hooks/useJobs.js")
  assert.ok(jobsHook.includes("persisted && !ACTIVE_STATUSES.has(persisted.status)"))
  assert.ok(jobsHook.includes("store.removeJob(jobId)"))
})

test("QC-resolved localization has a canonical Publish handoff", () => {
  assert.ok(studio.includes("/publish?video="))
  assert.ok(studio.includes("/publish-registration"))
  const expert = read("./components/tools/LocalizationExpertPanel.jsx")
  assert.ok(expert.includes("/publish-registration"))
  assert.ok(expert.includes("Mở Xuất bản"))
  assert.ok(assetModel.includes('localization: { label: "Bản địa hóa"'))
})

test("warning-only QC becomes resolved only after human acknowledgement", () => {
  assert.ok(studio.includes('qc?.status === "NEEDS_REVIEW" && detail?.qc_human_approved'))
  const expert = read("./components/tools/LocalizationExpertPanel.jsx")
  assert.ok(expert.includes("/qc-approval"))
  assert.ok(expert.includes("Chấp nhận cảnh báo QC"))
})

test("human review actions are bound to exact artifact revisions", () => {
  assert.ok(studio.includes("translation_review_revision"))
  const expert = read("./components/tools/LocalizationExpertPanel.jsx")
  assert.ok(expert.includes("translation_review_revision"))
  assert.ok(expert.includes("qc_review_revision"))
  assert.ok(expert.includes("revision: detail?.translation_review_revision"))
  assert.ok(expert.includes("revision: detail?.qc_review_revision"))
})

test("QC-resolved output can recover a missing Publish registration without rerunning media", () => {
  assert.ok(studio.includes('"Đưa sang Xuất bản"'))
  assert.ok(studio.includes("/publish-registration"))
  const expert = read("./components/tools/LocalizationExpertPanel.jsx")
  assert.ok(expert.includes("chưa có bản ghi Xuất bản"))
})

