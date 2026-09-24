# Affiliate2 Architecture

## Target system

```
                         Affiliate2
                             |
                 Canonical FastAPI + React app
                             |
       +---------------------+----------------------+
       |                     |                      |
     Scout                 Library              Profiles
       |                     |                      |
Source Providers         Canonical Media       AI / ASR / TTS /
       |                     |                 Quality / QC config
       v                     v                      |
     Radar ------------> Intelligence               |
       |                     |                      |
       +---------------------+----------------------+
                             |
                  Localization Pipeline
        Download → ASR → Translate → Dub/Align
             → Mix → Captions → Render → QC
                             |
                      Review / Exceptions
                             |
          +------------------+------------------+
          |                                     |
      Smart Cut                         Creative Remake HQ
          |                                     |
          +------------------+------------------+
                             |
                       Human approval
                             |
                           Publish
                             |
                        Performance
                             |
                           Learn
```

## Canonical source

`app/` is the runnable application tree.

`vendor/viralmint/` and `vendor/mediacrawler/` are immutable forensic/reference snapshots. Runtime changes must not be made there.

Legacy `src/radar/`, `viralmint_overlay/` and `overlay/` remain transitional until their behavior is migrated and verified.

## Product/OSS ownership boundary

Affiliate2 is the product.

ViralMint is the current application foundation and one donor for:

- FastAPI + React/Vite
- jobs/progress
- Library/media lineage
- downloader framework
- FFmpeg/media utilities
- ASR/Whisper abstraction
- AI-provider abstraction
- TTS abstraction
- captions
- Clip Studio
- upload/auth/metrics primitives

Inherited behavior is not automatically canonical when quality evidence shows it is inadequate.

Other Phase-C references are used selectively:

- YouDub — production localization/job/media patterns
- SmartSub — provider/config, dubbing and exception-review patterns
- VideoLingo — translation/subtitle intelligence
- pyVideoTrans — synchronization algorithm reference; no casual GPL code copying
- OpenCreator — automation + expert-workspace state pattern
- faster-whisper / WhisperX — ASR/alignment references
- MoneyPrinterTurbo — later automated creative composition reference

No donor remains a second runtime application.

## Discovery and Radar

Scout is the visible discovery/orchestration surface.

A provider must return results from the requested platform. Silent semantic substitution is forbidden.

Radar is the observation/ranking layer behind discovery:

```
provider result → normalized content → metric snapshot
                                ↓
                         repeated scan
                                ↓
                  observed velocity/baseline
                                ↓
                           candidate rank
```

Phase B completed native Bilibili/Douyin + Radar integration.

## Canonical media ingestion

Target:

```
canonical source URL
        ↓
canonical downloader
        ↓
best/source-native eligible media
        ↓
DownloadedVideo + media metadata
        ↓
Library
```

A platform provider resolves specialized media only when the canonical downloader cannot retrieve an eligible source.

The old 720p-first behavior is not a quality requirement. Phase C introduces explicit source/download quality policy and stores actual source properties.

Candidate Intelligence no longer owns a separate media downloader. The transitional `src/radar/intel/media.py` module delegates to the canonical source-best-first downloader and remains only for Radar CLI compatibility until later cleanup.

## Canonical AI/provider layer

One provider abstraction serves intelligence and localization.

Configuration supports cloud and local/OpenAI-compatible endpoints through a common shape such as:

```
provider
base_url
api_key
model_id
```

UI model registries are suggestions/presets, not hard allowlists.

Provider-specific direct clients should not proliferate outside the canonical abstraction.

Candidate Intelligence follows this rule: the transitional Radar AI module delegates to `backend.core.ai_provider` instead of owning a direct OpenAI-compatible HTTP client.

## Canonical transcript/timeline

One transcript should be reusable by:

- content intelligence
- localization
- Smart Cut
- Creative Remake

Do not perform duplicate ASR passes merely because features are implemented in different modules. Candidate Intelligence delegates its fallback ASR to the canonical Whisper service instead of owning a standalone faster-whisper model.

DownloadedVideo is the shared source identity for Studio. Its detail API exposes
the persisted transcript, transcript language/source and timed transcript
segments. Studio normalizes those source segments to millisecond coordinates
once; Phase D/E consume that contract rather than introducing another source or
ASR model owner. If transcript data is missing, later workflows may invoke the
canonical ASR service and persist the result on the same source.

Source and output coordinates are explicit and distinct:

```
source_start_ms / source_end_ms
target_start_ms / target_end_ms
```

A localization segment may additionally hold speaker, source/translated text, TTS duration and QC status.

## Localization Profiles

A saved profile supplies automation configuration:

```
download/source quality
ASR provider/model/quality
translation provider/model/preset/prompt/glossary
TTS provider/model/voice/rate policy
timing policy
audio mix policy
caption profile
render profile
QC profile
```

