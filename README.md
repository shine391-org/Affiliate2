# Affiliate2 — AI Content Distribution Engine

Affiliate2 is a local-first content engine built around one application foundation: ViralMint, extended with Affiliate2's Radar, Vietnamese localization/remake workflow, human review, publishing gates and performance learning.

## Product loop

```
Source Discovery
  → Radar observations/ranking
  → Download + Library
  → Transcript + Content Intelligence
  → Translate/Dub OR Smart Cut OR Creative Remake HQ
  → Human Review
  → Publish
  → Performance
  → Learning
```

The project is currently in **architecture consolidation**. The goal is to stop maintaining duplicate downloader/transcription/media/job/UI plumbing and converge onto one application.

## Repository layout during consolidation

```
app/                    Canonical runnable application (ViralMint foundation)
src/radar/              Existing Affiliate2 domain code; migrated incrementally
overlay/                 Legacy frontend overlay source; temporary
viralmint_overlay/       Legacy backend overlay source; temporary
vendor/viralmint/        Immutable captured ViralMint baseline/reference
vendor/mediacrawler/     Immutable MediaCrawler donor/reference snapshot
docs/                    Architecture, development and QA contracts
scripts/                 Native Windows launch/setup utilities
```

Do not develop new runtime features inside `vendor/`.

## Run locally on Windows

Prerequisites: Python 3.11+, Node.js 18+, FFmpeg. ImageMagick is recommended for all media features.

First setup:

```powershell
.\scripts\bootstrap.ps1
```

Develop with Vite HMR + FastAPI reload:

```powershell
.\scripts\dev.ps1
```

For long-running render/transcription jobs, keep the backend stable while retaining frontend HMR:

```powershell
.\scripts\dev.ps1 -StableBackend
```

Run a built production-like local version:

```powershell
.\scripts\run.ps1
```

Stop processes started by these scripts:

```powershell
.\scripts\stop.ps1
```

Development UI: `http://localhost:5173`

Stable local UI: `http://localhost:16888`

## Current architectural decisions

- ViralMint is the app shell and media engine.
- ViralMint downloader/Library/Whisper/jobs/FFmpeg/TTS/captions/Clip Studio are preferred over duplicate Affiliate2 implementations.
- MediaCrawler is used as source-provider donor/reference code, not as a second long-term application.
- Bilibili must use real Bilibili discovery, never silent YouTube fallback.
- Radar's repeated metric snapshots and domain ranking/learning stay Affiliate2-owned.
- Smart Cut is active through the existing ViralMint Clip Studio path: AI proposals → editable/approved ranges → shared manual renderer → `smart_cut` Library derivatives.
- Creative Remake HQ will be rebuilt around source-shot mapping + storyboard approval.
- Human approval remains mandatory before publishing.
- Docker is not required for local operation.

Read `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/CONSOLIDATION_PLAN.md` and `PROJECT_STATUS.md` before making changes.
