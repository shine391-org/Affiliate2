# Phase C — Media, AI & Automated HQ Localization

## Status

**STARTED — C0 COMPLETE; C1–C4 IMPLEMENTED / QA PENDING**

Branch:
`phase-c-media-ai-hq-localization`

C0 donor/source audit:
`docs/PHASE_C_C0_DONOR_AUDIT.md`

Next implementation gate:
**C5 — ASR Convergence**

Independent QA should review C1–C4 before C5 is treated as a mergeable milestone.

## Product goal

A user can select one or more Radar candidates, choose a saved Localization Profile and start processing. The system automatically produces Vietnamese localized videos or explicit QC exceptions without requiring routine manual transcript, translation, timing or FFmpeg work.

The happy path is:

```
Radar candidate
 → best eligible source
 → ASR
 → contextual Vietnamese translation
 → timing-aware dubbing
 → background/audio mix
 → captions
 → HQ render
 → automatic QC
 → review-ready output
```

Expert controls remain available as optional overrides on the same job/pipeline.

## Why Phase C combines media + localization

The previous roadmap split Media/Intelligence and Translate HQ across separate phases.

That split would cause rework because download quality, ASR model routing, AI provider configuration, transcript/timeline contracts, TTS timing and render strategy directly determine localization quality.

Phase C therefore establishes those contracts once.

## Scope

### C0 — Donor/source audit and decision matrix — COMPLETE ✅

Audit:
`docs/PHASE_C_C0_DONOR_AUDIT.md`

C0 established these implementation rules:

- do not vendor donor applications
- reuse existing dependencies before adding new ones
- keep faster-whisper as the initial canonical ASR and propagate the requested quality to real call sites
- gate WhisperX behind real-media timing/multi-speaker evidence; promote it if the baseline cannot pass
- keep the existing AI/TTS/job abstractions and repair their missing wiring
- reimplement only the timed-dubbing behavior Affiliate2 needs, but do not simplify away measured duration, alignment, safe fitting or quality-required retiming
- keep audio separation and video retiming benchmark-gated, not permanently excluded
- treat Edge TTS as a baseline/fallback rather than a guaranteed quality target
- keep MoneyPrinterTurbo out of Phase-C runtime
- apply Ponytail to architecture size only: a deferred capability becomes required when the defined quality gate fails

### C1 — Media quality foundation — IMPLEMENTED / QA PENDING

Implementation:

- default yt-dlp selection is source-best-first instead of 720p-first
- explicit 2160/1440/1080/720/480 choices are hard maximum heights with fallback remaining inside the selected cap
- actual on-disk source properties are probed with ffprobe and persisted as one JSON metadata payload
- Draft / Balanced / HQ H.264 render profiles share one encoder-argument helper
- aspect conversion and caption burn now use the shared render profile vocabulary; localization can request HQ without changing every legacy tool default
- legacy `src/radar/intel/media.py` remains only because legacy Candidate Intelligence has not reached C12 parity; while retained, its Bilibili resolver now selects the highest-bandwidth usable DASH streams instead of the lowest; the file must still be removed with that legacy pipeline after parity

Requirements:

- canonical downloader is the primary ingest path
- specialized platform media resolver is fallback only
- remove 720p-first preference as the default quality policy
- support explicit quality selection such as best / 2160 / 1440 / 1080 / 720 when meaningful
- store actual source resolution/fps/codec/bitrate/duration
- remove duplicate legacy Radar downloader after parity
- minimize repeated lossy video encodes
- define Draft / Balanced / HQ render profiles

### C2 — AI provider + local AI — IMPLEMENTED / QA PENDING

Implementation:

- retained the existing `AIClient` and OpenAI-compatible transport; no LiteLLM/provider framework added
- added Ollama / LM Studio / vLLM presets over that same compatible transport
- added Base URL persistence, free-form model ID, model discovery and connection test
- cloud/local provider switching clears stale stored credentials unless a replacement key is explicitly supplied
- local presets never inherit a stale cloud secret
- frontend registry is suggestions only, not an allowlist
- the legacy intelligence AI path remains until C12 parity rather than being deleted early

Complete one canonical provider layer.

Minimum provider UX:

- OpenAI
- Anthropic
- OpenRouter
- Ollama
- LM Studio
- vLLM
- Custom OpenAI-compatible

