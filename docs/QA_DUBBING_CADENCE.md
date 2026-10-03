# Dubbing cadence: source word anchors and review gates

Date: 2026-10-02. Branch: `feat/localization-fast-mode`; base `d0055c7`.

## Verdict

**FAIL for the original unchanged sample end-to-end.** The cadence/QC code fix
has passing automated and real-audio evidence. The original translation still
contains a standalone `吴小彤` cue which the selected Vietnamese Edge voice
failed to synthesize (`No audio was received`). No original job/profile/data was
rewritten and no name pronunciation was silently approved.

**LIVE VERIFIED, conditional input:** the same 38-cue transcript with three
explicit QA-only pronunciation substitutions produced a real full dub track
and a 70.424-second review MP4. This is not original-input acceptance, a verified
transliteration, listening approval, or a publish-ready delivery.

## Root cause

The source ASR/semantic cue window is not necessarily the actual speech onset.
The sample's adjacent padded cue windows have zero gaps even when their words
start substantially later:

| Cue | Window start | Earliest word onset |
| --- | ---: | ---: |
| `Ừm` | 1.080 s | 2.140 s |
| `Chào chị` | 2.360 s | 3.740 s |
| `Em là một beat blogger` | 4.520 s | 4.940 s |

C7 used window starts, so the donor planner saw continuous speech, joined four
utterances into one synthesis and put it at the chunk start. Remaining chunk
slack went to the tail. Changing punctuation or merely slowing TTS does not
restore these source pauses.

The original report's ~2.82-second early question timestamp compares against the
padded cue start after video retime. Relative to the actual source word onset,
its *planned* offset is ~4.58 seconds early. Neither number is target-audio forced
alignment.

## Implementation

- `localization_service._speech_onset_ms`: use the earliest source word start only
  when every word has a nonblank token, finite positive interval inside the cue
  envelope. Overlapping/out-of-order words use the minimum start, not list order.
- Invalid/missing/out-of-envelope timestamps fall back to the original window.
  Sub-millisecond rounding cannot create a zero-width interval.
- A start OR end Expert override disables automatic anchoring. Source fields and
  source words remain unchanged. Original cue ends remain the fitting budget.
- Existing planner preserves newly visible pauses, speaker/voice/lock boundaries,
  continuous narration merging and measured silence allocation. No speed ceiling,
  retime ceiling, rewrite policy or provider default was relaxed.
- Planned onset drift >750 ms relative to the retimed planner source anchor
  records `estimated_onset_drift`, timing basis and exact cue/chunk identity.
  Affected segments require review; QC emits `dubbing_onset_drift` and cannot
  report READY solely because total duration fits.
- Dubbing metadata explicitly calls chunk-internal offsets estimates, not forced
  alignment. This is a review heuristic, NOT word-level lip-sync certification.

## Donor comparison

Read primary upstream code, not secondary recommendations:

- VideoLingo `core/_8_2_dub_chunks.py`: uses source gaps/timing tolerance for
  cutoffs and bounded merges. `core/_11_merge_audio.py`: places audio with silence
  between configured time ranges. This supports preserving actual pause anchors;
  it does not make inaccurate source windows accurate.
- Existing OpenCreator/Krillin-derived local planner: already supports hard
  boundaries, measured fit and limited borrowing. Fix its input anchors instead
  of rewriting the planner.
- pyVideoTrans official synchronization documentation: speed/retime/silence and
  second-pass recognition are separate strategies. Borrowing speed/retime alone
  cannot recover internal turn boundaries. No GPL donor code was copied.

Chosen trade-off: use existing source words first; keep natural continuation
merging; expose remaining estimate drift. Mandatory per-cue TTS increases call
count and resets narration prosody. Mandatory forced alignment/diarization adds
runtime/dependencies and cannot repair untranslated names. A future targeted
alignment pass may refine flagged chunks, but is not implemented or claimed here.

## Verification

