# Phase B — Sources + Radar Integration

## Status

**COMPLETE — QA PASS — MERGED**

PR #6 was merged with squash commit:

`d20ac9db86acc3dc83f2d7236610ec9cb1291485`

Phase-B closeout/docs commit on `master`:

`ae30ebca156a0a286f920445e69adbddd8d6a018`

The implementation branch was removed after merge. Phase B is closed unless a regression is discovered.

## Goal

Move source discovery and Radar into the canonical `app/` application without changing the media-production stack.

## Delivered

### B1 — Provider contract — COMPLETE ✅

- app-native provider boundary
- keyword search
- optional creator history/detail/media metadata
- platform-identifiable source records
- explicit SUCCESS / EMPTY / AUTH_REQUIRED / ERROR states
- provider-owned auth/session behavior
- no provider-owned UI/job system/DB orchestration

### B2 — Bilibili provider — COMPLETE ✅

- real Bilibili keyword discovery
- creator-history path with WBI signing
- no silent YouTube substitution
- risk-control/malformed envelope handling distinct from normal empty results

### B3 — Douyin provider — COMPLETE ✅

- real Douyin keyword discovery through the user's existing Edge CDP session
- session discovery across browser contexts
- provider-owned auth state
- no canonical MediaCrawler subprocess + JSONL handoff

### B4 — Radar observation/ranking bridge — COMPLETE ✅

- repeated metric observations
- platform-scoped content identity `(platform, content_id)`
- platform-scoped creator identity `(platform, creator_id)`
- platform-scoped snapshot counts
- observed velocity from repeated snapshots
- creator-relative outlier against the same creator's historical baseline
- first-discovery `source_keyword` provenance preserved
- per-run query history persisted through `source_runs`
- legacy engagement/velocity ranking semantics preserved during migration

### B5 — Scout integration — COMPLETE ✅

- Scout remains the visible discovery surface
- Radar score/confidence/velocity/outlier data exposed through API/live/chat result paths
- Radar signal UI integration
- no second Radar dashboard

## Final QA evidence

- focused Phase-B suite: **162 passed / 0 failed**
- frontend production build: PASS
- real Bilibili query: PASS
- real Douyin query via local Edge CDP: PASS
- repeated-observation/velocity validation: PASS
- Scout API/UI smoke: PASS
- production code did not change after runtime QA; the final pre-merge change was a test-fixture correction

See `docs/QA_PHASE_B.md` for the QA procedure/evidence details.

## Hard rules preserved

- no silent cross-platform fallback
- no long-term MediaCrawler subprocess/JSONL contract
- no second discovery dashboard/job system
- content and creator identity remain platform-scoped
- creator outlier uses the creator's own historical baseline

## Intentionally deferred

Phase B did not:

- replace the downloader
- converge ASR/Whisper
- redesign AI providers
- implement Translate/Dub HQ
- migrate Radar to the final canonical DB
- implement Creative Remake HQ
- migrate Review/Publish/Performance
- delete legacy modules before parity

Those concerns move forward under the later phase roadmap. Media/AI/Translate-Dub quality work starts in Phase C.