Minimum controls:

- Base URL
- API key when required
- editable model ID
- Discover/Refresh Models when supported
- Test Connection

Static model lists are suggestions, not hard gates.

Remove the duplicate direct AI HTTP client only after feature parity.

### C3 — Localization Profiles — IMPLEMENTED / QA PENDING

Implementation:

- profiles are validated Pydantic data and persisted as one JSON payload on `user_settings`; no new profile table was introduced
- default `vi-affiliate-hq` profile uses best download quality, faster-whisper ASR with optional explicit model override, safe-fit timing, HQ render and strict QC
- profile CRUD/default-selection API is available
- profile count, prompt length and glossary size are bounded
- AI/TTS secrets are not duplicated into profiles
- C9 will copy the effective profile into the existing Job input/config snapshot when localization jobs are introduced; that job-level snapshot is intentionally not a second persistence system

A profile is reusable configuration, not per-video form state.

Minimum profile fields:

- source/download quality
- ASR provider/model/quality
- translation provider/model
- translation preset
- custom translation instructions
- glossary/term mappings
- TTS provider/model/voice
- TTS rate policy
- timing tolerance/policy
- background/source-audio mix policy
- caption profile
- target aspect/render profile
- QC profile

Initial translation presets:

- Natural Vietnamese
- Affiliate / Conversational
- Literal
- Custom

### C4 — Canonical transcript/timeline — IMPLEMENTED / QA PENDING

Implementation:

- `LocalizationSegment` has distinct source and target coordinates plus translation/timing/QC state
- malformed, negative or non-finite source timestamps fail explicitly instead of becoming fake zero timestamps
- source-language word timings remain source data and are never reused as target-language caption words
- downloaded subtitle segments are now retained alongside subtitle text so a valid timed source transcript is not thrown away
- existing raw Whisper/subtitle storage remains backward-compatible; C6+ canonicalizes it on entry rather than duplicating every historical transcript
- Analyzer selection now keys on missing insights rather than missing transcript, so creator/auto subtitles are reused for intelligence instead of causing the row to be skipped
- no speculative public API surface or second segment table was added; target-language segment persistence attaches to the localization job/artifact path when C6/C9 introduces those jobs

Create/normalize a durable localization segment contract.

At minimum:

```
id
speaker_id?

source_text
translated_text?

source_start_ms
source_end_ms

target_start_ms?
target_end_ms?

tts_duration_ms?

translation_status
timing_status
qc_status
```

Rules:

- source/output time fields are never interchangeable
- one transcript can be reused by intelligence/localization/Smart Cut/Remake
- job/segment state must support targeted rerun after expert override

### C5 — ASR convergence

Requirements:

- one canonical ASR entry point
- requested quality selects the actual model
- no hidden downgrade
- preserve timestamps needed for downstream timing
- no duplicate ASR passes for consumers that can share transcript data
- benchmark current faster-whisper path before adding WhisperX or another alignment dependency

The current faster-whisper path already provides word timestamps. Phase C first validates those timestamps and fixes call-site quality routing. If real localization samples show material alignment or multi-speaker failures, forced alignment/diarization support becomes required even if that adds dependency complexity.

### C6 — Translation engine

Translation should consider:

- full transcript/context
- local segment text
- glossary/terminology
- target profile/style
- speaker where useful
- target spoken duration

Initial implementation should remain simpler than a full multi-agent translation system but must be materially stronger than an isolated generic "translate this sentence" prompt. If one contextual structured pass repeatedly fails semantic, naturalness or timing-fit QA, add the smallest bounded refinement/adaptation pass required to pass.

Custom prompt/instructions live in the saved profile/advanced surface.

### C7 — HQ dubbing/timing

Replace whole-video joined-text TTS with timed semantic blocks.

Per block:

1. generate translated spoken text
2. synthesize TTS
3. measure actual duration
4. resolve fit

Timing resolution order:

1. duration-aware translation/adaptation
2. bounded rewrite/regeneration when necessary
3. safe TTS-rate adjustment
4. borrow available local silence within configured tolerance
5. otherwise mark explicit exception

Start without video retiming. If representative samples still produce systematic overflow or unnatural speech after the safe steps above, bounded video retiming is promoted into Phase C rather than accepting poor output for simplicity.

