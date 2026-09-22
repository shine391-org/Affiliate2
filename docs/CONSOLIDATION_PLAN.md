# Consolidation Plan

## Why this exists

The workspace audit found useful Affiliate2 domain logic mixed with duplicated infrastructure already implemented more robustly in ViralMint. Continuing feature development before consolidation would increase maintenance cost and hide product-quality bugs.

The immutable baseline is `workspace-sync-2026-09`.

## Phase A — Application baseline

Status: **IMPLEMENTING**

Deliverables:

- `app/` promoted from captured ViralMint tree.
- Native Windows bootstrap/dev/run/stop scripts.
- Current overlay behavior remains reachable.
- Standard agent/architecture/QA documents.
- No discovery/downloader/translate/remake behavior changes.

Acceptance: `docs/QA_PHASE_A.md`.

## Phase B — Sources + Radar

Status: PLANNED

- Introduce app-native provider contract.
- Port Douyin platform logic; remove subprocess + JSONL handoff.
- Keep real Bilibili provider and enrich it selectively.
- Persist repeated observations.
- Repair creator-baseline/ranking identity semantics.
- Integrate Radar scores into Scout result/review flow.

## Phase C — Media + intelligence

Status: PLANNED

- ViralMint downloader becomes default.
- Platform media resolver becomes fallback only.
- Remove custom Radar downloader.
- ViralMint Whisper becomes sole transcription service.
- Remove duplicate direct AI HTTP client.
- Produce one intelligence object with Affiliate2 domain fields.

## Phase D — Translate HQ + Smart Cut

Status: PLANNED

- Contextual Vietnamese translation modes/presets.
- Configurable model/provider/voice controls.
- Timing-aware block TTS.
- Captions derived from final timed blocks.
- Rename/position short source extraction as Smart Cut.
- Reuse Clip Studio range proposal/editing.

## Phase E — Creative Remake HQ

Status: PLANNED

- Full transcript handling.
- Shot/scene extraction.
- Multimodal visual map.
- VN creative beats.
- explicit source shot mapping.
- storyboard review.
- renderer consumes approved source ranges/reorder.
- no legacy first-N-seconds fallback.

## Phase F — Review / Publish / Performance native migration

Status: PLANNED

- Move overlay pages/API into canonical app.
- Reuse app upload/auth plumbing.
- Preserve publish approval gate and publication lineage.
- Reuse metric collectors.
- Keep Affiliate2 performance intelligence.
- delete overlay sync only after parity.

## Phase G — DB convergence + cleanup

Status: PLANNED

- Migrate second-DB tables incrementally.
- Verify counts/relationships before removing old DB paths.
- Remove dead duplicate modules/reference runtime glue.
- Full local/provider QA.
- Consolidated PR to master.

## Migration rule

Every phase must leave the app runnable. No big-bang rewrite and no simultaneous rewrite of source providers, DB, UI and rendering semantics in one commit.
