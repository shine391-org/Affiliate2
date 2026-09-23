# PROJECT_STATUS.md

## Overall status: PHASE C STARTED — DONOR REALIGNMENT

Phase A and Phase B are complete and merged to `master`.

### Phase A — COMPLETE ✅
Merge commit: `fbd1a6fec8949bf72098261c57d731a3c484c868`

### Phase B — COMPLETE ✅
Squash commit: `d20ac9db86acc3dc83f2d7236610ec9cb1291485`
Closeout: `ae30ebca156a0a286f920445e69adbddd8d6a018`

Validated Phase B:
- canonical application source
- Bilibili + Douyin provider paths
- Radar observations/velocity
- Scout integration
- 162 focused tests
- frontend build
- real-provider/runtime smoke

---

## Phase C — Media, AI & Automated HQ Localization — REALIGNMENT 🚧

Branch:
`phase-c-media-ai-hq-localization`

### Stable/kept foundation
- C1 media quality: QA PASS
- C2 AI/local provider controls: QA PASS
- C3 Localization Profiles: QA PASS
- C4 source/target coordinate separation: useful foundation, now being extended

### Real-media finding that triggered rework
At C5–C8 checkpoint on a 65.7s real sample:
- C5 ASR: PASS
- C6 translation: PASS
- C7 timed dubbing: FAIL
- 15/21 timed speech units overflowed
- most target-language dub was absent

Root architectural problem:
raw ASR segments were treated too directly as TTS units. Mature donors already separate semantic/display units from dubbing chunks.

### Donor-first correction
Phase C now ports/adapts proven behavior from:
- VideoLingo (Apache-2.0)
- SmartSub (MIT)
- OpenCreator (Apache-2.0)
- YouDub-webui (Apache-2.0)

pyVideoTrans (GPL-3.0) remains reference-only unless licensing is explicitly approved.

### Donor-derived rework now implemented
- C4R representation split: IMPLEMENTED / QA PENDING
- C5 ASR fixes retained
- C6R semantic normalization + validated AI grouping + contextual translation: IMPLEMENTED / QA PENDING
- C7R OpenCreator-derived chunk planner / measured TTS / post-TTS atempo fit: IMPLEMENTED / QA PENDING
- C8R YouDub-derived Demucs HQ clean-dub path: IMPLEMENTED / QA PENDING

### Current gate
C9+ remains blocked.

Required next step:
rerun focused local QA + the same previously failing 65.7s sample, then user perceptual QA.

See:
- `docs/PHASE_C.md`
- `docs/PHASE_C_C0_DONOR_AUDIT.md`
- `docs/QA_PHASE_C.md`
