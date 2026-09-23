# Phase C — Media, AI & Automated HQ Localization

## Status

**STARTED — DONOR-DERIVED C4R/C6R/C7R/C8R IMPLEMENTED / QA PENDING**

Branch:
`phase-c-media-ai-hq-localization`

C1–C4 passed local QA previously. C5–C8 then reached real-media QA, where the first real sample exposed a systematic C7 failure: 15/21 timed speech units overflowed and most Vietnamese dubbing was absent.

That failure froze C9+ and triggered the donor-first realignment documented in:
`docs/PHASE_C_C0_DONOR_AUDIT.md`.

## Product goal

Radar candidate
→ best source
→ canonical ASR/word timing
→ semantic source cues
→ contextual translation
→ display-caption cues
→ dubbing chunks
→ measured TTS
→ fitted target timeline
→ clean background mix
→ minimum-loss final render
→ QC
→ review-ready output

The happy path must not require manual transcript, translation, timing or FFmpeg work.

## Phase map after donor review

### C0R — Donor realignment — COMPLETE ✅

Policy:
- reuse/wrap/adapt mature donor behavior;
- build only Affiliate2-specific glue/domain behavior;
- Apache/MIT donor code/tests may be ported with proper notice;
- GPL donor remains reference unless licensing is explicitly approved.

Canonical donors:
- VideoLingo: semantic segmentation + translation structure + display/audio split
- SmartSub: word-aware segmentation validation + alignment/review + provider breadth
- OpenCreator: dubbing planner/chunks/fit/audio
- YouDub: clean audio separation/mix + stage/cache/resume
- pyVideoTrans: GPL timing/diarization/reference only

### C1 — Media quality foundation — QA PASS ✅

**Direct video-quality value.**

Keep:
- source-best-first downloader;
- explicit quality caps;
- actual ffprobe source/final evidence;
- Draft/Balanced/HQ render profiles;
- minimum-loss final visual encode.

### C2 — AI provider + local AI — QA PASS ✅

**Indirect quality value: model freedom.**

Keep:
- OpenAI / Anthropic / OpenRouter;
- Ollama / LM Studio / vLLM / custom compatible;
- Base URL;
- free-form model ID;
- discovery/test connection;
- credential isolation.

C2 is control-plane plumbing, not the translation engine itself.

### C3 — Localization Profiles — QA PASS ✅

**Indirect quality/reproducibility value.**

Keep profile persistence and validation.

Revise profile fields during C4R/C8R to include explicit donor-derived policy such as:
- segmentation policy;
- dubbing planner policy;
- clean_dub vs fast_duck audio mode;
- ASR/translation/TTS/render/QC choices.

### C4R — Canonical localization representations — IMPLEMENTED / QA PENDING

Do not treat one `LocalizationSegment` granularity as every downstream unit.

Required conceptual representations:

1. **Source words** — word text + source timestamps
2. **Semantic source cues** — language/meaning units used for translation
3. **Display cues** — subtitle-readable units
4. **Dubbing chunks** — longer audio synthesis/fitting units
5. **Dubbing plan items** — mapping from source/display items to fitted target timing

These do not require separate databases. They may be job artifacts/data models.

### C5 — ASR convergence — KEEP / QA RECHECK WITH REWORK

Current fixes remain valuable:
- requested quality/model reaches actual faster-whisper model;
- Analyzer does not hardcode balanced;
- word timestamps preserved.

Do not add WhisperX automatically. Re-evaluate on semantic segmentation/multi-speaker samples after donor-derived C6/C7 is in place.

### C6R — Semantic segmentation + translation — IMPLEMENTED / QA PENDING

Port/adapt VideoLingo + SmartSub behavior.

Implemented flow:

source words / ASR cues
→ deterministic word-time normalization
→ split overly long source cues from real word timestamps
→ merge tiny dependent cues
→ optional AI grouping of stable cue IDs only
→ validate every cue ID exactly once and in order
→ fallback to deterministic cues on invalid AI response
→ contextual translation with glossary/preset/custom instructions
→ stable-ID translation validation
→ display cues remain independent from dubbing chunks

