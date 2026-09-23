# QA Phase C — Donor-Derived HQ Localization

## Current checkpoint

C1–C4 foundation previously passed local QA.

The original C5–C8 prototype failed its first real-media checkpoint:
- 65.7s source
- 21 usable translated timing units
- 15 timing overflows
- most Vietnamese dub missing

C4R/C6R/C7R/C8R have now been reworked from proven donor behavior.

**Do not start C9 until this checkpoint passes.**

## Donor behaviors that must be preserved

### VideoLingo / SmartSub
- raw ASR segments are not sacred translation/dubbing units;
- long cues can be split using real word timestamps;
- tiny/dependent cues can be merged;
- AI semantic refinement may only change boundaries/grouping, not source content;
- invalid AI segmentation must fall back safely;
- translation identity/order must be validated.

### OpenCreator Smart Dubbing
- merge short adjacent cues into dubbing chunks;
- respect max chunk size and gap tolerance;
- synthesize TTS at chunk granularity;
- measure actual generated audio;
- fit after synthesis;
- redistribute fitted chunk timing to plan items;
- preserve monotonic output timing;
- explicit warnings/failures for excessive required speed.

### YouDub
- HQ path separates vocals/background before dubbing;
- ASR may consume the vocals stem;
- background stem is preserved for final mix;
- separation uses long-file-safe chunking/overlap/crossfade/artifacts;
- completed media stages must not silently degrade source video pixels.

## Environment/bootstrap

Sync:
```powershell
git fetch origin
git checkout phase-c-media-ai-hq-localization
git pull --ff-only
git rev-parse HEAD
git rev-parse origin/phase-c-media-ai-hq-localization
git rev-list --left-right --count HEAD...origin/phase-c-media-ai-hq-localization
```

Preserve all user untracked files.

Use the project venv.

Because HQ clean dub now uses Demucs, install/update dependencies using the repository's normal setup/requirements path before runtime testing.

Record:
- Python executable/version
- Node/npm
- FFmpeg/ffprobe
- torch version/device
- Demucs import/version/device
- local AI provider/model

## Focused automated gate

From `app/`:

```powershell
python -m pytest -q `
  tests/test_whisper_service.py `
  tests/test_analyzer.py `
  tests/test_phase_c_semantic_segmentation.py `
  tests/test_phase_c_dubbing_planner.py `
  tests/test_translate_batch_resilience.py `
  tests/test_phase_c_audio_separation.py `
  tests/test_phase_c_tts_timing.py `
  tests/test_phase_c_render_quality.py `
  tests/test_phase_c_localization_profiles.py `
  tests/test_phase_c_localization_timeline.py `
  tests/test_ai_provider.py `
  tests/test_tool_runners_more.py

npm --prefix frontend run build
```

Run additional directly affected regression tests if failures or import/signature changes point to another surface.

## Same-sample regression gate

Use the exact sample that previously failed if it is still available:

`E:\Affiliate ver 1\storage\videos\qa\sample.mp4`

Previous evidence:
- 1280x720, 30fps, H.264/AAC
- 65.7s
- C5 ASR passed
- C6 translation passed
- old C7: 15/21 overflow
- final dub coverage was unacceptable

Do not replace this first regression with a new easier clip.

### Required new pipeline

For `vi-affiliate-hq` full dub:

source
→ Demucs HQ separation
→ vocals stem
→ ASR + word timestamps
→ semantic cue preparation
→ contextual translation
→ OpenCreator-derived dubbing chunks
→ one natural-rate TTS per chunk
→ actual duration measurement
→ bounded rewrite/regeneration if required
→ post-TTS FFmpeg atempo fit
→ fitted target timeline
→ mix with clean background stem
→ one final visual encode for reframe/captions

## Required evidence

### Separation
- vocals/background files exist and are non-empty
- duration close to source
- actual Demucs model/device
- no source video re-encode in separation/mix stage

### Segmentation/translation
Record:
- raw ASR segment count
- semantic cue count
- semantic grouping fallback/degraded count if exposed
- translated cue count
- failed/untranslated count

### Dubbing
Record:
- dubbing chunk count
- chunk → constituent cue IDs
- natural TTS duration per chunk
- rewrite/regeneration count
- required/fitted speed factor per chunk
- max speed factor
- chunks above accept threshold
- chunks above max threshold
- final timing issue count
- Vietnamese speech coverage across the timeline

The regression is not fixed merely because the number of chunks is smaller. Target speech must actually cover the spoken content.

### Audio/mix
Record:
- audio_mode
- vocals/background/dub paths
- background gain / dub gain
- clipping/peak evidence when practical
- whether obvious English/source speech remains in the final mix

### Final media
ffprobe:
- width/height
- fps
- video codec
- audio codec
- bitrate
- duration

Verify:
- no beginning/end truncation
- video stream is copied through audio-only stages
- reframe + captions use one final lossy visual encode

## User perceptual QA

The user, not the QA agent, decides:
- Vietnamese translation naturalness
- TTS voice publishability
- speech timing
- caption sync
- residual source-language speech
- music/ambience quality
- visual degradation
- overall publishability

Provide the local final-video path to the user.

## Decisions after same-sample rerun

### WhisperX
Do not add only because VideoLingo uses it.
Promote it if real word timing/alignment remains materially wrong after the new segmentation/chunk pipeline.

### Better TTS
Evaluate only after the new chunk engine produces near-complete Vietnamese coverage.
Edge is allowed as a test/fallback voice; publishability is a human decision.

### Video retiming
Do not add merely because the old segment-per-TTS engine overflowed.
Consider it only if donor-derived chunking + rewrite + post-TTS fit still leaves systematic over-max chunks.

### C9 gate
`READY FOR C9: YES` requires:
- automated tests/build pass;
- same old sample no longer reproduces systematic dubbing loss;
- HQ clean-dub path runs successfully;
- no unresolved architectural/quality blocker;
- user perceptual QA accepts the sample sufficiently to proceed.

## Final report format

```
## QA STATUS
PASS / FAIL / BLOCKED

## Git
branch:
local HEAD:
remote HEAD:
ahead/behind:
working tree:

## Environment
Python:
Node/npm:
FFmpeg/ffprobe:
Torch:
Demucs/device:
AI provider/model:

## Automated
focused tests:
frontend build:
additional regressions:

## Same Sample
source:
raw ASR segments:
semantic cues:
translated cues:
dub chunks:
rewrite count:
max speed:
over-accept chunks:
over-max chunks:
timing issues:
audio mode:
separation artifacts:
final ffprobe:
final video path:

## Technical Verdict
C4R:
C5:
C6R:
C7R:
C8R:

## User Perceptual QA
PENDING / PASS / FAIL
notes:

## Remaining Escalations
WhisperX:
Better TTS:
Video retiming:
Other:

## Final Gate
READY FOR C9: YES/NO
```
