# QA Phase C — Final Full-Stack Localization Checkpoint

## Scope

This is the FIRST local QA after the complete donor-derived quality stack was implemented.

C1–C3 passed earlier.
This checkpoint validates C4R through C11 together.

Do not split this into another C5–C8 QA followed by C9–C11 QA.

## Historical regression

Use this sample first if it still exists:

`E:\Affiliate ver 1\storage\videos\qa\sample.mp4`

Old failure:
- 1280x720 / 30fps / H.264 + AAC
- ~65.7s
- C5 ASR passed
- C6 translation passed
- old C7 produced 15/21 overflows
- most Vietnamese speech was missing

The new architecture must fix the behavior, not hide it with a threshold.

## Git safety

Preserve all user/untracked files.

Forbidden:
- `git reset --hard`
- `git clean -fd`
- force push
- deleting research/media/user files
- merging master during QA

Sync:

```powershell
git status --short
git fetch origin
git checkout phase-c-media-ai-hq-localization
git pull --ff-only
git rev-parse HEAD
git rev-parse origin/phase-c-media-ai-hq-localization
git rev-list --left-right --count HEAD...origin/phase-c-media-ai-hq-localization
```

Use newest remote HEAD and report it.

## Read first

- AGENTS.md
- PROJECT_STATUS.md
- docs/PHASE_C.md
- docs/PHASE_C_C0_DONOR_AUDIT.md
- docs/PHASE_C_DONOR_CAPABILITY_MATRIX.md
- docs/QA_PHASE_C.md
- docs/ARCHITECTURE.md

## Environment

Use the project venv.

Install/update app requirements using the repo's normal bootstrap path.

Record:
- Python executable/version
- Node/npm
- FFmpeg/ffprobe
- torch / CUDA / GPU
- WhisperX import/version
- pyannote import/version
- Demucs import/version + selected device
- AI provider/model
- TTS provider/model/voice
- Hugging Face token configured? YES/NO only — never print it

Never print secrets.

## Automated gate

From `app/`:

```powershell
python -m pytest -q `
  tests/test_download_options.py `
  tests/test_phase_c_media_quality.py `
  tests/test_phase_c_render_quality.py `
  tests/test_ai_provider.py `
  tests/test_whisper_service.py `
  tests/test_analyzer.py `
  tests/test_phase_c_alignment.py `
  tests/test_phase_c_semantic_segmentation.py `
  tests/test_phase_c_dubbing_planner.py `
  tests/test_translate_batch_resilience.py `
  tests/test_phase_c_audio_separation.py `
  tests/test_phase_c_tts_timing.py `
  tests/test_phase_c_localization_profiles.py `
  tests/test_phase_c_localization_timeline.py `
  tests/test_phase_c_localization_stages.py `
  tests/test_phase_c_localization_qc.py `
  tests/test_tool_runners_more.py
