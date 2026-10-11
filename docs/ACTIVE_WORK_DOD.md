# Affiliate2 — Active work, integration and Definition of Done

**Last reconciled:** 2026-10-09. **Canonical development branch:** `feat/localization-fast-mode`. **Master:** untouched; DO NOT merge to master until release gates pass.

> This is the authoritative current TODO/DoD for the localization + cross-flow integration. Older QA/audit reports are historical evidence, not instructions to restart an already-fixed item.

## Engineering fixes — 2026-10-09 review closure

The follow-up implementation fixes the reproduced shared-silence allocation defect,
optional candidate-TTS recovery, bundle promotion rollback/invalidation, missing
multi-cue cadence warning, and stale unmeasured-acceptance tests (TIM-01–TIM-04).
It also adds bounded configured-route fallback for semantic rejections, local
source context for C6, source/name revalidation for C7, and the reproduced
predicate/difference guard cases. See [QA_LOCALIZATION_CLOSURE_20261009.md](QA_LOCALIZATION_CLOSURE_20261009.md).

These code fixes do **not** close G1/G2 provider diagnosis or G3/T5 real-video
acceptance. Generic semantic correctness, simultaneous-speaker mixing, local
video retiming and independent target-audio alignment remain unproved/unimplemented;
no automatic READY or zero-intervention claim follows from this change.


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
| **G3 / real C5→QC video** | BLOCKED G2 and T0–T4 | Complete an intact **5–10 min** source and persist final rendered video + manifest/QC + Library record; assert speech coverage, hard overflow=0, no unreported missing audio, no unexpected overlap, semantically safe C6/C7 rewrites, legitimate speakers/voices and QC review if quality uncertain. Report actual wall time |
| **G4 / cross-flow screens + lifecycles** | PENDING | Studio Manual/Smart Cut real navigation, batch auto-repair off-by-default and 3-job isolation, Download hostname/CDP, source-deletion guard under active job, published-at performance samples; run targeted frontend/backend regression + production build with actual result logs |

**Do not run all tests or begin another broad audit** unless a failing focused regression shows a shared dependency outside these scopes.

## 2A. Six product-quality contracts — PRIMARY PRODUCT DoD

**Scope:** These six obligations take precedence over descriptions such as "tests passed", "fix implemented", "chunk fit", "timing review", or "real video rendered". The T0–T5 timing/semantic gates and TIM-01…TIM-05 issue tickets below are the **engineering implementation** of these six obligations, not a substitute for them. A genuine external TTS failure (G1) is a separate infrastructure blocker, not proof of correctness for any obligation.

