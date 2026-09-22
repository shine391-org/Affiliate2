# Local Development — Windows

Affiliate2 intentionally does not require Docker for local development.

## Prerequisites

- Python 3.11+
- Node.js 18+
- FFmpeg on PATH
- ImageMagick recommended/required by some media features
- Git

The canonical app is `app/`.

Runtime data remains outside the app source. Launch scripts set:

- `VIRALMINT_DATA_DIR=<repo root>`
- `CONTENT_ENGINE_ROOT=<repo root>`
- `PYTHONPATH=<repo root>;<repo root>\app`

This preserves the current legacy overlay/Radar imports while consolidation is in progress.

## First setup

```powershell
.\scripts\bootstrap.ps1
```

This creates `.venv` at repo root, installs `app/requirements.txt`, installs frontend packages, and creates root `.env` from `app/.env.example` only when no root `.env` exists.

Secrets remain local and ignored.

## Development mode

```powershell
.\scripts\dev.ps1
```

- backend: http://localhost:16888
- frontend Vite: http://localhost:5173
- backend auto-reloads on Python changes
- frontend uses Vite HMR

Long Whisper/render jobs can be interrupted by backend reload. For those tests:

```powershell
.\scripts\dev.ps1 -StableBackend
```

The frontend still hot-reloads, but Uvicorn does not restart on file changes.

## Stable local mode

```powershell
.\scripts\run.ps1
```

This builds the frontend and runs one stable Uvicorn process serving the built SPA at:

http://localhost:16888

Use this for workflow QA and normal local use.

## Stop

```powershell
.\scripts\stop.ps1
```

Only process trees recorded by the launch scripts are terminated.

## Important current transitional dependency

Legacy Radar/Douyin paths may still expect the existing local `tools/MediaCrawler` checkout until Phase B/C ports those capabilities into `app/`.

The vendor snapshot is reference source, not the runtime path for those legacy calls.

## Tests

Do not claim a test passed unless the command was actually executed.

Expected Phase A checks are documented in `docs/QA_PHASE_A.md`.