```

Then:

```powershell
npm --prefix frontend run build
```

Then run the broader directly affected app suite if the focused suite passes:

```powershell
python -m pytest -q
```

From repo root also run:

```powershell
python -m pytest -q tests/test_intel.py
```

If a failure is a stale test fixture, prove that before changing it.
Do not change product behavior merely to make tests green.

## C4R / C5 gate

Verify:
- words / semantic cues / target timeline / dubbing chunks are distinct state
- faster-whisper keeps word timestamps
- HQ profile invokes WhisperX forced alignment
- diarization OFF/AUTO/WHISPERX modes behave as configured
- AUTO can degrade safely when gated model/token is unavailable
- strict WHISPERX diarization fails explicitly if required prerequisites are missing
- speaker assignment uses temporal overlap
- known speaker boundaries survive into semantic/dubbing state

## C6 gate

Verify:
- word-tier semantic segmentation can create boundaries inside a raw ASR segment
- AI may group stable word/cue IDs only
- missing/duplicate/reordered IDs reject/fallback
- source words are never silently lost
- full context/glossary/style reach translation
- translated stable IDs/order are validated
- malformed model output cannot slide text onto the wrong segment

## C7 gate

Verify real behavior:
- short adjacent cues become natural dubbing chunks
- speaker/voice change forces chunk boundary
- one natural-rate TTS request per chunk
- actual generated duration measured
- safe following silence borrowing works
- overlong chunk can rewrite/regenerate
- force one duration-rewrite AI timeout: dubbing must continue to TTS/timing fallback, record `rewrite_ai_unavailable`, and QC must become at least NEEDS_REVIEW rather than failing the whole stage
- local LM Studio/Ollama compatible requests may use the longer local timeout; cloud timeout policy remains bounded
- FFmpeg atempo happens AFTER synthesis
- quality speed ceiling is enforced
- unresolved over-max speech never gets silently dropped
- bounded video retime is last resort only
- retime never exceeds profile ceiling
- target timeline remains monotonic
- Expert translation/timing/voice override reaches the real planner
- targeted chunk regeneration reuses unaffected raw chunk audio
- if C7 shortened translated wording for duration, that spoken wording persists; a later one-chunk regeneration must not revert captions/text while reusing old raw chunks

## C8 gate

HQ mode:
- Demucs model = `htdemucs_ft`
- vocal/background artifacts non-empty
- long-file chunking/overlap/crossfade path intact
- RF64-safe intermediate handling
- ASR/alignment consumes vocals on HQ full dub
- target dub mixes with CLEAN background
- no original-language speech is deliberately mixed back in HQ mode
- audio-only stages do not re-encode picture
- external mixed audio + optional setpts + reframe + captions = one final lossy video encode

TTS provider registry:
- Edge
- OpenAI
- OpenAI-compatible
- Azure
- ElevenLabs
- GPT-SoVITS (Chinese/English donor-compatible clone path only; DO NOT use it as a Vietnamese acceptance provider)

Do not require paid provider live calls if no credential exists.
For the Vietnamese publish-quality sample, use Edge only as a technical fallback;
human publishability requires a Vietnamese-capable HQ provider such as Azure,
ElevenLabs, or a verified OpenAI-compatible backend.
Do verify dispatch/config/credential isolation with tests.

If GPT-SoVITS is available locally, run a live EN/ZH clone smoke only.
If it is not available, report BLOCKED only for live clone verification — do not fail unrelated HQ TTS providers.

Automatic clone reference:
- manual reference overrides auto
- auto mode extracts suitable reference speech
- per-speaker mode uses separate reference artifacts when speaker IDs exist
- on HQ clean-dub the reference source should be the vocal input

## C9 gate — persisted automation / resume / batch

Run an actual localization job and inspect:

`storage/localization_jobs/<job_id>/manifest.json`

Verify `version == 2`. A version-1/stale manifest must fail explicitly rather
than resume old artifacts.

Verify stages:
1. separation
2. asr
3. semantic
4. translation
5. dubbing
6. audio_mix
7. final_render
8. qc

Verify:
- persisted source copy
- effective profile snapshot
- artifact files actually exist
- completed stage is not considered complete when artifact disappears
- restart/rerun can reuse completed expensive stages
- rerun from dubbing does NOT rerun Demucs/ASR/translation
- rerun final_render does not rerun TTS
- failure leaves actionable stage/error evidence

Batch:
- submit at least 2 lightweight test videos or copies through the Batch API/UI
- each gets its own normal Job + localization manifest
- batch uses the same runner/profile, not a separate low-quality path

## C10 gate — QC / exceptions

Verify QC can produce:
- READY
- NEEDS_REVIEW
- FAILED

Verify it catches at minimum:
- untranslated target speech
- low target-speech coverage
- timing overflow
- suspicious aspect/duration drift
- missing media streams
- clipping/peak warning where applicable

Open Localization Review UI:
- exception jobs appear
- stage/QC evidence visible
- no secret values exposed

A rendered MP4 with FAILED QC is NOT a publish-ready pass.

## C11 gate — Expert targeted control

From Localization Expert UI/API:
- edit one translated segment
- override one target timing window
- override one segment voice
- rerun downstream
- verify upstream expensive stages are reused

Then:
- regenerate ONE dubbing chunk
- prove unaffected raw chunks were not regenerated
- final audio/render/QC updates

Rerun:
- translation
- dubbing
- final render
- QC

must all work from the same manifest/job rather than creating a second editing engine.

## Same failing sample — mandatory

After automated/runtime smoke, process the OLD sample end-to-end with `vi-affiliate-hq`.

Record:
- source ffprobe
- Demucs device/model
- Whisper/faster-whisper model
- alignment applied?
- diarization applied? / reason
- raw ASR count
- word count
- semantic cue count
- translated count / failed count
- speaker count
- dubbing chunk count
- cue IDs per chunk
- TTS provider/model/voice
- natural TTS duration per chunk
- rewrite count
- max raw required speed
- fitted speed per chunk
- video slowdown
- over-accept count
- over-max count
- timing issues
- target speech coverage
- QC result
- final ffprobe
- final video path

Compare explicitly:

OLD:
- 21 timing units
- 15 overflows
- most Vietnamese dub absent

NEW:
- exact metrics
- is meaningful Vietnamese speech present across essentially all spoken content? YES/NO

## User perceptual QA

The USER is the final judge.

After technical sample finishes, return the exact local final path and ask the user to inspect:
1. meaning / natural Vietnamese
2. TTS voice publishability
3. speaking cadence
4. caption sync
5. source-language bleed
6. background/music quality
7. visual degradation
8. beginning/end truncation
9. overall publishability

Do NOT self-award perceptual PASS.

## Additional real-media acceptance

Only after the old regression works.

Use representative real videos:
- fast speech
- music-heavy
- multi-speaker
- longer video
- normal product/review short

If suitable URLs are not already available, STOP at this point and ASK THE USER for links.

Do not fabricate or randomly replace the requested real samples.

Suggested request:
`I need the next real-media QA links now: 1 fast-speech, 1 music-heavy, 1 multi-speaker, 1 longer video, and 1 normal product/review video. Please send the URLs.`

The user previously said they will supply video links.

## C12

Do not refactor legacy `src/radar/intel/*` during this QA.
Only run `tests/test_intel.py` as regression evidence.

C12 cleanup starts only after the Phase-C output engine is accepted; it should not require another full perceptual-video campaign.

## QA fix policy

QA may:
- fix an objectively stale test fixture;
- fix a small direct product bug with clear root cause + regression test;
- commit each real fix separately.

QA must not:
- redesign Phase C;
- remove donor quality mechanisms because they are slow;
- disable WhisperX/Demucs/QC to get green tests;
- lower quality thresholds to hide a failure;
- replace real-media evidence with mocks.

## Final report

```
## QA STATUS
TECHNICAL PASS / PASS / FAIL / BLOCKED

## Git State
branch:
local HEAD:
remote HEAD:
ahead/behind:
working tree:

## Environment
Python:
Node/npm:
FFmpeg/ffprobe:
Torch/CUDA/GPU:
WhisperX:
pyannote:
Demucs/device:
AI provider/model:
TTS provider/model/voice:
HF token configured: YES/NO

## Automated
focused:
full app suite:
root intel:
frontend build:

## C4-C6
ASR:
alignment:
diarization:
semantic:
translation:

## C7 Smart Dubbing
chunks:
speaker boundaries:
rewrite count:
max raw required speed:
max fitted speed:
video slowdown:
over-max:
target coverage:

## C8 HQ Media
separation:
TTS:
voice reference:
audio mix:
single final encode:
ffprobe:

## C9 Automation
manifest:
resume:
rerun-from-stage:
batch:

## C10 QC
status:
failures:
warnings:
exception queue:

## C11 Expert
translation override:
timing override:
voice override:
targeted chunk regenerate:
upstream reuse:

## Old Sample Regression
OLD: 15/21 overflow; most target dub missing
NEW:
regression fixed: YES/NO
final video path:

## User Perceptual QA
PENDING / PASS / FAIL
notes:

## Additional Real Samples
requested from user: YES/NO
tested:
results:

## Issues
BLOCKER:
MAJOR:
MINOR:
QA fix commits:

## Final Gate
C4-C11 TECHNICALLY ACCEPTED: YES/NO
USER VIDEO QUALITY ACCEPTED: YES/NO/PENDING
READY FOR C12: YES/NO
```

If technical QA passes but user has not watched the output:
- QA STATUS = TECHNICAL PASS
- USER VIDEO QUALITY ACCEPTED = PENDING
- READY FOR C12 = NO
