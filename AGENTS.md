# AGENTS.md — Affiliate2 Agent Operating Contract

This file is the first document every coding/research/QA agent must read.

## Mission

Affiliate2 is a local-first AI content distribution engine:

Source discovery → Radar ranking → content intelligence → Vietnamese localization/remake → human review → publish → performance feedback.

The project is distribution-first. Affiliate monetization comes later, after repeatable traffic.

## Current state

Canonical application source:

`app/`

Completed:

- Phase A — application baseline
- Phase B — Sources + Radar, QA PASS, merged to `master`

Current implementation branch:

`phase-c-media-ai-hq-localization`

Current scope:

Phase C — Media, AI & Automated HQ Localization.

Read before changing Phase-C code:

- `docs/PHASE_C.md`
- `docs/QA_PHASE_C.md`
- `docs/ARCHITECTURE.md`
- `docs/CONSOLIDATION_PLAN.md`

Reference snapshots remain immutable:

- `vendor/viralmint/` — forensic/reference baseline; DO NOT edit
- `vendor/mediacrawler/` — donor/reference code; DO NOT edit
- `src/radar/` — legacy/transitional Affiliate2 code until explicitly migrated

Do not add new runtime code under `vendor/`.

## Architecture direction

Affiliate2 is the product. ViralMint is the current application foundation and a source of reusable plumbing, not a requirement to preserve weak defaults.

Prefer/reuse canonical app capabilities when they are fit for purpose:

- FastAPI + React/Vite shell
- job/progress infrastructure
- Library/media lineage
- downloader framework
- FFmpeg/media utilities
- ASR/Whisper abstraction
- AI-provider abstraction
- TTS abstraction
- caption primitives
- Clip Studio
- upload/auth/metrics plumbing

Quality-critical behavior may be replaced or extended when evidence shows the inherited implementation is inadequate.

Phase-C research references include:

- YouDub — production-oriented localization pipeline patterns
- SmartSub — provider/config, dubbing workbench and exception-review patterns
- VideoLingo — translation/subtitle intelligence patterns
- pyVideoTrans — timing/synchronization algorithm reference; GPL code must not be copied casually
- OpenCreator — automation + expert-workspace/state pattern
- faster-whisper / WhisperX — ASR and alignment references
- MoneyPrinterTurbo — later Creative Remake composition/reference, not the Phase-C localization engine

Do not import these projects as cooperating mini-apps. Adapt only the capability needed behind Affiliate2's canonical interfaces.

## Non-negotiable engineering rules

1. **One application, one canonical pipeline.**
   Target is one FastAPI backend, one React frontend, one job system and eventually one SQLite database. Auto, batch and expert modes must call the same underlying pipeline.

2. **Reuse good plumbing; replace weak defaults with evidence.**
   Do not duplicate canonical app infrastructure unnecessarily, but do not keep low-quality downloader, ASR, translation, dubbing or render behavior merely because it came from ViralMint.

3. **Automation-first, not editor-first.**
   The happy path is candidate + saved profile → automatic processing → QC → review-ready output. Transcript, prompt, timing and voice editing are optional expert overrides, not required steps.

4. **Human approval remains a hard publish gate.**
   No action may silently publish. Creative Remake HQ retains storyboard approval before expensive final render.

5. **Source and output timelines are distinct data types.**
   Never reuse one ambiguous `start/end` field for both. Localization, Smart Cut and Creative Remake must preserve coordinate meaning explicitly.

6. **Use canonical provider abstractions.**
   Extend the app AI/ASR/TTS interfaces instead of adding scattered provider-specific HTTP clients. OpenAI-compatible local/cloud endpoints must remain possible.

7. **No hidden quality downgrade or semantic fallback.**
   Examples: requested Bilibili cannot become YouTube; "Best" ASR cannot silently run a smaller model; failed localization cannot silently become a different production mode.

8. **Do not create a second media engine for manual mode.**
   Expert overrides change configuration/job state and rerun affected stages of the same pipeline.

9. **Smart Cut and Creative Remake are different products.**
   - Smart Cut: select/retime/reorder source ranges.
   - Creative Remake HQ: understand source → create VN beats → map source/other material → storyboard approval → compose/render.

10. **Local-first / no overengineering.**
    No Redis, PostgreSQL, vector DB, microservices, Kubernetes or Docker requirement without a measured bottleneck. SQLite remains the default.

11. **Respect donor licenses.**
    Reference algorithms and architecture may be studied. Code reuse must be license-compatible and documented. GPL/AGPL code is reference-only unless a deliberate licensing decision is made.

12. **Evidence-driven QA.**
    Passing unit tests or producing a playable MP4 is not proof of product quality. Report exactly what was run, what real output was inspected and what remains unverified.

13. **Use tiered tests.**
    - Tier 1 — directly affected tests + import/build/smoke
    - Tier 2 — phase/module integration + relevant UI/runtime smoke
    - Tier 3 — full suite only before major merge/release, broad shared-core/DB change or cross-cutting diagnosis

## Phase-C hard boundaries

Phase C includes:

- canonical media ingestion and source-quality policy
- AI/local-AI provider configuration
- Localization Profiles
- canonical transcript/timeline contracts
- ASR-quality routing
- contextual Vietnamese translation
- timing-aware dubbing
- audio/background mixing
- minimum-loss final rendering
- batch automation
- QC + exception queue
- expert overrides on the same job
- intelligence convergence on the same media/transcript/provider path

Phase C does not include:

- lip-sync
- avatar generation
- a CapCut-like full editor
- automatic publishing
- performance-learning rewrites
- generative B-roll system
- Smart Cut implementation
- Creative Remake HQ implementation
- big-bang DB convergence

## Branch/change protocol

- Historical snapshot branches are not development targets.
- Never push product work directly to `master`.
- Work on the named implementation branch.
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

Use stable backend during long ASR/render jobs:

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

- branch and commit SHA
- files changed
- behavior changed
- commands/tests actually run
- runtime/manual checks actually performed
- real-media evidence when the task affects media quality
- known gaps/blockers
- confirmation that no unrelated architecture was introduced

Never say "tested" when only code was written or statically inspected.