Important:
- raw Whisper segments are not canonical translation/dubbing units;
- display subtitle granularity may differ from audio dubbing granularity;
- strict QC forbids silent source-language passthrough.

### C7R — Donor-derived dubbing planner/timing — IMPLEMENTED / QA PENDING

Port/adapt OpenCreator Smart Dubbing as the main skeleton.

Implemented donor-derived core data:
- DubbingConfig
- DubbingCue
- PlanItem
- DubbingChunk
- DubbingReport

Implemented behavior:
- clean spoken text;
- estimate target speech duration;
- rewrite overlong text before synthesis when configured;
- merge short adjacent cues inside gap tolerance;
- cap chunk size;
- TTS **one natural-rate audio file per chunk**, not one file per raw Whisper segment;
- measure actual chunk audio duration;
- bounded structured rewrite/regeneration when required speed exceeds max;
- fit generated audio AFTER synthesis using FFmpeg atempo rather than trusting provider speed controls;
- distribute fitted chunk duration back to constituent caption/plan items;
- validate monotonic target timing;
- compose explicit silence/timeline gaps;
- emit warnings above acceptable speed and explicit failure above max speed.

Use SmartSub's overlong/review policy rather than blindly applying extreme speed.

The original C7 implementation that produced 15/21 overflows is a failed prototype and is not the canonical timing engine.

### C8R — HQ Clean Dub audio pipeline — IMPLEMENTED / QA PENDING

Two explicit modes:

**Fast / Preview**
- source ducking + target dub;
- useful for quick output/fallback.

**HQ Clean Dub**
- Demucs `htdemucs_ft`, using YouDub's chunk/overlap/crossfade/atomic-artifact pattern;
- ASR runs on the separated vocals stem for full-dub HQ jobs;
- synthesize/fitted target dub;
- mix target dub with clean background/music rather than ducking source speech;
- global peak protection + final limiter;
- RF64-compatible long-audio intermediates;
- stream-copy video during audio-only stages;
- perform one final visual encode for reframe/captions.

Default `vi-affiliate-hq` profile now targets `hq_clean`. `fast_duck` remains an explicit preview/fallback mode.

### C9 — Automation + batch — BLOCKED UNTIL C6R–C8R QA PASS

Reuse existing Affiliate2 jobs, but port YouDub/OpenCreator stage semantics:
- persisted stage state;
- artifact paths;
- resume after completed stages;
- rerun from invalidated stage;
- no second queue/app.

### C10 — Automatic QC + exception review — BLOCKED UNTIL C9

Port/adapt:
- SmartSub overlong/alignment review reasons;
- OpenCreator Dubbing Report;
- Affiliate2 media/QC evidence.

Output:
READY / NEEDS_REVIEW / FAILED.

### C11 — Expert override — BLOCKED UNTIL C10

Use the same job/artifacts as automation.

Allow targeted:
- translation edit/rerun;
- TTS/chunk regenerate;
- voice/model override;
- timing exception handling;
- final render rerun.

No CapCut clone.

### C12 — Intelligence convergence

Reuse canonical media, transcript and provider paths for Radar/intelligence.

Remove transitional duplicate paths only after parity.

### C13 — Real-media final gate

At least 10 real supported Douyin/Bilibili candidates.

Must inspect:
- ASR;
- semantic segmentation;
- Vietnamese translation;
- dubbing coverage;
- required speed factors;
- caption sync;
- clean background;
- TTS naturalness;
- visible render quality;
- beginning/end truncation.

## Immediate gate

C0R/C4R/C6R/C7R/C8R are now implemented.

Next:
1. sync local branch and install the updated app requirements (Demucs added);
2. run focused donor-regression suites + frontend build;
3. rerun the **same 65.7s failing sample**;
4. capture semantic cue count, dubbing chunk count, rewrite count, max required speed, timing issues, clean-dub artifacts and final ffprobe;
5. user performs perceptual QA;
6. only after PASS may C9 start.

## Definition of done

Phase C is done only when 10 real candidates can go from Radar → localized Vietnamese output automatically, with review-ready video or explicit actionable exceptions and without routine manual editing.
