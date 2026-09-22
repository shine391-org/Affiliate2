# QA Phase C — Media, AI & Automated HQ Localization

## Purpose

Phase C changes quality-critical shared media/AI contracts. QA must validate both correctness and product output quality.

A playable MP4, successful HTTP response or green unit suite is not sufficient by itself.

## Test policy

Use the repository tiered-test policy.

### Tier 1 — inner loop

Run only directly affected tests plus import/build/smoke for the changed capability.

Examples:

- downloader format/quality tests
- provider settings tests
- ASR quality-routing tests
- translation segment tests
- timing/TTS block tests
- render command tests
- relevant frontend component tests/build

### Tier 2 — Phase-C module gates

Run focused integration coverage across:

- media ingestion
- AI provider/model settings
- profile persistence
- transcript/timeline
- ASR
- translation
- dubbing/timing
- mixing
- captions/render
- QC/job progression
- expert targeted rerun
- batch orchestration

Include frontend production build and stable local runtime smoke.

### Tier 3 — pre-merge milestone

Run the broad/full suite before Phase-C merge because shared media/AI infrastructure is cross-cutting.

## Required automated assertions

### Media/source quality

- best/default selection no longer prefers an available lower-quality source solely because it is <=720p
- requested quality policy is respected
- actual downloaded media metadata is persisted
- specialized resolver is used only under documented conditions
- final render reports actual media properties
- no unintended repeated encoding path is introduced

### AI provider/local AI

- base URL persists through API/settings/UI
- model ID is manually editable
- compatible/local provider works without static-registry membership
- model discovery works where supported
- connection test returns explicit success/failure
- secrets are not leaked back to UI/logs

### Profiles

- saved profile round-trips
- job snapshots effective profile/config so later profile edits do not silently change an already-running job
- batch jobs consume the intended profile
- explicit job/segment override precedence is deterministic

### Transcript/timeline

- source and target coordinates use distinct fields
- no code path writes output timing into source timing
- segment state can survive persistence/reload
- downstream consumers reuse the canonical transcript rather than retranscribing unnecessarily

### ASR

- configured quality maps to the intended actual model
- failure/downgrade is explicit
- timestamps remain monotonic
- no duplicate pass is made when an existing canonical transcript is valid

### Translation

- context/preset/glossary reach the translation request
- segment order/identity is preserved
- malformed provider responses fail explicitly
- target-duration context is available to the translation/adaptation path

### Dubbing/timing

- TTS is generated per block/semantic unit rather than one joined whole-video text
- actual TTS duration is measured
- timing resolution follows configured policy
- safe rate limits are enforced
- unresolved overflow becomes NEEDS_REVIEW rather than silent distortion/truncation
- final audio block placement follows resolved timeline
- captions use resolved final timing

### Audio/mix

- background preservation path works on supported media
- gain/mix policy is deterministic
- missing separation output is explicit and handled according to documented policy
- no accidental total replacement/truncation of desired source ambience

### Render

- final resolution/aspect matches the selected profile
- final duration is plausible relative to the resolved timeline
- video/audio streams are valid
- caption/render stages do not silently add unnecessary lossy intermediate encoding when a single final encode is possible

### Automation/batch/QC

- multiple candidate jobs can be queued through the existing job system
- stage progression is persisted
- one failed job does not corrupt unrelated jobs
- READY / NEEDS_REVIEW / FAILED are explicit
- QC reason is actionable
- targeted expert rerun starts from the correct stage and does not redo unaffected expensive stages unless required

## Required real-media acceptance set

Final Phase-C acceptance uses at least 10 real source videos from actual supported discovery/media paths.

The set should include:

- Douyin talking-head short
- Douyin product/demo
- fast speech
- music/background-heavy short
- multiple additional Douyin examples
- Bilibili sample with longer/more complex speech
- Bilibili multi-speaker or similarly complex sample when obtainable

Do not use synthetic-only media as the final gate.

## Evidence captured per sample

Source:

- canonical URL/content ID
- width/height
- fps
- video/audio codec
- bitrate when available
- duration

Processing:

- effective Localization Profile
- ASR model actually used
- translation provider/model actually used
- TTS provider/voice actually used
- number of segments/blocks
- timing exception count
- stage failures/retries

Final:

- output width/height
- fps
- codec
- bitrate when available
- duration
- READY / NEEDS_REVIEW / FAILED
- QC reasons

## Human/perceptual checklist

For each final acceptance sample explicitly inspect:

- source meaning preserved
- Vietnamese sounds natural for the chosen preset
- important terminology/names/products preserved
- obvious ASR omissions/hallucinations absent
- dub starts/stops plausibly against the speaker/content
- no severe speech-rate distortion
- captions match final speech timing
- background/music/ambience remains usable
- no obvious clipping
- no obvious visual degradation beyond the selected render profile
- beginning/end are not truncated

Record failures; do not mark them PASS merely because automated checks are green.

## Phase-C merge gate

Phase C may merge only when:

1. focused automated suites pass
2. frontend production build passes
3. stable local runtime smoke passes
4. cloud/OpenAI-compatible provider path is validated
5. at least one local compatible provider path is validated when the user's environment supports it
6. real-media acceptance set is completed
7. material quality regressions are fixed or explicitly accepted/documented
8. no Auto/Manual duplicate pipeline exists
9. no new second job system/media app exists
10. architecture/status/docs match implemented reality

The final report must state exact commands, counts, real samples inspected, provider/runtime limitations and any untested surface.
