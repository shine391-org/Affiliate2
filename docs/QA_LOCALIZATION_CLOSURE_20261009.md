# Localization review closure — 2026-10-09

Branch: `feat/localization-fast-mode`. Review/fix base: `7ca884c0bb19e482196e87f0e7fb58ae03a371bf`.
No master merge. No new runtime dependency or second media engine.

## Implemented

- Shared silence: satisfy normal-speed ceiling demands for all chunks before
  spending remaining slack on acceptable/natural speed. Preserve pauses, source
  anchors, locks, per-chunk borrow limits and targeted-regeneration ownership.
  Reproducer: two 1s source windows at 0s/2s; measured audio 2.1s/1.3s;
  old allocation demanded 1.3825x despite a feasible <=1.30x schedule.
- Optional rewrite TTS: recoverable candidate failure keeps last-good raw audio,
  caption and accepted revision, then proceeds to bounded fit/retime. Explicit
  cancellation, config errors and filesystem failures still propagate, including
  wrapped filesystem errors. Failed candidates never increment accepted count.
- Audio bundle publication: invalidate input identity before mutation, publish
  identity last, restore previous bytes/events/identity on caught promotion
  failures. Abrupt process termination can leave a non-reusable artifact; this
  is fail-closed cache invalidation, not a claim of a multi-file filesystem transaction.
- Cadence: multi-cue audio without usable measured placements always records
  `multi_cue_unverified_timing`; total chunk fit cannot certify cue onsets.
  QC retains this and `rewrite_tts_unavailable` with exact chunk/cue ownership.
- Meaning: detect the reproduced buying/selling, waterproof/permeable and
  lending/borrowing reversals in C6/C7. Compounds are interpreted before their
  component predicates. The lexical Chinese `不同` is paired with Vietnamese
  `khác`/`không giống`, preserving outer negation instead of falsely rejecting
  the valid coffee rewrite.
- C6: after targeted semantic repair still fails, try only configured subsequent
  routes, only for rejected IDs, within the existing shared deadline/dispatch
  budget. Accepted siblings remain durable; only the final rejected candidate
  owns the draft receipt. Attempt counts include semantic fallback attempts.
  Local read-only source neighbours remain in prompts even if global context
  truncates the middle of the video.
- C7: recheck source invariants/context and the accepted job name dictionary,
  in addition to checking the previous Vietnamese text. Bump C6 prompt and C7
  acceptance policy versions so old cached decisions are not silently treated
  as current evidence.
- Correct two stale tests which had called an unmeasured failed candidate an
  accepted rewrite. Separate measured acceptance followed by later failure from
  failure before candidate measurement.

## Verification scope

Linux, Python 3.12.14, FFmpeg/ffprobe 6.1.1. This is not the user's Windows/GPU
runtime. Tests use isolated data storage. Initial RED evidence reproduced the
allocation defect and six direction/property guard mutations. Existing baseline
also failed the stale accepted-rewrite assertion (actual accepted count was 0).

New regression coverage includes reversed allocation pressure, idempotent
allocation, allowed equivalent paraphrases, source-vs-C6 checking, semantic
fallback success/exhaustion, targeted neighbour context, each bundle promotion
failure, missing candidates, cancellation/config/disk failures, last-good audio
fallback and measured rewrite resume. Existing PCM tests use real WAV/FFmpeg and
FFprobe, with provider leaves replaced by deterministic fixtures.

Final verification: **551 passed in 73.07 seconds** across the 26 focused modules
below; Ruff, Python compile checks and `git diff --check` passed. No full-suite,
frontend or live-provider pass is claimed.

Run from `app/` with the project's Python environment:

```sh
python -m pytest -q \
  tests/test_phase_c_dubbing_planner.py \
  tests/test_phase_c_semantic_segmentation.py \
  tests/test_phase_c_tts_timing.py \
  tests/test_phase_c_localization_qc.py \
  tests/test_phase_c_localization_soft_overflow.py \
  tests/test_localization_review_closure.py \
  tests/test_localization_measured_speakers.py \
  tests/test_localization_postfit_measurement.py \
  tests/test_localization_guard_context.py \
  tests/test_localization_guard_caller.py \
  tests/test_localization_numeric_role_regressions.py \
  tests/test_localization_location_questions.py \
  tests/test_localization_vi_realization_particles.py \
  tests/test_localization_zh_fixed_compounds.py \
  tests/test_localization_zh_particle_quantities.py \
  tests/test_localization_auto_translation.py \
  tests/test_localization_auto_omissions.py \
  tests/test_localization_repair_provenance.py \
  tests/test_localization_auto_c7_audio.py \
  tests/test_localization_auto_draft_integration.py \
  tests/test_localization_revision_fallback.py \
  tests/test_localization_tts_failure_recovery.py \
  tests/test_localization_voice_workflow.py \
  tests/test_localization_segmentation_provenance.py \
  tests/test_c7_rewrite_recovery.py \
  tests/test_c7_measured_cadence.py
```

## Remaining product limits

No live ASR/LLM/Edge call, user media, 5–10 minute E2E video, listening acceptance,
frontend acceptance or zero-intervention success-rate benchmark was performed.
No credentials or user configuration were changed.

The deterministic predicate inventory is NOT a general semantic judge. No claim
that arbitrary changed facts or model hallucinations are fully detected.
Configured semantic fallback improves recovery but can exhaust its budget;
Auto drafts/omissions retain their existing explicit review policy.

Simultaneous-speaker mixing, local (instead of global) video retiming, independent
target-audio forced alignment and TTS provider failover are not implemented by
this bounded bug-fix set. Large source overlaps and truly infeasible speech
windows remain explicit failures. Existing publish approval is unchanged.

Verdict: code regression closure only; G1/G2/G3/T5 and the requested diverse-video
zero-intervention product DOD remain open.
