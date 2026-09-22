# AGENTS.md — Affiliate2 Agent Operating Contract

This file is the first document every coding/research/QA agent must read.

## Mission

Affiliate2 is a local-first AI content distribution engine:

Source discovery → Radar ranking → content intelligence → Vietnamese localization/remake → human review → publish → performance feedback.

The project is distribution-first. Affiliate monetization comes later, after repeatable traffic.

## Current consolidation state

The immutable source snapshot is branch `workspace-sync-2026-09`.

Active implementation branch:
`consolidation-phase-b-sources-radar`.

Phase A is complete. Canonical application source is:

`app/`

`app/` is an exact promotion of the captured ViralMint working tree at the start of Phase A. It includes the existing Affiliate2 review/performance overlay that had already been synchronized into ViralMint.

Reference snapshots remain:

- `vendor/viralmint/` — forensic baseline; DO NOT edit.
- `vendor/mediacrawler/` — donor/reference code; DO NOT edit.
- `src/radar/` — current Affiliate2 Radar/intelligence/remake implementation; still active for legacy paths until ported.

Do not add new runtime code under `vendor/`.

Current Phase B scope is source-provider + Radar integration only. Read `docs/PHASE_B.md` and `docs/QA_PHASE_B.md` before changing provider/Scout/Radar code.

## Architecture direction

ViralMint is the application foundation: FastAPI, React/Vite, jobs, Library, downloader, Whisper, FFmpeg, captions, TTS, Clip Studio and common upload/metrics plumbing.

Affiliate2 owns the domain-specific layers:

- source-provider integration for real Douyin/Bilibili discovery,
- Radar snapshots/ranking/learning,
- Vietnamese localization quality,
- Creative Remake HQ planning + storyboard QC,
- human approval gates,
- performance intelligence.

MediaCrawler is donor/reference code. Long-term it must not remain a second application, second WebUI, second DB or subprocess pipeline.

## Non-negotiable engineering rules

1. **Do not duplicate OSS plumbing that ViralMint already does well.**
   Before adding downloader, Whisper, FFmpeg, TTS, job, Library, upload or metrics infrastructure, inspect `app/`.

2. **One application, not cooperating mini-apps.**
   Target is one FastAPI backend, one React frontend, one job system and eventually one SQLite database.

3. **Human approval stays a hard gate.**
   No action may silently publish. Creative Remake HQ must have storyboard review before expensive final render.

4. **Current legacy Creative Remake is not a production-quality reference.**
   Its planner confuses output timeline timestamps with source-video timestamps and ignores generated reuse/reorder intent. Do not extend that semantic mistake.

5. **Smart Cut and Creative Remake are different products.**
   - Smart Cut: select/retime/reorder source ranges; reuse ViralMint Clip Studio.
   - Creative Remake HQ: transcript + visual map → VN script beats → source-shot mapping → storyboard → human approval → render.

6. **Use ViralMint AI/provider and Whisper abstractions.**
   Do not introduce direct provider-specific HTTP clients unless a capability cannot be expressed through the existing abstraction and the reason is documented.

7. **Local-first / no overengineering.**
   No Redis, PostgreSQL, vector DB, microservices, Kubernetes or Docker requirement without a measured bottleneck. SQLite remains the default.

8. **No silent fallback that changes semantics.**
   A requested Bilibili search must not quietly become YouTube results. A failed creative plan must not silently become “first N seconds”.

9. **Preserve source/output coordinate separation.**
   Source media timestamps and output timeline timestamps are different data types in all remake planning.

10. **Evidence-driven QA.**
    Passing unit tests is not proof of product behavior. Report exactly what was run, what output was inspected, and what was not tested.

11. **Use tiered tests; do not run the full suite on every change.**
    - Tier 1 — every small change: directly affected tests + import/build/smoke checks. Target: seconds to a few minutes.
    - Tier 2 — phase/module gate: affected module/integration tests + relevant UI/runtime smoke. Target: a few minutes.
    - Tier 3 — full suite: only before a major merge/release, after broad shared-core/DB changes, or when diagnosing cross-cutting regressions. Long full-suite runs should be background/CI work, never the default inner loop.
    - A 20–30 minute suite is NOT required for a docs, source-parity, launcher, or narrowly scoped frontend fix when focused tests prove the changed surface.

## Branch/change protocol

- Never develop directly on `workspace-sync-2026-09`; it is a snapshot baseline.
- Never push product work directly to `master`.
- Work on a named implementation branch.
- Keep commits scoped to one migration/capability.
- Do not merge a phase until its QA checklist passes.
- Do not rewrite historical snapshot/vendor commits.
- If a change alters architecture, update `docs/ARCHITECTURE.md`, `docs/CONSOLIDATION_PLAN.md` and `PROJECT_STATUS.md`.

## Local development

Windows is the primary development environment.

Initial setup:

```powershell
.\scripts\bootstrap.ps1
```

Hot-reload development:

```powershell
.\scripts\dev.ps1
```

Use stable backend during long Whisper/render jobs:

```powershell
.\scripts\dev.ps1 -StableBackend
```

Production-like local run:

```powershell
.\scripts\run.ps1
```

Stop managed local processes:

```powershell
.\scripts\stop.ps1
```

See `docs/DEVELOPMENT.md`.

## Definition of done for an agent task

An implementation report must include:

- branch and commit SHA,
- files changed,
- behavior changed,
- commands/tests actually run,
- runtime/manual checks actually performed,
- known gaps/blockers,
- confirmation that no unrelated architecture was introduced.

Never say “tested” when only code was written or statically inspected.
