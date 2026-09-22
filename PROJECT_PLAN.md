# PROJECT_PLAN.md

## Goal

Converge Affiliate2 into one local application before adding further product features.

Target: one FastAPI backend, one React frontend, one job system, one Library, and eventually one SQLite database.

## Phase A — Canonical application baseline

- Promote captured ViralMint source to `app/`.
- Preserve current behavior.
- Add Windows native setup/dev/stable launchers.
- Add agent, architecture and QA contracts.
- Verify app boots/builds with existing Review Queue and Performance pages.

Exit gate: `docs/QA_PHASE_A.md`.

## Phase B — Source + Radar integration

- Add a source-provider interface under the application.
- Port real Douyin discovery from MediaCrawler capabilities; remove subprocess/JSONL bridge.
- Keep/strengthen real Bilibili provider; no silent YouTube fallback.
- Preserve immutable Radar metric snapshots.
- Fix ranking identity/baseline issues, including platform-scoped creator identity and true current-vs-creator-baseline lift.

## Phase C — Media + intelligence convergence

- Use ViralMint downloader as default ingestion.
- Add platform-specific media resolver only as fallback.
- Remove custom Affiliate2 downloader.
- Use ViralMint Whisper service; remove duplicate transcription wrapper.
- Use ViralMint AI-provider abstraction.
- Merge Affiliate2 localization/remake-specific intelligence fields into one analysis flow.

## Phase D — Production modes

Provide three explicit modes:

1. Translate / Dub — preserve full-video meaning/duration.
2. Smart Cut — AI proposes editable source ranges using Clip Studio.
3. Creative Remake HQ — visual/transcript understanding → script beats → source-shot map → storyboard.

Build HQ Vietnamese translation/dubbing with contextual translation and timing-aware per-block TTS.

## Phase E — Creative Remake HQ

- Full transcript strategy.
- Scene/shot map + visual understanding.
- Separate source timestamps from output timestamps.
- Map every script beat to real source shot(s).
- Storyboard review before final render.
- Reorder/retime/replace source shots.
- Optional fallback/B-roll only when explicitly planned.
- Final human review remains required.

## Phase F — Native Review / Publish / Performance

- Port Review Queue from overlay into canonical application structure.
- Reuse ViralMint upload/auth plumbing where appropriate.
- Preserve Affiliate2 publish approval gate and publication lineage.
- Reuse ViralMint metric collection; preserve Affiliate2 performance intelligence.
- Remove overlay sync mechanism.

## Phase G — Database convergence + full QA

- Migrate Radar/review/publication/performance tables into the canonical application DB.
- Remove second DB after verified migration.
- Remove dead duplicate code/reference runtime paths.
- Run unit, integration, frontend build and real-provider/local E2E QA.
- Produce a clean consolidated PR that supersedes old stacked MVP PRs.

## Explicit non-goals

No Docker requirement, Redis, PostgreSQL, vector DB, microservices, Kubernetes, automatic publishing without human confirmation, or new dashboard framework.
