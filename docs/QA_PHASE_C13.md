# QA Phase C13 — Final Real-Media Acceptance

## Status

**ACTIVE**

C12 is QA PASS. C13 is the final acceptance campaign for the Phase-C output
engine. It does not introduce a second pipeline or a new media architecture.

The purpose is to prove that the already-implemented canonical pipeline produces
publishable real output across representative material, and to fix only defects
that are demonstrated by that evidence.

## Non-negotiable rules

- use the canonical Phase-C localization pipeline and saved profile;
- default profile: `vi-affiliate-hq`;
- do not relax `dub_speed_max`, video-retime ceiling, coverage/QC thresholds,
  or other quality gates merely to obtain PASS;
- do not disable WhisperX, Demucs, QC or durable stage validation to save time;
- do not change AI/TTS provider simply to hide a bug;
- reuse completed expensive stages when their provenance and artifact identity
  remain valid;
- if a sample exposes a real defect, fix the smallest root cause, add regression
  coverage, commit/push, then rerun the affected sample/stage;
- do not self-award perceptual PASS. The user is the final judge of rendered
  output.

## Git safety

Preserve user media and runtime artifacts.

Forbidden:
- `git clean -fd`
- force push
- deleting user/research/media files
- silently resetting untracked runtime evidence

Before and after work record:

```powershell
git status --short
git fetch origin
git checkout phase-c-media-ai-hq-localization
git pull --ff-only
git rev-parse HEAD
git rev-parse origin/phase-c-media-ai-hq-localization
git rev-list --left-right --count HEAD...origin/phase-c-media-ai-hq-localization
```

Final branch state must be local == remote and ahead/behind `0 0`.

## Acceptance set

### A. Historical regression — mandatory first

Preferred sample when still present:

`E:\Affiliate ver 1\storage\videos\qa\sample.mp4`

Historical failure:
- ~65.7 seconds
- 21 old timing units
- 15 timing overflows
- most Vietnamese target speech missing

C13 must show the failure is no longer present on the canonical pipeline.

### B. Representative real media

After the historical regression succeeds, run these categories:

1. fast speech;
2. music-heavy;
3. multi-speaker;
4. longer-form;
5. normal product/review short.

Use real supported Douyin/Bilibili media supplied or approved by the user.
Do not fabricate replacement URLs merely to complete the matrix.

If a required category has no suitable source yet, record `WAITING_FOR_SAMPLE`
for that category and ask the user for the missing URL.

## Preflight

Record once for the campaign:

- Python executable/version
- Node/npm
- FFmpeg/ffprobe
- torch / CUDA / GPU
- faster-whisper model
- WhisperX import/version
- pyannote import/version
- Demucs import/version/device
- AI provider/model
- TTS provider/model/voice
- HF token configured: YES/NO only

Never print credentials or tokens.

Run the current affected automated suite and frontend build before accepting new
real-media evidence. A prior PASS may be reused only when HEAD has not changed.

## Per-sample execution contract

Each sample uses the same canonical job/manifest path. Record the job id and
`storage/localization_jobs/<job_id>/manifest.json`.

For each sample capture:

### Source/provenance

- source URL/path
- source media identity/hash when available
- source ffprobe duration, resolution, fps, video codec, audio codec
- manifest version
- effective profile
- source path used by C5
- source/reference media path used by C7/C8
- prove transcript/timeline end does not exceed canonical source duration except
  for an explicitly documented bounded retime mapping

A media/timeline provenance mismatch is a blocker. Do not mask it by rewriting
speech text.

### Separation / ASR / alignment

- Demucs model/device
- vocals/background artifacts exist and are non-empty
- ASR model
- detected language
- raw ASR segment count
- word count
- WhisperX alignment applied: YES/NO + reason
- diarization applied: YES/NO + reason
- speaker count
- known speaker boundaries propagated downstream

### Semantic / translation

- semantic cue count
- translated cue count
- failed/fallback count
- missing/duplicate/reordered stable IDs: must be zero after accepted fallback
- glossary/style/profile applied
- translated content covers source speech without silent drops

### Dubbing / timing

- dubbing chunk count
- cue IDs per chunk
- TTS provider/model/voice
- raw TTS duration per chunk
- duration rewrite count
- rewrite AI unavailable warnings
- max raw required speed
- max fitted speed
- video slowdown
- over-accept count
- unresolved over-max count
- timing issues
- target speech coverage

Acceptance requirements:
- unresolved over-max = 0 for READY output;
- no target speech silently dropped;
- configured speed ceiling remains enforced;
- bounded retime remains within profile ceiling;
- final target timeline remains monotonic.

