# Phase C0 — Donor-First Source Audit and Implementation Policy

Date: 2026-09-23

Status: **REALIGNED AFTER C5–C8 REAL-MEDIA QA**

## Why this document changed

Phase C originally researched mature open-source localization projects so Affiliate2 would not reinvent video localization. During C6–C8 implementation that rule drifted into "reimplement the minimum behavior locally". Real-media QA then exposed a systematic dubbing failure: 15 of 21 translated segments overflowed because raw ASR segments were treated as one-to-one TTS units.

That failure is now treated as evidence that the implementation policy was wrong, not merely that one threshold needs tuning.

The Phase-C policy is therefore:

> **Reuse first. Wrap second. Adapt third. Build only what donors do not already solve or what is Affiliate2-specific.**

Do not optimize for minimum code or minimum dependency count at the expense of a behavior that a mature donor already implements and tests.

## Evidence standard

A donor is considered stronger when it has several of:

1. concrete source implementation, not README-only claims;
2. regression/integration tests for the behavior;
3. active development and releases;
4. meaningful adoption/community usage;
5. credible real workflow evidence.

Stars are a popularity signal only, not proof of quality.

## Donor snapshot

| Project | Current signal | License | Strongest Phase-C role |
|---|---:|---|---|
| VideoLingo | ~18.5k stars, active, one-click translation/dubbing | Apache-2.0 | semantic segmentation, terminology/context translation, display-vs-audio units |
| SmartSub | ~5.3k stars, active desktop releases, full ASR→translate→dub workflow | MIT | word-aware subtitle refinement, provider breadth, measured alignment, review exceptions |
| YouDub-webui | active end-to-end localization app; author states a 1M+ follower / 20k+ video Bilibili creator workflow uses it | Apache-2.0 | production-style stages, Demucs separation, background preservation, resume/cache |
| OpenCreator | ~12.2k stars, active; Smart Dubbing + video translation available | Apache-2.0 | dubbing planner/chunks, actual-duration fitting, task/workspace state |
| pyVideoTrans | ~19.1k stars, mature dedicated translator/dubber | GPL-3.0 | algorithm/reference for timing, diarization, audio/video sync, provider breadth |

YouDub production-scale figures are the author's repository claim and should be treated as such, not as independently audited analytics.

## License policy

- **Apache-2.0 / MIT donors:** port/adapt behavior and tests directly where appropriate; preserve required notices/attribution.
- **GPL-3.0 donor (pyVideoTrans):** algorithm/reference by default. Do not copy implementation into Affiliate2 without an explicit licensing decision.

## Correct donor mapping

### Media / ingest / render
Primary: Affiliate2 existing path.
Reference: YouDub / SmartSub for robust task artifacts and media handling.

### ASR / word timing
Primary: current faster-whisper adapter.
Upgrade/reference: VideoLingo WhisperX and SmartSub multi-engine ASR when word alignment or diarization quality requires it.

### Semantic subtitle formation
Primary donors:
- SmartSub AI segmentation: LLM inserts boundaries while content is validated against source text and real word times.
- VideoLingo NLP/meaning splitting: semantic sentence formation before translation.

Affiliate2 must not equate raw Whisper segments with final subtitle or dubbing units.

### Translation
Primary donors:
- VideoLingo: context, terminology, semantic units, translation alignment, display split + remerged audio text.
- SmartSub: structured provider handling, validation, fallback/retry, local/cloud provider breadth.

Affiliate2-specific additions:
- affiliate/conversational preset;
- custom instructions;
- saved glossary;
- profile/model selection.

### Dubbing planner / timing
Primary donor skeleton: **OpenCreator Smart Dubbing**.

Behaviors to port/adapt:
- PlanItem / Chunk / Report model;
- merge short adjacent cues within gap tolerance;
- max chunk size;
- estimate duration before synthesis;
- bounded text rewrite;
- TTS at **chunk** granularity;
- measure actual chunk audio;
- fit actual duration into chunk window;
- redistribute fitted chunk timing to constituent cue items;
- monotonic timeline validation;
- insert silence explicitly;
- stream-copy video at audio mux.

SmartSub contributes:
- slot/gap accounting;
- provider speed capability;
- overlong/review policy.

pyVideoTrans remains a timing/reference source for harder cases.

### Audio separation / mix
Primary donor: **YouDub**.

HQ clean-dub path:
- separate vocals/background;
- dub against clean background/music;
- mix final target speech with background;
- persist stage artifacts and safely resume.

Fast/fallback path may use source ducking, but **HQ profile must not define ducking-only as the intended final-quality path**.

### Stage persistence / resume / batch
Primary donors:
- YouDub stage runner/cache/artifact recovery;
- OpenCreator task/stage state and shared workspace state.

Affiliate2 keeps its existing Job system and ports the proven stage/artifact semantics into it. Do not import a second application/job system.

### Expert review
Primary donors:
- SmartSub line-level regenerate/review;
- OpenCreator shared automation/workspace state.

Auto, batch and expert remain one pipeline.

## What C1–C3 actually contribute to video quality

### C1 — Media quality foundation
**Direct quality impact.**

It determines the pixels and codecs entering the entire pipeline and the quality of the final encode.

Keep:
- source-best-first download;
- hard quality ceilings;
- actual ffprobe evidence;
- HQ render profile;
- minimum-loss render path.

### C2 — AI/local provider controls
**Indirect but necessary quality impact.**

It does not improve pixels by itself. It allows the localization engine to choose a model that is actually good enough for:
- semantic segmentation;
- terminology/context translation;
- duration-aware rewrite;
- later intelligence/remake tasks.

