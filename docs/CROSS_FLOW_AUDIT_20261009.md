# Affiliate2 — Cross-flow audit and targeted fixes (2026-10-09)

> **HISTORICAL SNAPSHOT — superseded by [ACTIVE_WORK_DOD.md](ACTIVE_WORK_DOD.md).** The cross-flow work was merged into `feat/localization-fast-mode` via [PR #20](https://github.com/shine391-org/Affiliate2/pull/20), merge `fbd129d`. The old statements below saying `9282657` is unpublished or that strict transport splitting is pending describe the **pre-integration** situation and are no longer current. The rebase landed as `713c1cc` / `6eba93c`; full TTS/media acceptance is still BLOCKED. **Do not use this old document as an active DoD.**

## Scope and provenance

- This patch branch starts from remote `feat/localization-fast-mode` SHA `fbd77fe861b7300b40459c8a50a99d1c8932404d`.
- A separate, **unpublished local** commit `9282657bafcfa05f59b435856946d6ceb54b791b` was reported by local QA. This patch branch **does not include** it. Reconcile on the Windows workstation before integration.
- Static GitHub-source review and changes only; no local pytest, Node tests, live TTS or video render were run by this review. No merge to master.

## Fixed on this branch (requires local verification)

1. **Studio mode routing:** `/studio/manual` and `/studio/smart-cut` render the already-implemented Studio view instead of redirecting to Localize. Legacy `/clips` resolves to Manual.
2. **Batch auto-repair UI:** explicit **off-by-default** checkbox sends `auto_repair=true/false` through the existing FastAPI contract. Never interpret as QC approval.
3. **Download platform identification:** resolve hostname, not `m.`/first-label heuristics or tokens embedded in unrelated URL paths. Preserve Douyin CDP and TikTok cookie selection for verified hostnames.
4. **Performance recommended posting time:** use verified `PublicationRecord.published_at`, never `GeneratedVideo.created_at`; require actual 1–24h metric snapshots, normalize first-day views by elapsed hours, require >=3 qualified samples, and label confidence heuristic.
5. **Download-source deletion:** block if any pending/running/review-paused Job references the source id or file path; take a SQLite `BEGIN IMMEDIATE` before checking and removing source. Preserve media files referenced by other DownloadedVideo or GeneratedVideo rows (including sidecars). This is a conservative guard, **not** a cross-filesystem/SQLite atomic transaction.
6. **Frontend test discovery:** run nested Node `*.test.js` files as well as root ones.
7. **QA evidence honesty:** historical metadata-only video smoke cannot mark localization `PASS` or claim a secondary voice was tested.
8. **Agent documentation:** remove stale hardcoded implementation branch.

Regression cases were added for routes, batch form wiring, hostname routing, posting-time provenance and source deletion while an active job references it.

## Still blocked / not implemented in this branch

- **Real E2E localization:** Edge TTS websocket no-audio resets under the existing strict profile; recovery retained 22/31 WAV artifacts. Must pass TTS -> C7 -> mix -> render -> QC -> Library, and inspect publish eligibility. Do not change provider/voice/translation implicitly.
- **Strict-mode TTS transport-only split:** investigate separately, preserve verbatim text and QC `NEEDS_REVIEW` if transport splitting changes acoustic boundaries.
- **Multi-speaker automated diarization:** requires a suitable configured Hugging Face token/model and real-media speaker QA.
- **Historical overflow corpus, batch>=3, controlled cancellation/restart, real Douyin/TikTok browser/CDN:** require on-machine QA evidence.
- **SSRF defense:** current URL + DNS preflight does not guarantee redirect/DNS-rebinding protection without network egress enforcement.
- **Deletion durability:** SQLite row deletion and filesystem unlink are not atomic across a crash; further changes must be justified by a failing integration test.
- **Large-module organization:** `localization_service.py`, `tool_runners.py`, `task_runner.py`, `settings.py`, and `ytdlp_service.py` mix distinct responsibilities. Avoid speculative structural refactors while the real pipeline is blocked. If splitting later, preserve existing import/API/manifest/serialization contracts with equivalence tests.
- **Legacy donor trees:** `vendor/` is a reference snapshot; `src/radar/` is transitional. Do not delete them without call-site and parity evidence.

## Focused local verification

From `E:\Affiliate2\app` after bringing changes into a reconciled worktree:

```powershell
..\.venv\Scripts\python.exe -m pytest -q tests/test_cross_flow_platform_hosts.py tests/test_task_runner.py tests/test_task_runner_downloads.py tests/test_api_downloaded_router.py tests/test_phase_f_performance.py
cd frontend
npm test
npm run build
```

Also run `git diff --check`, targeted Ruff, check Studio Manual/Smart Cut in the real browser and verify the Windows source-deletion guard with SQLite. Inspect any failed test and fix actual defects, not fixtures that conceal them.

**DoD:** focused tests + frontend build PASS; no incorrect platform routing; batch checkbox persisted; posting-time output backed by actual published times; source deletion blocked while active job uses it; no false QA-PASS; independent diff review. Full real-media merge readiness remains **BLOCKED** until the separately tracked TTS/QC acceptance passes.
