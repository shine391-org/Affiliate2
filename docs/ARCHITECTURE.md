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

The legacy `src/radar/intel/media.py` path is removed only after parity is proven.

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

## Canonical transcript/timeline

One transcript should be reusable by:

- content intelligence
- localization
- Smart Cut
- Creative Remake

Do not perform duplicate ASR passes merely because features are implemented in different modules.

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

### Translate / Dub

Preserve source meaning/content while producing a Vietnamese-localized version through the canonical localization pipeline.

### Smart Cut

Shorten/repackage source material by selecting/retiming/reordering source ranges. Reuse Phase-C media/transcript/timeline and Clip Studio where fit.

### Creative Remake HQ

Not a synonym for Smart Cut.

```
source
 → canonical transcript
 → scene/shot map
 → creative VN script beats
 → beat ↔ source/other-material mapping
 → storyboard
 → HUMAN APPROVAL
 → automated composition
 → timed voice/captions/audio
 → final review
```

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
