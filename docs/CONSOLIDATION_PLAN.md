# Consolidation Plan

## Why this exists

Affiliate2 combines differentiated discovery/Radar/business logic with useful OSS application/media plumbing. Consolidation exists to create one maintainable product without preserving duplicate infrastructure or low-quality defaults.

The target is not "ViralMint plus patches". Affiliate2 is the product. Existing ViralMint/MediaCrawler code and other OSS projects are donors/references behind one canonical application.

## Phase A — Application baseline

Status: **COMPLETE ✅**

Delivered:

- `app/` established as canonical runnable source
- native Windows bootstrap/dev/run/stop
- source parity repaired
- focused backend/API, frontend and runtime QA passed

Acceptance:
`docs/QA_PHASE_A.md`

## Phase B — Sources + Radar

Status: **COMPLETE ✅ — QA PASS — MERGED**

Delivered:

- app-native provider contract
- real Bilibili discovery + creator history
- real Douyin CDP provider
- repeated Radar observations
- creator-baseline/identity fixes
- Radar signal integration in Scout

Merge:
PR #6, squash commit `d20ac9d`

Acceptance:
`docs/PHASE_B.md`
`docs/QA_PHASE_B.md`

## Phase C — Media, AI & Automated HQ Localization

Status: **C4–C11 TECHNICAL PASS; C12 IMPLEMENTED / LOCAL QA PENDING; C13 PENDING 🚧**

Detailed scope:
`docs/PHASE_C.md`

Acceptance:
`docs/QA_PHASE_C.md`

Phase C intentionally combines the old Media/Intelligence convergence work with the Translate/Dub HQ foundation. These concerns share the same media, ASR, provider, transcript, timeline, TTS and render contracts; splitting them would create rework.

### C0 — donor/source audit and decision matrix

For each capability, record current implementation, donor/reference, decision (keep/adapt/reimplement/reject), license and reason.

Core references:

- YouDub
- SmartSub
- VideoLingo
- pyVideoTrans (algorithm reference; GPL caution)
- OpenCreator
- faster-whisper / WhisperX
- MoneyPrinterTurbo only for later composition ideas, not as localization runtime

### C1 — media quality foundation

- canonical downloader becomes the default ingestion path
- specialized provider media resolution only when required
- prefer best/source-native quality instead of 720p-first defaults
- persist source media properties
- remove duplicate Radar downloader after parity
- minimize repeated lossy video encoding
- add Draft / Balanced / HQ render profiles

### C2 — AI provider + local AI

- one canonical AI-provider interface
- cloud presets plus Ollama / LM Studio / vLLM / custom OpenAI-compatible
- editable base URL, API key and model ID
- dynamic/discoverable models when supported
- test-connection path
- static model registry is suggestion, not hard allowlist
- remove duplicate direct AI HTTP client after parity

### C3 — Localization Profiles

Persist reusable automation configuration for:

- source/download quality
- ASR provider/model/quality
- translation provider/model/preset/prompt/glossary
- TTS provider/model/voice/rate policy
- audio mixing
- captions
- render quality/aspect
- timing policy
- QC policy

Profiles are configured once and reused across single or batch jobs.

### C4 — canonical transcript/timeline model

Use explicit source/output coordinates.

At minimum, localization segments carry:

- source text / translated text
- source start/end
- target start/end
- speaker identity when available
- TTS duration
- translation/timing/QC status

The same canonical transcript/timeline becomes reusable by intelligence, Smart Cut and Creative Remake.

### C5 — ASR convergence

- one canonical transcription path
- profile/user quality choice must route to the actual model
- no hidden "Best → small" downgrade
- benchmark existing faster-whisper behavior versus optional alignment support such as WhisperX before adding dependencies
- no duplicate transcription passes merely because multiple features need text

### C6 — contextual Vietnamese translation

- translate with full-context awareness
- preserve/use glossary and terminology
- support small initial preset set: Natural Vietnamese / Affiliate-Conversational / Literal / Custom
- custom prompt is profile/advanced configuration, not a mandatory per-video step
- translation should be aware of target spoken duration

### C7 — HQ dubbing/timing engine

- TTS per semantic/timed block, not one joined whole-video text
- measure actual generated duration
- timing policy: duration-aware rewrite/adaptation → safe rate adjustment → bounded silence borrowing → exception
- compose blocks at timeline positions
- final captions derive from resolved timed blocks
- canonical TTS abstraction must be used

### C8 — audio separation/mixing

- preserve useful background/music/ambience
- replace or attenuate source speech as appropriate
- controlled dub/background gain
- no default dry TTS replacement of the entire source soundtrack

### C9 — automation + batch

Happy path:

```
candidate + saved profile
→ automatic pipeline
→ QC
→ review-ready output
```

Batch mode runs the same canonical pipeline through the existing job system. No Redis/Celery/second queue unless measured evidence later requires it.

### C10 — automatic QC + exception queue

Automatically detect/report issues such as:

- bad/low source quality
- ASR uncertainty
- translation/timing overflow
- missing/failed TTS blocks
- render/media mismatches
- clipping/loudness problems when measurable

Only exceptions require manual intervention.

### C11 — expert override

Expert mode may edit/override transcript, translation, prompt, model, voice, timing, captions or render settings and rerun only affected stages.

It must not be a second pipeline or a CapCut clone.

### C12 — intelligence convergence

Implementation now routes the transitional Radar Candidate Intelligence pipeline through the canonical app media downloader, Whisper service, and AI provider abstraction while preserving the existing Radar CLI/report/cache contract. The legacy intelligence modules are compatibility adapters rather than independent owners of downloader/ASR/provider logic.

Affiliate2-specific output may include:

- content summary/hook/topic/product signals
- Vietnamese angle/localization potential
- translation/localization risk
- remake potential/visual dependency
- affiliate angle

### C13 — real-media benchmark gate

Phase C cannot pass solely on unit tests or playable MP4 files.

Use representative real Douyin/Bilibili samples and inspect:

- source/final media properties
- ASR correctness
- Vietnamese translation naturalness
- terminology
- TTS quality
- timing/sync
- captions
- background preservation
- visible quality loss
- failure/exception behavior

## Phase D — Smart Cut

Status: **IMPLEMENTATION ACTIVE — shared-engine convergence wired / local QA pending 🚧**

Current implementation:
- reuses the existing downloaded source, cached transcript/Whisper path, AI clip
  selector, SourceBench range editor and clip renderer;
- `/studio/smart-cut` mounts that existing workspace instead of a second Smart
  Cut implementation;
- direct Auto-cut is intentionally hidden in Smart Cut: AI suggestions must
  return to editable ranges before render;
- approved ranges post through the existing manual `/extract-clips` path with
  `output_source_type=smart_cut`;
- rendered rows persist as `GeneratedVideo.source_type=smart_cut` with
  `source_downloaded_video_id`, so existing Library lineage owns the result;
- legacy Manual Edit / Clipper keeps `clip_extraction` identity and behavior.

Remaining gate:
- focused backend/frontend tests and local browser smoke;
- real source: Ask AI → adjust range → create Smart Cut → verify Library lineage;
- no duplicate ASR, proposal engine, range editor or renderer may be introduced.

No Creative Remake semantics are hidden inside Smart Cut.

## Phase E — Creative Remake HQ

Status: **DEFERRED / NOT IN CURRENT PRODUCT ROADMAP**

Audit:
`docs/PHASE_E_DONOR_AUDIT.md`

Donor decisions:
- KEEP ViralMint source/transcript/Library/job/FFmpeg/TTS/caption primitives;
- ADAPT ViralMint Pexels/stock scene assembly and B-roll cue timing;
- ADAPT ViralMint Motion Studio as a contextual overlay/insert provider, not the orchestration owner;
- ADAPT MoneyPrinterTurbo's provider-neutral material acquisition/allocation patterns;
- STRONG ADAPT OpenCreator's stable segment/shot IDs, strict storyboard schema, artifact lineage and stale-plan validation;
- ADOPT PySceneDetect for deterministic physical shot boundaries;
- DEFER direct TransNetV2 integration; enable only through the same scene-map interface if real media needs it;
- REJECT Remotion as a parallel runtime/renderer for now.

The donor audit is retained as reference only. No E1–E7 implementation is
planned unless real affiliate output data later proves a full-remake workflow
adds value beyond Localization HQ + Smart Cut.

Creative Remake remains architecturally distinct from Smart Cut if it is ever
revisited.

## Phase F — Review / Publish / Performance native migration

Status: **F1 NATIVE PUBLISH OWNER WIRED — LOCAL QA PENDING 🚧**

See `docs/PHASE_F_CONVERGENCE.md`.

F1:
- `GeneratedVideo.id` is the canonical publish output identity;
- explicit `publish_approved_at` is the human gate;
- approval and platform publish remain separate actions;
- `/publish` is now a real native workspace, not a shell;
- existing `UploadAgent` + UserSettings OAuth/upload credentials remain canonical;
- native YouTube/TikTok publishing is surfaced first;
- old candidate/remake overlay Publish remains compatibility-only until parity cleanup.

Next:
- F2 converge Performance onto native `VideoMetrics` / `performance_tracker`;
- F3 wire MessagingManager to meaningful review/publish/performance events;
- F4 verify the end-to-end affiliate loop;
- delete overlay sync only after parity.

## Phase G — DB convergence + cleanup

Status: PLANNED

- migrate transitional tables incrementally
- verify counts/relationships before removing old paths
- remove dead duplicate modules/reference runtime glue
- milestone/full QA

## Migration rules

1. Every phase leaves the app runnable.
2. No big-bang rewrite.
3. No second WebUI/job system/media app.
4. Reuse canonical plumbing where adequate; replace quality-critical behavior only with evidence.
5. Auto, batch and expert modes share one pipeline.
6. Do not delete legacy paths before parity and migration evidence.
7. Donor code reuse must be license-compatible and documented.