RED before initial fix: `test_phase_c_word_onset.py` -> 2 failed / 7 passed;
actual failures showed 1080 instead of 2140 and one synthesis instead of four.
QC RED: fitted-but-front-loaded audio produced no onset issue; the QC evaluator
returned READY for supplied drift. Both now require review. Additional adversarial
word timestamp and rounding cases were exercised and fixed.

Final regression command (Windows Git Bash, repo root):

```bash
VIRALMINT_DATA_DIR='C:/Users/shine/AppData/Local/hermes/profiles/affiliate/cache/scratch/cadence-tests' \
E:/Affiliate2/.venv/Scripts/python.exe -m pytest \
  app/tests/test_phase_c* app/tests/test_fast_mode_profiles.py \
  app/tests/test_fast_mode_routing.py app/tests/test_translate_batch_resilience.py \
  --rootdir=app -o pythonpath=app -q \
  --basetemp='C:/Users/shine/AppData/Local/hermes/profiles/affiliate/cache/scratch/cadence-final-regression'
```

Focused earlier gate: 298 passed. Broad final gate: **485 passed in 44.72 s**.
`git diff --check` passed. No full-repository suite or frontend rebuild is claimed.

Real execution scripts and evidence are under the active profile's scratch:

- `qa_cadence_live.py`: read-only existing settings/artifacts; isolated C7 output.
- `qa_cadence_live.py --diagnostic-names`: explicit conditional pronunciation input.
- `cadence-live/analyze_and_render.py`: actual FFmpeg silencedetect, stream-copy
  review render, full decode and production QC against source/final files.
- `cadence-live/diagnostic-names/{plan,chunks,transcript,result,cadence-evidence,qc-report}.json`
- `cadence-live/diagnostic-names/cadence-review.mp4`

QA-only substitutions are `橙子 -> Cheng Zi`, `大奔小发 -> Da Ben Xiao Fa`,
`吴小彤 -> Wu Xiao Tong`. They were NOT added to the saved profile or original
translation. Their linguistic correctness requires human review.

Conditional live result: 23 chunks vs original 11; video slowdown 1.0 vs original
1.033789; maximum fitted speed 1.292584 (normal ceiling unchanged at 1.30).
At -35 dB, old first-15-second PCM contained 3.103- and 4.380-second silent tails.
The conditional output's longest detected pause in that interval is 1.373 seconds;
pauses now occur between speech turns. Because pronunciation text changed, this
is not a controlled same-text duration/performance benchmark.

The generated MP4 has real H.264 1920x1080 video, AAC 48 kHz audio, duration
70.424 s; complete decode succeeded. Production QC: NEEDS_REVIEW, no hard
failures; warnings retain Edge preview quality, high speed, onset drift and
segment review. The review MP4 is a remux for audio inspection, not a full
canonical job rerun with new burned Vietnamese captions.

## Historical first-pass remaining issues / rollout

- Choose approved Vietnamese/Latin pronunciations for the three names through
  existing translation instructions/glossary or Expert translation edits; rerun
  translation/dubbing as appropriate. Never silently omit names or loosen speed.
- Source word timing accuracy is inherited from ASR when alignment is off.
  Mixed turns already inside one semantic cue remain an upstream limitation.
- Final conditional output has two planner-anchor drift warnings: +769 ms
  (`seg-0018`) and -1136 ms (`seg-0029`). Both remain review-only. Raw provider
  synthesis/rewrite can vary across reruns; no deterministic timing guarantee or
  fresh full-pipeline speed benchmark is claimed.
- Independent review worker failed with HTTP 429 and produced no review. Parent
  performed adversarial self-review plus objective integration/regression checks.
- Existing running backend imports old modules. No restart or production job
  invalidation was performed. Applying to an old completed job requires reloading
  the backend safely and rerunning from C7; a cached full-job resume is not proof
  of this fix.
- Changes remain uncommitted; no push/merge, no production DB/profile mutations.

## Follow-up review and fixes (supersedes first-pass verdict)

Original translation now runs through production spoken-form preparation, not
the `--diagnostic-names` substitutions. Preparation replaces only Han spans for
Vietnamese Edge voices, validates all forms before mutation, prefers glossary,
and caches by input. It keeps pronunciation provenance and requires review.
No new dependency or speed-ceiling increase was introduced.