### Audio / render

- clean background used in HQ mode
- source speech is not deliberately mixed back into HQ output
- dub/background gain path
- clipping/peak warnings
- final encode count for the visual stream
- final ffprobe duration/resolution/fps/codecs
- beginning/end truncation check
- final video path

The intended HQ path performs one final lossy visual encode.

### QC / durability

- QC status
- QC failures
- QC warnings
- manifest stages complete
- artifact files actually exist
- resume test: completed expensive stages are reused
- rerun from dubbing does not rerun separation/ASR/translation
- targeted chunk regeneration leaves non-target raw chunks unchanged

## Category-specific checks

### Fast speech

Focus on semantic chunk quality, rewrite behavior, speed ceiling, retime ceiling,
missing target speech and natural cadence.

### Music-heavy

Focus on Demucs separation, speech bleed, background/music preservation, dub
audibility, limiter/clipping and avoiding dry-TTS replacement of the soundtrack.

### Multi-speaker

Focus on diarization evidence, stable speaker IDs, speaker-safe chunk boundaries,
per-speaker voice/reference mapping when configured, and no cross-speaker merge.

If diarization is unavailable because the required gated model/token is absent,
record that sample as BLOCKED for multi-speaker acceptance rather than pretending
single-speaker behavior proves the gate.

### Longer-form

Focus on stage durability/resume, model memory/resource stability, long
Demucs/ASR behavior, accumulated timeline drift and bounded provider failures.

### Normal product/review short

This is the representative normal production case. It should complete without
expert intervention unless QC finds a real exception.

## Technical verdict per sample

Use exactly one:
- `TECHNICAL_PASS`
- `NEEDS_REVIEW`
- `FAIL`
- `BLOCKED_EXTERNAL`
- `WAITING_FOR_SAMPLE`

A playable MP4 is not automatically TECHNICAL_PASS.

## User perceptual review

For every technically acceptable final video, return the exact local final path
to the user and ask them to judge:

1. Vietnamese meaning/naturalness
2. TTS voice publishability
3. speaking cadence
4. caption sync
5. source-language bleed
6. background/music quality
7. visible quality loss
8. beginning/end truncation
9. overall publishability

Record the user verdict as `USER_PASS`, `USER_FAIL` or `USER_PENDING`.
Do not infer the user's verdict.

## Phase-C final gate

Phase C may be marked COMPLETE only when all of the following hold:

- C1-C12 technical gates remain green on final HEAD;
- historical regression = TECHNICAL_PASS;
- representative real-media set is complete, or any intentionally excluded
  category is explicitly agreed with the user;
- multi-speaker behavior has real evidence on a suitable sample;
- no accepted sample has unresolved timing overflow or silent target-speech loss;
- HQ audio/background and final video properties are technically acceptable;
- resume/rerun/targeted regeneration evidence remains valid;
- user perceptual verdict is PASS for representative outputs they choose to review.

Until user perceptual acceptance is recorded:

`PHASE C = TECHNICAL PASS / USER ACCEPTANCE PENDING`

## Final report template

```
# C13 FINAL ACCEPTANCE REPORT

## Git
branch:
HEAD:
remote HEAD:
ahead/behind:
working tree:

## Environment
Python:
Node/npm:
FFmpeg/ffprobe:
Torch/CUDA/GPU:
Whisper:
WhisperX:
pyannote:
Demucs/device:
AI provider/model:
TTS provider/model/voice:
HF token configured: YES/NO

## Automated
focused:
full affected:
frontend build:

## Historical Regression
sample:
job:
technical verdict:
old 15/21 overflow fixed: YES/NO
target coverage:
max raw speed:
max fitted speed:
video slowdown:
QC:
final path:
user verdict:

## Fast Speech
source:
job:
technical verdict:
key evidence:
final path:
user verdict:

## Music Heavy
source:
job:
technical verdict:
key evidence:
final path:
user verdict:

## Multi Speaker
source:
job:
technical verdict:
speaker evidence:
key evidence:
final path:
user verdict:

## Longer Form
source:
job:
technical verdict:
resume evidence:
key evidence:
final path:
user verdict:

## Product / Review
source:
job:
technical verdict:
key evidence:
final path:
user verdict:

## Defects Found
BLOCKER:
MAJOR:
MINOR:
fix commits:

## Final Gate
C1-C12 remain green: YES/NO
C13 technical acceptance: PASS/FAIL/BLOCKED/PENDING
USER PERCEPTUAL ACCEPTANCE: PASS/FAIL/PENDING
PHASE C COMPLETE: YES/NO
```