| Product case | What code can / cannot solve | Current assessment (2026-10-09, static HEAD review only) | Concrete DoD |
| --- | --- | --- | --- |
| **P-Q1: Wrong sentence split / context loss** | Most deterministic splitting can be corrected in `semantic_segmentation.normalize_semantic_cues`, `refine_semantic_cues_with_ai`, `dubbing_planner.build_plan`; preserve real word boundaries, speaker/voice changes and source punctuation. ASR word errors need source/audio evidence. | **IMPLEMENTED, UNVERIFIED E2E**: word-ID-only semantic grouping, hard-speaker boundaries, strong-end regex, continuation cap/orphan rescue exist; splitting can still sound unnatural on live voice. | Reproduce live wrong-split examples; words/IDs one-to-one with source; no speaker crossing, no lost fragment/negation/question particle; deterministic fixture and real-source review (T0/T4/T5). |
| **P-Q2: Wrong negation, number, name, relationship** | Large portion addressable with contextual C6 guard and *re-translate only rejected IDs*; fuzzy idioms, pronouns and implicit context require human/semantic review. Avoid falsely blocking correct Vietnamese. | **PARTIAL / NOT PROVEN**: `_translation_guard_checks`, `_translation_candidates`, `_translate_batch` targeted semantic repair and name dictionary exist; tests include context fixtures. Not a proof all mistranslations are caught. | Source→Vietnamese spot check and mutation fixtures for negation including idioms, digits/units/ages, names, subjects/objects, questions and answer particles; rejected cues are repaired within cap or explicitly fail/review; no global guard weakening or sibling retranslate (T0/T5). |
| **P-Q3: AI makes meaning-changing shorter rewrite** | Reject candidate by semantic invariants; retain last approved C6/C7; audio must also be **measured faster**, not just fewer words. Semantic subtleties can still need listening/review. | **IMPLEMENTED, TECHNICAL GAPS OPEN**: `check_rewrite_meaning`, `rewrite_measured_not_faster` and candidate WAV measurement exist; optional candidate-TTS failure/promotion atomicity risks TIM-01/TIM-02 remain. | Valid shorthand accepted only if meaning guard passes and real speech gets shorter. Wrong meaning, slower TTS, provider failure or canceled trial => unchanged last-good audio + C6 caption + current accepted audit; no truncated meaning (T1/T2). |
| **P-Q4: TTS longer than source window** | Use actual silence budget, measured rewrite, bounded atempo, bounded fallback retime; solve only if a feasible schedule exists. | **IMPLEMENTED ALGORITHM, NOT VERIFIED 5–10 MIN VIDEO**: `allocate_silence`, `fit_timeline`, postfit probes and QC overflow tiers exist. Multi-cue onsets may still be estimated (TIM-03). | Measured raw and fitted audio, no overlap/cut, normal <= speed cap, emergency => review, hard overflow => fail; cue-local cadence evidence where needed, no silent success (T3/T4/T5). |
| **P-Q5: 200ms source vs 2s Vietnamese** | **Sometimes unsatisfiable** inside a fixed timeline. Can recover only with genuine silence, safe rewriting or explicitly authorized image retime. Never speed x10, delete words, falsify subtitle timing, or claim auto-fit. | **INTENTIONAL LIMITATION, MUST FAIL HONESTLY**: existing max-speed/retime/soft/hard reporting; no general automated guarantee. | Controlled impossible-window fixture remains marked `FAILED` or `NEEDS_REVIEW` per documented bounded policy, with original and needed milliseconds, proposed slowdown and human resolution. No overflow hidden by QA (T3/T5). |
| **P-Q6: Unnatural voice, mispronunciation, prosody** | Some improvements via punctuation, phrase grouping, speaker/voice binding, pronunciation normalization and provider selection. **Cannot fully guarantee by rules**; requires real audio and the voice/provider actually working. | **PARTIAL, PROVIDER BLOCKED**: continuation handling, speaker mapping, QC preview/voice warnings exist. Edge `NoAudioReceived` still prevents the current real acceptance. | Listen to >=20 representative source/target cues incl. question, short answer, speaker turn and Chinese names; confirm tone/emphasis/natural breaks; wrong audible speech => targeted repair or explicit `NEEDS_REVIEW`, never automatic quality PASS. Validate actual selected Vietnamese TTS first (G1/G2/T5). |

### Non-negotiable closure order / no new phase

1. **Code gate**: Reproduce and resolve only TIM-01 to TIM-04 as needed; run existing C6 source/context/guard and semantic-segmentation regressions on merged head, plus C7 cadence/fit/regeneration/resume regressions. Add only concrete missing failing cases from P-Q1/P-Q2; no speculative multi-file refactor.
2. **TTS gate**: Direct out-of-pipeline sample from actual configured voices and provider, then resume the failed chunk or explicit rebind. Avoid changing C6 when only TTS fails.
3. **Product gate**: ONE actual 5–10 min video + source-meaning review + cue-level measured timing/QC + voice listening and Library artifact. Record individual **P-Q1…P-Q6 PASS / NEEDS_REVIEW / BLOCKED** with sampled errors and actual evidence; `NEEDS_REVIEW` is not unconditional approval to publish.
4. **Stop**: If provider no-audio blocks E2E, report the blocking external dependency and completed code-gate evidence. If meaning/timing is inherently infeasible, flag it; do not spin endless rewrites/tests.

---

## 3. REOPENED: source sentences and timing — code review on integrated HEAD

**Review basis:** static reading of `localization_service.py`, `dubbing_planner.py`, `dubbing_cadence.py`, `semantic_segmentation.py`, `localization_qc_service.py` and related tests on integrated head `5a6ae4e`. The findings below are **not** a claim that pytest or real-media QA ran.

### Already implemented — preserve these contracts

