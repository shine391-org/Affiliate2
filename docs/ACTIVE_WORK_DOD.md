# Affiliate2 — Active work, integration and Definition of Done

**Last reconciled:** 2026-10-09. **Canonical development branch:** `feat/localization-fast-mode`. **Master:** untouched; DO NOT merge to master until release gates pass.

> This is the authoritative current TODO/DoD for the localization + cross-flow integration. Older QA/audit reports are historical evidence, not instructions to restart an already-fixed item.

## 1. Integrated code — what exists

- Previous canonical branch through `e526c86`: C6 semantic/meaning guards, measured-first C7 timing, **commit rewrite only after candidate audio measures faster** (preserve last-good text/audio otherwise), speaker/voice fallback review and stage-only recovery, batch/source scratch cleanup, URL/DNS preflight.
- Rebased local commits `713c1cc` and `6eba93c`: strict and auto bounded, verbatim Edge TTS transport splitting; eligible no-audio/reset without partial audio only; single attempt per part, absolute deadline, no invented word-boundary evidence, provenance and QC review preserved.
- Cross-flow audit via [PR #20](https://github.com/shine391-org/Affiliate2/pull/20), merge commit `fbd129d`: Studio Manual/Smart Cut routes; batch auto-repair opt-in checkbox; host-based Douyin/TikTok detection; posting-time provenance from published records; prevent deletion of actively referenced source media; frontend recursive test discovery; honest QA evidence and AGENTS.md cleanup.
- The branch `fix/cross-flow-audit-20261009` is an **integrated archival branch**, not a second place for development. Continue all fixes exclusively on `feat/localization-fast-mode`.

**Important:** Above means **implemented in code**, NOT verified on the post-merge integrated head.

## 2. Current verdict: BLOCKED — TTS/provider and post-merge verification

### What is actually proved

- The isolated local pre-rebase Edge strict/transport patch reported **94 focused + 82 regression PASS**, Ruff/AST/diff PASS. Those runs do not certify the rebased and newly merged branch.
- The post-rebase focused regression + out-of-pipeline Edge CLI probe **was interrupted**; no PASS can be claimed.
- A real resumed job `a0778add-d75b-4b1e-b31c-9eb1e57baf39` reused **22 WAV**, produced **0 new WAV**, failed at **chunk 23**; verbatim split first piece `thì tôi sẽ quay` produced NoAudioReceived. C5/C6 were not rerun; mix/render/QC remain pending; no final video or publish job. The service/network cause is **not proven**.
- GitHub commit/PR merge confirms integration; it is NOT a local test or real-media acceptance.

### Highest-priority remaining gates (one bounded closure pass)

| Gate | State | DOD (evidence required) |
| --- | --- | --- |
| **G0 / integration safety** | PENDING | Local `git pull --ff-only` on `feat/localization-fast-mode`, worktree clean; focused changed-file regressions PASS, Ruff/AST/diff PASS, frontend tests/build PASS; confirm local HEAD matches origin |
| **G1 / TTS no-audio root cause** | BLOCKED | Same Python venv/Edge version: run one bounded **direct** CLI audio synthesis for `thì tôi sẽ quay` on both selected Vietnamese voices, check non-silent decodable output + diagnostics (SDK, network/proxy/TLS, timeouts). If CLI succeeds but adapter fails, fix only adapter. If CLI fails, classify external dependency; do not add unlimited retries or pretend a split fixed it |
| **G2 / provider decision and recovery** | PENDING G1 | Either Edge creates valid audio and chunk 23 resumes **without restarting C5/C6**, or a verified Vietnamese-capable alternative is **explicitly** chosen/rebound (C7+ invalidation and full TTS artifact regeneration; never mix cross-provider old WAV). Transport split audit must remain and QC review must not be silently waived |
| **G3 / real C5→QC video** | BLOCKED G2 | Complete an intact **5–10 min** source and persist final rendered video + manifest/QC + Library record; assert speech coverage, hard overflow=0, no unreported missing audio, no unexpected overlap, semantically safe C6/C7 rewrites, legitimate speakers/voices and QC review if quality uncertain. Report actual wall time |
| **G4 / cross-flow screens + lifecycles** | PENDING | Studio Manual/Smart Cut real navigation, batch auto-repair off-by-default and 3-job isolation, Download hostname/CDP, source-deletion guard under active job, published-at performance samples; run targeted frontend/backend regression + production build with actual result logs |

**Do not run all tests or begin another broad audit** unless a failing focused regression shows a shared dependency outside these scopes.

## 3. Scope boundaries and follow-up backlog

- **C6 translation/cue correctness:** code guards are implemented but semantic quality is only confirmed by source/target spot-checks on the real video. Deterministic defects found from the fixed corpus must be reproduced and fixed on failing cues, not globally weaken guards.
- **C7 timing:** measured TTS is authoritative; keep old audio if candidate is not faster; borrow genuine silence, bounded audio fit/retime and human review for irreducible short-cue overflow. Do not add unlimited rewrite/transport retries, global speed cap inflation, or silently truncate speech.
- **Multi-speaker/gender:** token/model and real-media listening for distinct voices still require independent proof; fallback reuse/default must remain reviewable rather than fatal.
- **Security/durability P2:** URL redirect/DNS rebinding network egress, and cross SQLite/filesystem deletion crash atomicity, still unresolved (require reproduction/test before modifying). Not grounds for re-opening C6/C7 endlessly.
- **Deferred/non-gating:** broad refactors of large modules, vendor cleanup, unrelated Phase D/E product work. Do not merge to `master`.

## 4. Minimal focused verification

From `E:\Affiliate2\app`, on the **integrated branch**:

```powershell
git fetch origin
git switch feat/localization-fast-mode
git pull --ff-only origin feat/localization-fast-mode
..\.venv\Scripts\python.exe -m pytest -q tests/test_edge_tts_strict_recovery.py tests/test_localization_postfit_measurement.py tests/test_localization_auto_c7_audio.py tests/test_localization_auto_repair_integration.py tests/test_c7_measured_cadence.py tests/test_c7_rewrite_recovery.py tests/test_localization_flow_hardening.py tests/test_cross_flow_platform_hosts.py tests/test_api_downloaded_router.py tests/test_phase_f_performance.py tests/test_task_runner.py tests/test_task_runner_downloads.py
cd frontend
npm test
npm run build
```

Run targeted Ruff/compile and `git diff --check`. Test only **after** updating local branch so local runs are against the correct integration commit. If a listed module/test is not available, report the specific path; don't silently skip it.

## 5. Final handoff format / stop conditions

One final report only, with: **HEAD + remote match**, **what changed**, **focused test counts/errors**, **direct Edge CLI evidence**, **real chunk 23 outcome**, **5–10 min E2E + QC + Library**, **G0–G4 PASS/BLOCKED**, **single next root-cause action**.

- **Code gate passes** only when combined changes pass focused tests/build and review.
- **Product gate passes** only on an actual output video, not unit mocks or pre-existing WAVs.
- Any unresolved HARD overflow or transport no-audio must be marked **BLOCKED**, not described as success.
- No force push, no master merge, no implicit provider change, no new branch or rewrite of history for this closeout.