Additional fixes:

- Preserve valid word onset when a word tail crosses the ASR cue end.
- One bounded adaptive split for substantial estimated drift, only for viable
  source-word-anchored cues. Preserve normal narration grouping elsewhere.
- Persist boundaries for resume/targeted regeneration; reject targeted requests
  when saved timing layout no longer matches. Raw-audio identity checks remain.
- Speculative splitting uses an owned temporary directory: provider timeout or
  infeasible fitting retains the original renderable track and review warning.
- Only AI-call time spends the rewrite budget; serial TTS/FFmpeg time does not
  consume unused AI allowance. The adaptive retry shares remaining budget.
- Reject Vietnamese duration rewrites that reintroduce Han after preparation.
- Final QC explicitly retains pronunciation-review obligations.

Latest original-input LIVE execution:

| Metric | Result |
|---|---|
| C7 result | rendered, 27 chunks |
| Video slowdown | 1.0645 (existing profile policy, not no-retime) |
| Maximum fitted speed | 1.2994363551 |
| Estimated onset-drift warnings | 0; not forced alignment |
| Pronunciation review | 3 segments |
| Speed review | 2 chunks |
| Real output | H.264 1920x1080, AAC 48 kHz, 74.966 s |
| Full FFmpeg decode | PASS |
| Production QC | NEEDS_REVIEW; failures=[] |

At -35 dB, old first-15-second PCM had 3.103/4.380 s silent tails.
Latest output's longest detected pause starting in that interval is 1.496 s.
Different spoken forms and retime mean this is not a same-text benchmark.
QC warnings: preview_tts_provider, high_speech_speed,
dubbing_pronunciation_review, video_retimed, segment_needs_review.

Evidence: active-profile scratch `cadence-live/{result,plan,chunks,transcript,
cadence-evidence,qc-report}.json`, `cadence-live/cadence-review.mp4`.
`analyze_and_render.py` now uses original-input results and applies actual
video slowdown with setpts (the earlier diagnostic stream-copy path is historical).
This is a review render, not a canonical C1-through-export job or caption QA.

Regression command (isolated VIRALMINT_DATA_DIR; project venv Python):

```text
python -m pytest app/tests/test_phase_c* app/tests/test_dubbing_speech_preparation.py app/tests/test_fast_mode_profiles.py app/tests/test_fast_mode_routing.py app/tests/test_translate_batch_resilience.py --rootdir=app -o pythonpath=app -q
```

499 tests cover relevant Phase C/regressions, including successful repair,
provider-timeout fallback/cleanup, cached resume, targeted regeneration,
Expert timing mismatch, invalid preparation, voice routing, rewrite budget,
and foreign-script reintroduction. Provider failure paths use mocks; live
success/media/QC evidence is separate. Full repository/frontend not claimed.

Delivery remains NEEDS_REVIEW for names/speed/retime; no listening approval,
target-word forced alignment or production rollout is claimed. No commit,
push, production database mutation or backend restart was performed.

## Code review pass (second follow-up)

Adversarial self-review of the diff found three real defects; all are fixed and
covered by tests.

1. Pronunciation preparation rewrote `translated_text`. Captions, burned
   subtitles and the persisted C6 transcript would show AI-chosen Latin forms
   instead of the user's approved text. Preparation now records forms and
   provenance only; `spoken_pairs()`/`_apply_spoken_forms()` substitute them
   into TTS input and restore the original script after any duration rewrite.
   The reintroduction guard still rejects rewrites that add Han back.
2. Adaptive splitting required every cue in a chunk to be viable, so one 640 ms
   cue vetoed splitting the substantial cues behind it. Boundaries are now
   judged pairwise. This is what removed the last `-1322 ms` drift
   (`seg-0029`).
3. Splitting could trade a cadence warning for a timing overflow. A trial whose
   result contains `timing_overflow` (or raises `LocalizationTimingError`) is
   now rejected with `cadence_repair_rejected_overflow`; the original layout,
   its renderable audio and the cadence warning all survive.