| Contract | State in code | Still requires proof |
| --- | --- | --- |
| Source/semantic cue grouping preserves original words and speaker boundaries | IMPLEMENTED: AI ID-only grouping + deterministic fallback; punctuation, continuation and max-span guards | Fast speech, CJK punctuation, micro-cues, multi-speaker fixtures |
| Rewrite starts ONLY on **measured raw TTS overflow** | IMPLEMENTED | Good-fit measured chunk never rewritten because of a bad estimate |
| Candidate rewrite preserves meaning and must have **measured duration strictly lower** than previous selected audio | IMPLEMENTED in `_adapt_chunk_items` / `synthesize_timed_dub` | Shorter text but slower speech rejected; identity/audio/text/audit agree |
| Real chunk fit with silence borrowing, bounded speed/retime, post-FFmpeg measurement | IMPLEMENTED | No cropping, silence, overlap or artifact mixing |
| Measured per-cue placement when word boundaries exactly match synthesized audio | IMPLEMENTED; otherwise falls back to `allocated` cue timing estimates | Actual onsets, rather than only chunk duration, are verified |
| Edge bounded no-audio transport splitting | IMPLEMENTED | Real network/provider and final media still unverified |

### Open timing/code issues — focused regression before fixes

| ID / priority | Source-backed finding | Definition of Done |
| --- | --- | --- |
| **TIM-01 P1** candidate TTS transport failure aborts optional rewrite + whole C7 | After `_adapt_chunk_items` updates spoken text, `_generate_tts_chunk_atomic(candidate)` has no local provider-error fallback. `finally` restores text, but exception propagates, skipping normal fit/retime even when last-good WAV exists | Reproduce original valid WAV + candidate `NoAudioReceived`. Keep old audio, identity, text; record degraded rewrite; use bounded fit/overflow/QC. Never swallow cancellation, invalid original, auth/config or irreversible faults |
| **TIM-02 P1** partial promotion crash-consistency risk | WAV `candidate_path.replace(path)` precedes `.words.json` and `.input.json` promotions. Injected I/O failure in between can leave new audio with stale/missing sidecars and restored old text | Fault-inject each promotion. On success bytes, metadata, accepted revision match. On failure restore original atomically where possible or invalidate artifact; no false cache reuse or silent text/audio mismatch |
| **TIM-03 P1** within-chunk per-cue timing can be *allocated*, not measured | `fit_timeline` distributes `estimated_duration_ms` weights and later scales by measured overall chunk length. Without verified word events `tts_duration_basis=allocated`; QC flags drift >750ms, but lacks a general `multi_cue_unverified_timing` review flag | Reproduce multi-cue chunk with shifted speech onset and no events. Preserve `allocated` provenance, mark QC review until per-cue timing verified or add bounded independent alignment; no `READY` from whole-chunk fit alone |
| **TIM-04 P1** stale regression expectation violates measured-before-commit contract | `test_localization_measured_speakers.py::test_audit_survives_failure_after_an_accepted_rewrite` asserts `rewrite_count==1` and accepted entry with `duration_after_ms=None` after candidate synthesis fails. New contract forbids accepting an unmeasured candidate | RED→GREEN: failed candidate yields 0 newly accepted rewrites; add distinct test for one *measured-accepted* candidate surviving a later failure. Do not weaken production gate just to satisfy old test |
| **TIM-05 P1** phrase rhythm and real speech quality | Continuation/speaker splits, orphan rescue and cadence metadata have simulated tests, but no integrated 5–10 min real-video evidence | Real-media listening + exact failing cue fixtures. No lost particles, names, negation, numbers, abrupt jump, wrong voice or hidden overly-fast speech |

These are **code release gates**, separate from **G1 Edge provider outage**. External no-audio can block the full video but cannot be used as evidence that C7 timing is correct.

### Mandatory timing/sentence DoD

**T0 — Source/cue integrity:** C5→C6 preserves each aligned source word exactly once and in order, including Chinese negatives, counts, names, questions and micro-cues. No cross-speaker merging, dropped cue, invented meaning or mutation of approved C6. RED→GREEN fixtures for defects, spot-check source audio + Vietnamese text.

**T1 — Rewrite monotonicity:** Persist `before_text`, `after_text`, `before_ms`, `after_ms`, `meaning_verdict`, `accepted`, `reason`. Select only when semantics pass AND `after_ms < before_ms`; any rejected/failed candidate retains last-good audio/text and is not counted as accepted. One job/voice binding and current-revision audit.

**T2 — Audio identity/atomicity:** Fault-inject no-audio/reset, cancellation, silent/truncated output, SHA/word metadata mismatch and sidecar promotion failure. Audio, translated/spoken text, cache identity and rewrite audit must always agree across resume; else fail closed. Cancellation is never swallowed.

