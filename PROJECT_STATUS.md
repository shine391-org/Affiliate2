# PROJECT_STATUS.md

## Overall status: CONSOLIDATION IN PROGRESS

Phase A is complete and merged into the synchronized consolidation baseline.

### Phase A — COMPLETE ✅

Merge commit:
`fbd1a6fec8949bf72098261c57d731a3c484c868`

Validated:

- canonical application source established under `app/`
- source parity repaired
- frontend build PASS
- focused backend/API regression 60/60 PASS
- dev mode PASS
- hot reload PASS
- stable local mode PASS
- stop-script process safety PASS

The full project suite is intentionally reserved for milestone/CI QA rather than every implementation iteration.

---

## Phase B — Sources + Radar Integration — COMPLETE ✅

Status:

QA PASS — merged via PR #6 (squash commit `d20ac9d`).

Branch:
`consolidation-phase-b-sources-radar` (merged into `workspace-sync-2026-09`)

Goal:

Move real Douyin/Bilibili source discovery and Affiliate2 Radar into the canonical application.

Progress:

1. app-native provider contract — COMPLETE, QA PASS
2. real Bilibili keyword + creator-history provider — COMPLETE, QA PASS
3. real Douyin CDP provider — COMPLETE, QA PASS
4. Radar observation/ranking bridge — COMPLETE, QA PASS
5. Scout Radar signal/API/UI integration — COMPLETE, QA PASS

Final QA evidence: focused suite 162 passed / 0 failed; frontend production build PASS; real Bilibili query PASS; real Douyin query via local Edge CDP PASS; Radar repeated observation/velocity PASS; Scout API/UI smoke PASS.

Hard rules:

- no silent cross-platform fallback
- no long-term MediaCrawler subprocess/JSONL contract
- no second dashboard/job system
- content and creator identity remain platform-scoped
- creator outlier uses the creator's own historical baseline

See:
`docs/PHASE_B.md`
`docs/QA_PHASE_B.md`

---

## Later phases

### Phase C — Media + Intelligence convergence

- ViralMint downloader becomes default.
- Remove duplicate Radar downloader.
- ViralMint Whisper becomes sole transcription implementation.
- Merge Affiliate2 domain intelligence into the canonical analysis flow.

### Phase D — Translate HQ + Smart Cut

- contextual Vietnamese translation
- timing-aware block TTS
- Clip Studio for Smart Cut

### Phase E — Creative Remake HQ

- full transcript + visual shot map
- source-shot mapping
- storyboard human approval
- source/output timeline separation

### Phase F — Review / Publish / Performance native migration

- remove overlay copy/sync architecture after parity
- reuse app upload/metrics plumbing
- keep Affiliate2 approval/learning semantics

### Phase G — DB convergence + final cleanup

- migrate to one canonical application DB
- remove dead duplicate code only after verified parity
- milestone/full QA
