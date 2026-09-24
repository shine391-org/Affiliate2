# PROJECT_STATUS.md

## Overall status: PHASE C C4–C12 TECHNICAL PASS — C13 REAL-MEDIA ACCEPTANCE ACTIVE

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
- UX1 + UX2 shell is implemented: workflow sidebar, Discover parent with Spy / Channels / Candidates, backward-compatible legacy routes, and contextual candidate-to-Library/Studio lineage.
- Saved/shortlist remains deferred until the backend has a canonical persisted state; the UI does not emulate it locally.

### C4–C11 technical gate
**TECHNICAL PASS** on the clean real-media job at commit `b5e3609`.

Verified engineering evidence includes real C7 timing success without relaxing profile limits, HQ mix/final render, one final visual encode, QC without failures, source provenance protection, and C11 targeted regeneration isolation. Broader perceptual/multi-sample acceptance remains C13.

### C12 — Intelligence convergence
**QA PASS.**

Candidate Intelligence now routes through canonical application services for:
- source-best-first media download via `backend.services.ytdlp_service`;
- canonical faster-whisper via `backend.services.whisper_service`;
- canonical BYOK/local AI routing via `backend.core.ai_provider`.

The old `src/radar/intel/media.py`, `transcribe.py`, and `ai_analyze.py` are compatibility adapters only. Their duplicate platform downloader, standalone faster-whisper model owner, and direct OpenAI-compatible HTTP client have been removed.

The Radar CLI/report/cache contract remains intact during this transition. Local QA at `8e15807` passed focused Candidate Intelligence, legacy Intel, Analyzer, AI provider, Radar enrichment, cache reuse, real canonical ASR, real Gemma/LM Studio AI, failure isolation, and canonical ownership checks. DB convergence remains Phase G.

### C13 — Final real-media acceptance
**ACTIVE — translation quality hardening before broader sample campaign.**

Final acceptance = same regression sample + representative real supported videos supplied by the user, including fast speech, music-heavy, multi-speaker, longer-form, and normal product/review material. C13 is an acceptance campaign, not a new media engine: fix only evidence-backed defects and do not relax quality thresholds to obtain a pass.

Historical regression is technically healthy (timing/media/resume), but user perceptual review rejected the Vietnamese translation quality. C13 therefore pauses broader samples until C6 translation quality is revalidated. Hardening now includes fidelity-first default prompting, independent C6 translation vs C7 rewrite model routing, editable Localization Profiles in Settings, and an optional pre-dubbing translation review gate.

See:
- `docs/PHASE_C.md`
- `docs/PHASE_C_C0_DONOR_AUDIT.md`
- `docs/PHASE_C_DONOR_CAPABILITY_MATRIX.md`
- `docs/QA_PHASE_C.md`