**T3 — Measured time bounds:** Use real synthesis and post-FFmpeg probes; no clipping/overlap; regular acceleration <= configured max; emergency soft overflow => `NEEDS_REVIEW`; hard overflow => `FAILED` (zero on accepted final output). Respect source speaker/time boundaries and immutable expert locks. `allocated` must never be mislabeled `measured`.

**T4 — Cue-level onset quality:** Multi-cue full-chunk duration success does NOT prove each cue on time. For every cadence-critical multi-cue chunk use hash-bound provider events or validated independent alignment; otherwise mark a review warning. Inspect >=20 representative cues across beginning/middle/end, short replies, questions, fast speech, long pauses and speaker changes. Report observed onset differences in milliseconds, any audible clipping, missing words or misplaced sentence breaks.

**T5 — Finished video:** On one intact 5–10 minute video with working TTS, C5→C6→C7→mix→render→QC→Library must complete, with zero missing spoken segments, zero hard overflows, semantically safe rewrites, no unreported overlap/cut and documented measured-vs-estimated cues. Speaker/voice and speed warning gate remains honest. Final status may be `NEEDS_REVIEW` for preview/soft quality, never a false `READY`; truly bad semantics/timing => `FAILED`. Record provider/voice and wall-clock runtime.

**Execution:** Verify T0–T4 on focused fixtures while independently diagnosing G1; once TTS works, execute T5. No broad refactor or repeated project-wide QA without a failing dependency trace.

---

## 4. Scope boundaries and follow-up backlog

- **C6 translation/cue correctness:** code guards are implemented but semantic quality is only confirmed by source/target spot-checks on the real video. Deterministic defects found from the fixed corpus must be reproduced and fixed on failing cues, not globally weaken guards.
- **C7 timing:** measured TTS is authoritative; keep old audio if candidate is not faster; borrow genuine silence, bounded audio fit/retime and human review for irreducible short-cue overflow. Do not add unlimited rewrite/transport retries, global speed cap inflation, or silently truncate speech.
- **Multi-speaker/gender:** token/model and real-media listening for distinct voices still require independent proof; fallback reuse/default must remain reviewable rather than fatal.
- **Security/durability P2:** URL redirect/DNS rebinding network egress, and cross SQLite/filesystem deletion crash atomicity, still unresolved (require reproduction/test before modifying). Not grounds for re-opening C6/C7 endlessly.
- **Deferred/non-gating:** broad refactors of large modules, vendor cleanup, unrelated Phase D/E product work. Do not merge to `master`.

## 5. Minimal focused verification

From `E:\Affiliate2\app`, on the **integrated branch**:

```powershell
git fetch origin
git switch feat/localization-fast-mode
git pull --ff-only origin feat/localization-fast-mode
..\.venv\Scripts\python.exe -m pytest -q tests/test_edge_tts_strict_recovery.py tests/test_localization_postfit_measurement.py tests/test_localization_auto_c7_audio.py tests/test_localization_auto_repair_integration.py tests/test_c7_measured_cadence.py tests/test_c7_rewrite_recovery.py tests/test_localization_measured_speakers.py tests/test_localization_flow_hardening.py tests/test_cross_flow_platform_hosts.py tests/test_api_downloaded_router.py tests/test_phase_f_performance.py tests/test_task_runner.py tests/test_task_runner_downloads.py
cd frontend
npm test
npm run build
```

Run targeted Ruff/compile and `git diff --check`. Test only **after** updating local branch so local runs are against the correct integration commit. If a listed module/test is not available, report the specific path; don't silently skip it.

## 6. Final handoff format / stop conditions

One final report only, with: **HEAD + remote match**, **what changed**, **focused test counts/errors**, **direct Edge CLI evidence**, **real chunk 23 outcome**, **5–10 min E2E + QC + Library**, **G0–G4, P-Q1–P-Q6 and T0–T5 PASS/NEEDS_REVIEW/BLOCKED**, **single next root-cause action**.

- **Code gate passes** only when combined changes pass focused tests/build and review.
- **Product gate passes** only on an actual output video, not unit mocks or pre-existing WAVs.
- Any unresolved HARD overflow or transport no-audio must be marked **BLOCKED**, not described as success.
- No force push, no master merge, no implicit provider change, no new branch or rewrite of history for this closeout.