Latest original-input LIVE run after these fixes:
| Metric | Result |
|---|---|
| C7 result | rendered, 27 chunks, 72.299 s output |
| Video slowdown | 1.0267 |
| Maximum fitted speed | 1.2999756513 (at the normal ceiling) |
| Estimated onset drift > 750 ms | 0 of 38 cues |
| Pronunciation review | 3 segments (spoken-only, captions unchanged) |
| Speed review | 3 chunks |
| Full FFmpeg decode | PASS |
| Production QC | NEEDS_REVIEW; failures=[] |

Longest detected pause starting in the first 15 s is 1.436 s, versus the
original 3.103 s and 4.380 s silent tails. QC warnings:
preview_tts_provider, dubbing_rewrite_degraded, high_speech_speed,
dubbing_pronunciation_review, video_retimed, segment_needs_review.

`dubbing_rewrite_degraded` is the reintroduction guard firing on a real
provider response for `seg-0005`; the original approved wording was kept.

Regression: 502 tests pass (same command as above). AI pronunciation output is
provider-dependent and changed between runs (`Cheng Zi` vs `Trành Tử`), which is
exactly why the review obligation is not auto-cleared. Fix names in the profile
glossary or Expert translation to make audio deterministic.

## Independent QA review of the whole change set

Reviewed the complete uncommitted diff (277 changed lines plus 4 new files)
against the user's actual request: better translation and cadence. Findings,
all reproduced and fixed:

| # | Defect | Impact | Status |
|---|---|---|---|
| A | Pronunciation preparation overwrote `translated_text` | Burned subtitles and the persisted C6 transcript would show machine-chosen names instead of the approved text | Fixed: spoken-only substitution, original script restored after every rewrite |
| B | Split viability was evaluated per chunk | One 640 ms cue blocked splitting the substantial cues behind it, leaving `-1322 ms` drift | Fixed: pairwise boundary evaluation |
| C | A split could trade a cadence warning for a timing overflow | Silent degradation into faster speech | Fixed: `cadence_repair_rejected_overflow`, original layout kept |
| D | Duration rewrite was shown the approved caption text (still Han) | The model echoed Han back, then the reintroduction guard rejected it — real `rewrite_invalid_response` / `dubbing_rewrite_degraded` on `seg-0005` in live runs | Fixed: rewrite is shown `item.spoken_text` |

Defect D is the clearest proof that "the other models optimized the wrong
layer": the guard was doing its job, but the input made success impossible.
The pipeline now shortens what will actually be spoken and stores the approved
wording, verified end to end (`seg-0005`: spoken `Tên em Da Ben Xiao Fa`,
stored `Tên em 大奔小发`).

Latest original-input LIVE run after all four fixes:

| Metric | Result |
|---|---|
| C7 result | rendered, 29 chunks, 70.424 s output |
| Video slowdown | 1.0 |
| Maximum fitted speed | 1.2839 |
| Duration rewrites applied | 5 |
| Cadence repair | `measured_split`, boundaries persisted |
| Estimated drift > 750 ms | 1 cue (`seg-0031`, 856 ms) |
| Pronunciation review | 3 segments |
| Segments marked review | 8 of 38 |
| Full FFmpeg decode | PASS |
| Production QC | NEEDS_REVIEW; failures=[]; warnings: preview_tts_provider, high_speech_speed, dubbing_pronunciation_review, segment_needs_review |

`video_retimed` and `dubbing_rewrite_degraded` are gone from this run's
warnings. Longest pause starting in the first 15 s is 1.393 s versus the
original 3.103 s and 4.380 s.

Remaining, by design: `seg-0031` sits at 856 ms of estimated drift because the
repair pass is deliberately bounded to one attempt, and it only fires for
chunks that drift after a split. Raising the retry count trades provider cost
for diminishing returns; selective forced alignment is the real fix and is not
implemented. Names are still provider-dependent between runs.

Regression: 503 tests pass with the same Phase C command. No commit, push,
backend restart or production mutation was performed.