Do not distort speech arbitrarily to force every block into the original duration.

Final audio is composed at resolved timeline positions.

Captions derive from the same resolved blocks.

### C8 — Audio separation/mixing

Preserve useful source background/music/ambience.

Preferred conceptual path:

```
source audio
 → voice/background separation when needed
 → target-language dub
 → controlled mix
 → final audio
```

Expose only useful controls such as dub/background gain and preserve-background behavior. Do not build a DAW.

C0 decision: keep the separation contract independent of a specific library and benchmark the simple path first. On speech+music/ambient samples, if source-speech bleed or background damage is perceptually unacceptable, separation becomes required in Phase C; Demucs is the primary reference, not a forbidden dependency.

### C9 — Automation + batch

Auto happy path:

```
candidate + saved profile
→ pipeline
→ QC PASS
→ READY FOR REVIEW
```

Batch:

```
N candidates + one profile
→ N jobs through existing job system
```

Expected stage vocabulary may include:

- QUEUED
- DOWNLOADING
- TRANSCRIBING
- TRANSLATING
- DUBBING
- ALIGNING
- MIXING
- RENDERING
- QC
- READY
- NEEDS_REVIEW
- FAILED

Do not introduce Redis/Celery/a second queue without measured need.

### C10 — Automatic QC + exception queue

QC should surface actionable exceptions, not force manual inspection of every segment.

Examples:

- source below configured quality threshold
- ASR uncertainty/failure
- translation or TTS timing overflow
- missing/failed audio block
- caption/timeline inconsistency
- final media resolution/duration mismatch
- clipping/loudness issue when measurable
- render failure

Output categories:

- READY
- NEEDS_REVIEW
- FAILED

### C11 — Expert override

Expert mode is not a full video editor.

It may expose:

- transcript
- translation
- prompt/model override
- glossary
- TTS voice/rate
- timing exception
- caption settings
- render settings

Actions should support targeted rerun such as:

- rerun translation
- rerun TTS for one block
- rerun timing/mix
- rerun final render

Auto, batch and expert mode all use the same canonical pipeline and state.

### C12 — Intelligence convergence

Use the same media, transcript and AI-provider paths for Affiliate2 intelligence.

Possible domain output:

- summary
- hook
- topic/product signals
- localization potential
- translation/localization risk
- remake potential
- visual dependency
- affiliate angle

Do not run an independent duplicate transcription/provider stack.

### C13 — Real-media benchmark gate

Use representative real samples, including at minimum:

- talking-head short
- product/demo short
- fast speech
- background/music-heavy content
- a multi-speaker or more complex Bilibili sample

For each sample preserve source and output evidence.

Measure what can be measured:

- resolution/fps/codec/bitrate/duration
- stage success/failure
- timing overflow count
- missing blocks
- audio peaks/loudness when implemented

Human/perceptual QA must explicitly inspect:

- ASR correctness
- Vietnamese naturalness
- terminology
- TTS naturalness
- speech timing
- subtitle sync
- background preservation
- visible quality degradation

## Architectural acceptance

Phase C is not acceptable if it creates separate Auto and Manual media engines.

Required shape:

```
LocalizationPipeline(media, config)

Auto   = saved profile
Batch  = saved profile × N jobs
Expert = saved profile + job/segment overrides
```

## Phase-C definition of done

Input:

- at least 10 real Douyin/Bilibili candidate videos for final acceptance

Action:

- select candidates
- select one saved Localization Profile
- start batch processing

Required result:

- independent canonical jobs
- automatic media → ASR → translation → timed dubbing → mix → captions → render → QC
- review-ready outputs or explicit exceptions

The happy path must not require:

- manual transcript editing
- manual translation editing
- manual TTS timing
- manual FFmpeg
- CapCut

Also required:

- expert override works on the same underlying job/pipeline
- no silent semantic/quality fallback
- no duplicate ASR/provider/media pipeline introduced
- source/final media evidence is retained

## Explicitly out of scope

- lip-sync
- avatar generation
- full nonlinear video editor
- automatic publish
- performance-learning rewrite loop
- generative B-roll system
- Smart Cut implementation
- Creative Remake HQ implementation
- big-bang DB convergence