Keep:
- provider/base URL/model selection;
- model discovery;
- test connection;
- local OpenAI-compatible support;
- credential safety.

### C3 — Localization Profiles
**Indirect but necessary quality/reproducibility impact.**

A profile does not make a video better by itself. It makes the chosen quality policy reproducible across one video, batches and reruns.

Keep:
- source quality;
- ASR engine/model;
- translation model/prompt/glossary;
- TTS provider/voice;
- segmentation/dubbing policy;
- audio mode (fast duck / HQ clean dub);
- render/QC policy.

Profiles should store policy, not replace the donor-derived engines.

## Revised Phase-C implementation policy

For each capability:

1. inspect Affiliate2 existing behavior;
2. inspect donor source + tests;
3. if donor behavior is stronger and license-compatible, port/adapt it;
4. preserve donor regression behavior in Affiliate2 tests;
5. change only for Affiliate2-specific requirements or measured quality evidence;
6. real-media QA before building automation on top.

No Ponytail/YAGNI gate is used in Phase C.

## 2026-09-23 donor fidelity re-audit

The C7 implementation is a **hybrid adaptation**, not a verbatim clone of one
donor engine. Future reviews must compare invariants, not assume that a donor
name in a comment means the donor's failure semantics were preserved.

Current donor snapshots reviewed:

- OpenCreator `master` at `422d9db`
- VideoLingo `main` at `11048cc`
- SmartSub `main` at `6120a4d`
- YouDub-webui current `main`

### OpenCreator invariants actually worth preserving

OpenCreator's current Smart Dubbing runtime:

- makes the LLM optimizer optional;
- ignores optimizer failure and keeps the original spoken text;
- synthesizes one natural-rate raw file per planned chunk;
- measures the generated chunk duration before fitting;
- retries TTS without treating a stale output file as success;
- writes plan/report artifacts after fitting.

Important divergence: OpenCreator currently treats `SpeedMax` as a warning
threshold and can apply a larger measured speed factor. Affiliate2 intentionally
does **not** copy that quality policy: it keeps a hard quality ceiling and uses
bounded rewrite/retime/failure handling instead.

Therefore Affiliate2's post-measure rewrite loop, hard speed ceiling and bounded
video retime are Affiliate2 integration logic and need their own tests.

### SmartSub invariants needed for safe resume/regeneration

SmartSub 3.x does not reuse speech audio just because a WAV exists. It computes a
synthesis identity from inputs such as engine/rules, voice, text, language and
speaker settings. A changed identity marks the artifact stale.

It also synthesizes into a new attempt artifact and only makes that artifact
authoritative after synthesis/alignment succeeds. A failed regeneration keeps the
previous good artifact available.

Affiliate2 now mirrors these invariants for C7 raw chunks:

- raw chunk reuse requires a synthesis-identity sidecar;
- stale/missing identity forces regeneration;
- TTS regeneration writes a pending WAV then atomically replaces the canonical
  chunk only after generation and duration probing succeed;
- normal C7 resume may reuse validated completed chunks;
- an overlong reused chunk may still enter rewrite/regeneration rather than
  becoming permanently locked by cache state.

### VideoLingo / YouDub invariants

VideoLingo treats duration estimation as advisory and has fallback behavior when
AI shortening fails; measured audio/timeline logic remains authoritative.

YouDub uses explicit downstream stage-artifact invalidation and publishes Demucs
outputs through pending files before replacing canonical artifacts. Affiliate2's
stage manifest should preserve the same principle: a stage is complete only when
all artifacts required by downstream/expert paths exist.

### Affiliate2-specific glue that donors do not validate for us

These behaviors are not covered simply by saying "ported from donor" and require
Affiliate2 regression + real-media QA:

- multi-item JSON duration rewrite with stable segment IDs;
- local OpenAI-compatible timeout/degradation semantics;
- hard speaker/voice chunk boundaries;
- hard speech-speed ceiling;
- bounded whole-video retime;
- persistence of duration-rewritten translation text;
- targeted chunk regeneration across the shared C6/C7/C11 state;
- synthesis identity compatibility across resume;
- QC promotion of degraded rewrite behavior to NEEDS_REVIEW.

This distinction explains the earlier timeout bug: OpenCreator's planner swallows
optimizer failure by design, while the first Affiliate2 adaptation called the
shared AI client directly and allowed its timeout exception to escape the C7
stage.

## Current rework decision

- C1–C3: **KEEP**
- C4: **EXTEND** to distinguish source words / semantic cues / display cues / dubbing chunks
- C5: **KEEP + later alignment upgrade if real evidence requires**
- C6 prototype: **SUPERSEDED; replace with VideoLingo/SmartSub-derived segmentation + translation**
- C7 prototype: **SUPERSEDED; replace with OpenCreator-derived planner/chunk/fit engine**
- C8 prototype: **SUPERSEDED as HQ path; retain ducking only as fast/fallback, add YouDub-style clean-dub separation**
- C9–C11: **DO NOT IMPLEMENT until reworked C6–C8 passes real-media QA**
- C12–C13: unchanged conceptually

## Required rework checkpoint

Use the same failing real sample first.

Before:
- 21 usable translated source segments
- 15 timing overflows
- only a small fraction of the timeline received target-language dub

After donor-derived rework, record:
- semantic/display cue count;
- dubbing chunk count;
- actual chunk durations;
- required speed factors;
- over-max chunk count;
- target speech coverage;
- source vocal bleed;
- final media evidence.

Only then ask for additional real Douyin/Bilibili samples.
