# Phase C — Media, AI & Automated HQ Localization

## Status

**STARTED**

Branch:
`phase-c-media-ai-hq-localization`

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

### C0 — Donor/source audit and decision matrix

Before substantial implementation, inspect the current app path and relevant donor/reference source.

For each capability record:

- current Affiliate2 implementation
- donor/reference
- decision: KEEP / ADAPT / REIMPLEMENT / REJECT
- license
- reason
- expected files/contracts affected

Required reference set:

- YouDub
- SmartSub
- VideoLingo
- pyVideoTrans
- OpenCreator
- faster-whisper
- WhisperX when alignment evidence requires it
- MoneyPrinterTurbo only for future composition context

Do not clone these as runtime sub-applications.

### C1 — Media quality foundation

Requirements:

- canonical downloader is the primary ingest path
- specialized platform media resolver is fallback only
- remove 720p-first preference as the default quality policy
- support explicit quality selection such as best / 2160 / 1440 / 1080 / 720 when meaningful
- store actual source resolution/fps/codec/bitrate/duration
- remove duplicate legacy Radar downloader after parity
- minimize repeated lossy video encodes
- define Draft / Balanced / HQ render profiles

### C2 — AI provider + local AI

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

### C3 — Localization Profiles

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

### C4 — Canonical transcript/timeline

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
- benchmark current path before adding WhisperX or another alignment dependency

The decision to add word-level alignment must be evidence-based from real localization samples.

### C6 — Translation engine

Translation should consider:

- full transcript/context
- local segment text
- glossary/terminology
- target profile/style
- speaker where useful
- target spoken duration

Initial implementation should remain simpler than a full multi-agent translation system but must be materially stronger than an isolated generic "translate this sentence" prompt.

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
