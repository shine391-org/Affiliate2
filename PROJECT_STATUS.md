# PROJECT_STATUS.md

## Overall status: PHASE C FULL HQ STACK IMPLEMENTED — FINAL LOCAL QA PENDING

Phase A and Phase B are complete and merged to `master`.

### Phase A — COMPLETE ✅
Merge commit: `fbd1a6fec8949bf72098261c57d731a3c484c868`

### Phase B — COMPLETE ✅
Squash commit: `d20ac9db86acc3dc83f2d7236610ec9cb1291485`
Closeout: `ae30ebca156a0a286f920445e69adbddd8d6a018`

---

## Phase C — Media, AI & Automated HQ Localization

Branch:
`phase-c-media-ai-hq-localization`

### Stable foundation
- C1 Media quality: QA PASS
- C2 AI/local provider controls: QA PASS
- C3 Localization Profiles: QA PASS

### Real-media failure that triggered donor-first rebuild
The first 65.7s sample exposed the old C7 prototype:
- 21 usable translated speech units
- 15/21 timing overflows
- most Vietnamese dub missing

The failed architecture treated raw ASR segments too directly as TTS units.

### Donor-first implementation now in branch

Quality-critical behavior has been ported/adapted from mature donors:
- VideoLingo (Apache-2.0)
- SmartSub (MIT)
- OpenCreator (Apache-2.0)
- YouDub-webui (Apache-2.0)
- pyVideoTrans: timing/retiming reference only because GPL-3.0

Current implementation:
- C4R canonical words / semantic cues / target timeline / dubbing chunks: IMPLEMENTED
- C5 faster-whisper + WhisperX forced alignment + optional diarization: IMPLEMENTED
- C6R word-aware semantic segmentation + validated contextual translation: IMPLEMENTED
- C7R speaker-aware smart dubbing, chunk TTS, measured fit, rewrite, silence borrowing, atempo, bounded retiming: IMPLEMENTED
  - duration rewrite AI is best-effort; provider timeout degrades to measured TTS/timing fallback + QC review instead of aborting dubbing
- C8R Demucs HQ clean dub + multi-provider HQ TTS + voice cloning reference extraction + one final video encode: IMPLEMENTED
- C9 durable stage artifacts + version-2 manifest guard + reproducible runtime bindings + resume + rerun-from-stage + batch: IMPLEMENTED
- C10 automatic QC + exception queue: IMPLEMENTED
- C11 Expert workspace + translation/timing/voice overrides + targeted chunk regeneration: IMPLEMENTED

### HQ TTS / speaker capabilities
Supported provider layer now includes:
- Edge TTS preview/fallback
- OpenAI TTS
- OpenAI-compatible TTS
- Azure Speech
- ElevenLabs
- GPT-SoVITS local voice cloning (donor-compatible EN/ZH only; not a Vietnamese acceptance path)

Multi-speaker flow:
ASR → forced alignment → optional diarization → stable speaker IDs → semantic cues → speaker-safe chunks → speaker voice/reference mapping → target dub.

GPT-SoVITS can use manual reference audio or automatically extract reference speech from the separated vocal/source timeline per speaker, within its donor-compatible EN/ZH language contract. Vietnamese HQ acceptance must use a Vietnamese-capable provider.

### Control / credentials
- AI provider/model/base URL BYOK remains encrypted.
- TTS provider/model/base URL/region/BYOK is configurable in Settings.
- Hugging Face token for gated WhisperX/pyannote diarization models is encrypted in Settings with .env fallback.

### Product surfaces
- Translate + Dub uses Localization Profiles and the canonical HQ pipeline.
- Localization Batch uses the same runner for up to 20 jobs; no cheaper second engine.
- Localization Review exposes QC exceptions and the same persisted artifacts.
- Expert panel can edit translation/timing/voice, regenerate one dubbing chunk, or rerun downstream stages.

### Current gate
**FINAL LOCAL QA PENDING.**

Do not start C12 cleanup until the complete C4–C11 stack passes:
1. focused/full affected tests;
2. frontend build;
3. same previously failing 65.7s sample;
4. real HQ clean-dub runtime;
5. resume/rerun/QC/expert controls;
6. user perceptual QA.

### C12
Intelligence convergence is intentionally AFTER the localization acceptance gate.

Reason: `src/radar/intel/*` is a transitional legacy intelligence path and is not the media path that renders Phase-C localized output. Removing it before proven parity creates unrelated regression risk. After Phase-C video acceptance, C12 can remove/converge it with focused regression rather than another full perceptual-video round.

### C13
Final acceptance = same regression sample + representative real supported videos supplied by the user, including fast speech, music-heavy and multi-speaker material.

See:
- `docs/PHASE_C.md`
- `docs/PHASE_C_C0_DONOR_AUDIT.md`
- `docs/PHASE_C_DONOR_CAPABILITY_MATRIX.md`
- `docs/QA_PHASE_C.md`
