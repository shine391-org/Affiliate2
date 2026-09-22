# PROJECT_STATUS.md

## Overall status: PHASE C STARTED

Phase A and Phase B are complete and merged to `master`.

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

---

## Phase B — Sources + Radar Integration — COMPLETE ✅

Merged via PR #6.

Squash commit:
`d20ac9db86acc3dc83f2d7236610ec9cb1291485`

Phase-B closeout/docs commit:
`ae30ebca156a0a286f920445e69adbddd8d6a018`

Validated:

- app-native provider contract
- real Bilibili keyword search + creator history
- real Douyin discovery through Edge CDP
- Radar repeated observations and velocity
- creator-relative outlier semantics
- Scout Radar API/UI integration
- focused Phase-B suite: 162 passed / 0 failed
- frontend production build PASS
- real-provider/runtime smoke PASS

Phase B is closed. It must not be reopened unless a regression is discovered.

See:
`docs/PHASE_B.md`
`docs/QA_PHASE_B.md`

---

## Phase C — Media, AI & Automated HQ Localization — STARTED 🚧

Implementation branch:
`phase-c-media-ai-hq-localization`

Goal:

Build one canonical media/AI/localization path that can take a Radar candidate to a review-ready Vietnamese video automatically, while retaining expert controls as optional overrides on the same pipeline.

Phase C absorbs the previous "Media + Intelligence" work and the Translate/Dub HQ foundation because those concerns share the same downloader, ASR, AI-provider, transcript, timeline, TTS and render contracts.

Current implementation state:

- C0 donor/source audit: COMPLETE
- C1 media quality foundation: IMPLEMENTED / QA PENDING
- C2 AI + local/OpenAI-compatible provider controls: IMPLEMENTED / QA PENDING
- C3 Localization Profiles: IMPLEMENTED / QA PENDING
- C4 canonical transcript/timeline contract: IMPLEMENTED / QA PENDING
- next implementation gate: C5 ASR convergence

C1–C4 intentionally reuse existing app/job/storage/provider primitives. Legacy Radar intelligence media/AI paths remain transitional until C12 parity; they are not new Phase-C runtime architecture.

Primary outcomes:

1. source-quality-aware canonical media ingestion
2. one canonical AI/provider abstraction with cloud + local OpenAI-compatible support
3. reusable Localization Profiles for automation
4. one canonical transcript/timeline model with source/output coordinate separation
5. configurable ASR quality with no hidden downgrade
6. contextual Vietnamese translation with prompt/preset/glossary controls
7. timing-aware block TTS rather than one whole-video TTS blob
8. background/audio preservation and controlled mixing
9. one-pass or minimum-loss final render path
10. batch automation, QC and exception queue
11. expert override/retry on the same job state
12. one transcript/intelligence path reused by later Smart Cut and Creative Remake

Phase-C acceptance requires real Douyin/Bilibili media, perceptual QA and measurable media/timing evidence. A valid MP4 alone is not a product-quality PASS.

See:
`docs/PHASE_C.md`
`docs/QA_PHASE_C.md`

---

## Later phases

### Phase D — Smart Cut

- shorten/repackage source material
- source-range proposal/editing
- retime/reorder approved ranges
- reuse canonical media, transcript and timeline from Phase C
- keep Smart Cut distinct from Creative Remake

### Phase E — Creative Remake HQ

- full transcript + visual shot map
- VN creative beats
- explicit source-shot/material mapping
- storyboard human approval
- automated creative assembly/render
- source/output timeline separation
- evaluate MoneyPrinterTurbo-style composition patterns without importing a second application

### Phase F — Review / Publish / Performance native migration

- remove overlay copy/sync architecture after parity
- reuse app upload/metrics plumbing
- preserve approval/publication lineage
- keep Affiliate2 performance-learning semantics

### Phase G — DB convergence + final cleanup

- migrate to one canonical application DB
- remove dead duplicate code only after verified parity
- milestone/full QA