Profiles are used by single and batch jobs.

## One localization pipeline

Auto, batch and expert modes share one engine:

```
Media
 ↓
ASR
 ↓
Context/segmentation
 ↓
Translation
 ↓
Timed semantic blocks
 ↓
TTS per block
 ↓
Timing resolution
 ↓
Background/voice mix
 ↓
Captions from resolved timing
 ↓
Minimum-loss final render
 ↓
QC
```

### Auto mode

```
config = saved profile
candidate → pipeline → QC → review
```

### Batch mode

Runs the same pipeline for multiple candidates through the existing job infrastructure.

### Expert mode

```
config = profile + job/segment overrides
```

Expert actions modify the same job state and rerun only affected stages where safe. There is no second manual media engine.

## Quality policy

Phase C treats technical validity and perceptual quality separately.

Important rules:

- prefer source-native/best eligible media before degrading
- no hidden ASR/model downgrade
- avoid repeated lossy encodes; combine visual operations into the final encode when practical
- TTS is timing-aware and block-based
- captions follow final resolved timing
- preserve useful background/music/ambience
- automatic QC surfaces exceptions instead of forcing manual inspection of every segment

## Production modes

The frontend exposes one canonical `/studio` workspace. Localize, Manual Edit
and Smart Cut reuse the same selected Library source. Creative Remake remains
an explicit reserved mode until Phase E.

Phase-D/E workflows use the same source contract:

```
DownloadedVideo id
  -> full source detail
  -> canonical transcript segments / source timeline
  -> Studio mode
  -> durable derivative
  -> existing Library lineage
```

Smart Cut is live without a second media engine:

```
DownloadedVideo
  -> canonical cached transcript / Whisper when missing
  -> existing /suggest-clips AI range proposal
  -> existing SourceBench human adjust/approve
  -> existing /extract-clips manual renderer
  -> GeneratedVideo(source_type="smart_cut",
                    source_downloaded_video_id=<source>)
  -> Library lineage
```

The backend only allows the `smart_cut` derivative identity on manual/approved
ranges; direct AI extraction remains a Clipper output. `creative_remake`
remains reserved for Phase E and will attach the same source lineage rather than
introducing another media graph.

`/review` is the canonical human-exception workspace. It loads workflow
sources through one registry: Localize is live now, while Smart Cut and Creative
Remake have explicit extension contracts. Storyboard / Visual review categories
exist without fake review items. Phase E must stop at storyboard approval before
expensive composition/render.

Existing Stock / Smart Video and Motion Graphics surfaces are asset-provider
capabilities for Creative Remake, not new workflow owners.

### Translate / Dub

Preserve source meaning/content while producing a Vietnamese-localized version through the canonical localization pipeline.

### Smart Cut

Shorten/repackage source material by selecting source ranges. The current Phase-D
implementation reuses the existing Clip Studio proposal bench and renderer:
AI proposes non-overlapping source windows, the user adjusts/approves them, and
only those approved ranges render as `smart_cut` derivatives. There is no
second ASR owner, range editor, job system or FFmpeg clip renderer.

### Creative Remake HQ

Not a synonym for Smart Cut.

```
source
 → canonical transcript
 → deterministic physical shot map
 → representative frames + multimodal shot understanding
 → creative VN script beats
 → beat ↔ source/Library/stock/motion asset candidates
 → persisted storyboard
 → HUMAN APPROVAL
 → canonical composition/render
 → timed voice/captions/audio
 → QC / final review
```

Phase-E donor boundary:

- PySceneDetect owns initial physical shot-boundary detection behind one
  Affiliate2 scene-map interface; direct TransNetV2 is deferred unless real
  media demonstrates a need, and then remains behind the same interface.
- OpenCreator is a contract reference for stable beat/shot IDs, strict
  storyboard validation, artifact lineage and stale-plan rejection.
- MoneyPrinterTurbo is a reference for provider-neutral stock/local/generated
  material acquisition and allocation; it is not a runtime dependency.
- ViralMint Stock/Pexels/B-roll/Motion capabilities remain contextual asset and
  composition primitives rather than becoming a second Creative Remake app.
- Remotion is reference-only; the existing FFmpeg + Motion stack remains the
  renderer ownership boundary.

Physical shot boundaries are deterministic media facts. AI may describe, rank
or map those shots, but it must not invent the source cut coordinates.

Source-video timestamps and output-timeline timestamps must never share one ambiguous field.

## Persistence migration

Transitional state may use both application SQLite and `data/radar.db`.

This is intentional while functionality is ported. Do not attempt a big-bang DB rewrite.

Final target is one application DB containing source observations, localization/review, publication and performance records alongside application entities.

## Runtime

Primary environment is native Windows.

Development:

- FastAPI/Uvicorn on 16888
- Vite HMR on 5173

Stable local:

- built frontend served by FastAPI on 16888

Docker is not required.
