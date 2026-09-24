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
- UX1–UX5 convergence is QA-passed: workflow sidebar and Discover parent; canonical Studio shell with Localize / Manual Edit plus truthful Phase-D/E reservations; one six-step Localize workflow with contextual Batch; and top-level Review using a generic exception envelope.
- UX6–UX7 are QA-passed (403aa50 + browser QA on current code): Utilities no longer owns Localize/Batch/Review workflows; Settings is grouped by connection/profile/service ownership; Localization Profiles expose discovered/custom C6/C7 models and grouped production defaults with all 26 profile fields persisting/reloading; Studio opens one contextual Expert drawer (single LocalizationExpertPanel instance, 620px desktop / full-width 375px) rather than rendering the full Expert workspace in multiple steps.
- UX8–UX9 are implemented / local-QA pending: Studio now exposes one normalized persisted source/transcript/timeline contract for the reserved Phase-D/E modes; Library has reserved `smart_cut` / `creative_remake` derivative identities and source handoffs; Review has a workflow-source registry plus Smart Cut / Creative Remake contracts with Storyboard/Visual categories; Stock and Motion are contextual Remake asset providers; no Smart Cut/Remake engine or new top-level app was introduced.
- Legacy Translate, Batch, Review, and Clip Studio URLs preserve source/job query context while redirecting to the canonical Studio or Review owner.
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

### Post-C12 source ownership cleanup
- Legacy Radar Douyin scanning now adapts the canonical app-native CDP provider.
- Retired donor runtime/evidence files were removed from the working tree.
- Download/discovery browser-session ownership is now separated from TikTok upload-only legacy state.
- Stale downloader regressions were aligned with the current browser-order and quality-cap contracts.

### C13 — Final real-media acceptance
**ACTIVE — translation quality hardening + real-media defect triage.**

Real-media regression campaign in progress against fast-speech, music-heavy,
multi-speaker, and longer-form samples. The 2026-09-23 run reproduced the
perceptual rejection and confirmed the structural defects below; each is
tracked as a discrete, evidence-backed defect rather than a quality downgrade.

Known defects (with evidence):
- **C13-TT-01** Fast-speech timing warnings (`high_speech_speed`, 2 chunks for
  `qa-c13-regression-20260924-0610`) — accepted as reviewable via the Review
  queue instead of silently clamped.
- **C13-AI-02** Edge TTS preview tier flagged (`preview_tts_provider`) —
  surfaced in Review with human-quality approval gate.
- **C13-TR-03** Vietnamese translation fidelity regression on fast product
  review dialogue — under revalidation via independent C6 translation vs C7
  rewrite routing.

The Review workspace adapts these QC items into a generic envelope with
Translation/Timing/Audio/QC filters so each defect is independently inspectable
and rerunnable through the Expert panel.

See:
- `docs/PHASE_C.md`
- `docs/PHASE_C_C0_DONOR_AUDIT.md`
- `docs/PHASE_C_DONOR_CAPABILITY_MATRIX.md`
- `docs/QA_PHASE_C.md`
