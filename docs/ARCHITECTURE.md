# Affiliate2 Architecture

## Target system

```
                         Affiliate2
                             |
                 ViralMint application foundation
                             |
        +--------------------+--------------------+
        |                    |                    |
      Scout                Library              Tools
        |                    |                    |
 Source Providers       DownloadedVideo      Media Engine
        |                    |              FFmpeg/Whisper/
        |                    |              TTS/Captions
        v                    v                    |
      Radar ----------> Intelligence              |
        |                    |                    |
        +-------------+------+--------------------+
                      |
        Translate / Smart Cut / Remake HQ
                      |
               Human Review Gates
                      |
                    Publish
                      |
                  Performance
                      |
                    Learn
```

## Canonical source during consolidation

`app/` is the runnable application tree.

`vendor/viralmint/` and `vendor/mediacrawler/` are immutable forensic/reference snapshots. Runtime changes must not be made there.

Legacy `src/radar/`, `viralmint_overlay/` and `overlay/` remain temporarily because current behavior still reaches them. They are migrated incrementally, not deleted early.

## Ownership boundaries

### ViralMint foundation

Prefer/reuse:

- FastAPI application + React/Vite UI
- Job model/dispatcher and WebSocket progress
- Library + media lineage
- yt-dlp downloader and retry/cookie/format handling
- Whisper service
- FFmpeg/media utilities
- TTS abstraction
- Caption engine
- Clip Studio
- common upload/auth adapters
- performance metric collection primitives

### Affiliate2 differentiated domain

Keep/port:

- repeated source metric observations
- Radar ranking and learning
- source-provider selection semantics
- Vietnamese localization quality
- localization/remake-specific intelligence
- Creative Remake HQ planner and storyboard QC
- candidate/final human approval semantics
- publish gate
- performance intelligence joined to topic/format/hook/localization/remake dimensions

### MediaCrawler donor code

Use selectively for platform-specific capabilities, especially:

- Douyin authenticated/CDP session handling
- Douyin real search/detail/creator integration
- Bilibili WBI/auth/detail/media behavior when superior to current provider

Do not keep its WebUI, DB/store, global config or whole crawler orchestration as a parallel application.

## Discovery

Scout is the UI/orchestration surface.

A provider must return results from the platform the user requested. Silent semantic substitution is forbidden. In particular, Bilibili discovery cannot silently return YouTube cross-posts.

Radar is not a competing Scout. Radar is the observation/ranking layer behind discovery:

```
provider result → normalized content → metric snapshot
                                ↓
                         repeated scan
                                ↓
                  observed velocity/baseline
                                ↓
                           candidate rank
```

## Media ingestion

Default:

```
canonical URL → ViralMint downloader → DownloadedVideo → Library
```

Only if the generic downloader cannot retrieve an eligible source should a platform provider resolve specialized media streams.

The legacy `src/radar/intel/media.py` implementation is scheduled for removal.

## Intelligence

Use one transcript and one provider abstraction.

ViralMint supplies transcription and generic content analysis primitives. Affiliate2 extends the result with domain fields such as Vietnamese angle, localization potential, remake difficulty and dependency/risk signals.

Do not perform duplicate Whisper passes merely because two modules need the transcript.

## Production modes

### Translate / Dub

Preserves source content. HQ path will translate with full-context awareness, build timing-aware semantic blocks, synthesize per block, fit durations, place blocks on the source timeline and derive captions from the same blocks.

### Smart Cut

Shortens/repackages source material. Reuse Clip Studio's proposal + editable-range workflow.

### Creative Remake HQ

Not a synonym for Smart Cut.

```
source
 → transcript
 → scene/shot map
 → creative VN script beats
 → beat ↔ source-shot mapping
 → storyboard
 → HUMAN APPROVAL
 → render/reorder
 → timed TTS/captions/audio
 → final review
```

Source-video timestamps and output-timeline timestamps must never share one ambiguous field.

## Persistence migration

Transitional state may use both ViralMint SQLite and `data/radar.db`.

This is intentional while functionality is ported. Do not attempt a big-bang DB rewrite.

Final target is one application DB containing source observations, review, publication and performance records alongside application entities.

## Runtime

Primary environment is native Windows.

Development:
- FastAPI/Uvicorn on 16888
- Vite HMR on 5173

Stable local:
- built frontend served by FastAPI on 16888

Docker is not required.
