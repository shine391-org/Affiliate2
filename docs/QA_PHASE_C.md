# QA Phase C — Media, AI & Automated HQ Localization

## Purpose

Phase C changes quality-critical shared media/AI contracts. QA must validate both correctness and product output quality.

A playable MP4, successful HTTP response or green unit suite is not sufficient by itself.

Ponytail/YAGNI may reduce architecture, dependencies and code paths, but may not reduce this quality bar. If a simple baseline repeatedly fails representative real-media QA, the next proven capability (for example forced alignment, a refinement pass, bounded video retiming, source separation or a better TTS provider) must be implemented rather than waived as "too complex".

## Test policy

Use the repository tiered-test policy.

### Tier 1 — inner loop

Run only directly affected tests plus import/build/smoke for the changed capability.

Examples:

- downloader format/quality tests
- provider settings tests
- ASR quality-routing tests
- translation segment tests
- timing/TTS block tests
- render command tests
- relevant frontend component tests/build

### Tier 2 — Phase-C module gates

Run focused integration coverage across:

- media ingestion
- AI provider/model settings
- profile persistence
- transcript/timeline
- ASR
- translation
- dubbing/timing
- mixing
- captions/render
- QC/job progression
- expert targeted rerun
- batch orchestration

Include frontend production build and stable local runtime smoke.

### Tier 3 — pre-merge milestone

Run the broad/full suite before Phase-C merge because shared media/AI infrastructure is cross-cutting.

## C1–C4 implementation handoff

C1–C4 are implemented but are **not QA PASS** until the independent QA agent runs them.

Focused starting point from the `app/` directory:

```powershell
python -m pytest -q tests/test_download_options.py tests/test_phase_c_media_quality.py tests/test_phase_c_render_quality.py tests/test_ai_provider.py tests/test_analyzer.py tests/test_phase_c_localization_profiles.py tests/test_phase_c_localization_timeline.py
npm --prefix frontend run build
cd ..
python -m pytest -q tests/test_intel.py
```

Also smoke the Settings API/UI for:

- switching cloud providers requires the new provider's key
- switching cloud → Ollama/LM Studio/vLLM does not send/reuse the old cloud secret
- compatible/local model discovery and connection-test failure/success states
- localization profile create/update/default/delete
- upgraded SQLite database receives the two additive JSON columns
- a real download with default quality records actual source media metadata
- retained legacy Bilibili intelligence fetch selects the highest-bandwidth usable DASH stream while C12 parity is pending
- creator/auto subtitle transcript rows still run intelligence without retranscribing

Do not infer runtime PASS from the implementation commits alone.

## C5–C8 local quality checkpoint

C5–C8 are implemented but are **not QA PASS** until this checkpoint runs on the local Windows machine.

Do not start C9 if this checkpoint finds a quality blocker that requires changing the ASR / translation / dubbing / mix contracts.

### Focused automated gate

From `app/`:

```powershell
python -m pytest -q `
  tests/test_whisper_service.py `
  tests/test_analyzer.py `
  tests/test_translate_batch_resilience.py `
  tests/test_phase_c_tts_timing.py `
  tests/test_phase_c_render_quality.py `
  tests/test_phase_c_localization_profiles.py `
  tests/test_phase_c_localization_timeline.py `
  tests/test_ai_provider.py

npm --prefix frontend run build
```

Then run any directly affected regression suites the QA agent deems necessary.

### Runtime / real-media checkpoint

Use real supported media, not synthetic-only fixtures. Minimum useful set for this checkpoint:

1. vertical Douyin talking-head or product/demo short
2. fast-speech short
3. speech over music/ambient-heavy source
4. longer or multi-speaker/complex Bilibili sample; preferably include a 16:9 → 9:16 final localization render

For each sample record:

- source URL/content identity
- source width/height/fps/codec/bitrate/duration
- actual ASR quality/model loaded
- transcript and word-timestamp quality
- translation provider/model/profile
- translation naturalness and terminology
- TTS provider/voice
- per-block measured TTS duration
- adaptation/regeneration events
- actual speech-rate adjustments
- unresolved timing overflow count
- source/dub/background gain policy
- final width/height/fps/codec/bitrate/duration
- visible quality and caption sync

### Mandatory quality-escalation decisions

This checkpoint exists to decide whether benchmark-gated complexity is now required.

- **WhisperX / forced alignment / diarization** becomes required in Phase C if faster-whisper word timing materially fails representative fast-speech or multi-speaker alignment.
- **Better TTS provider / voice path** becomes required if the selected Vietnamese voice is not publishable in perceptual review.
- **Source separation (Demucs or the smallest proven alternative)** becomes required if duck/mix leaves unacceptable source-speech bleed or damages useful background/music.
- **Bounded video retiming** becomes required if timing overflow or unnatural speech speed remains systematic after duration-aware translation, bounded rewrite, safe TTS speed and configured silence tolerance.

Ponytail/YAGNI may choose the smallest implementation that passes, but it may not be used to accept perceptibly bad output.

### Render-loss check

For full-dub localization:

- dub/background mix must stream-copy the video stream
- reframe + translated captions should share one final lossy video encode
- HQ profile should use the configured HQ render settings
- no hidden extra video transcode should be inserted merely because audio/timing stages ran

### Gate result

Report each as:

```
C5 ASR: PASS / FAIL / BLOCKED
C6 Translation: PASS / FAIL / BLOCKED
C7 Timed dubbing: PASS / FAIL / BLOCKED
C8 Mix/final render: PASS / FAIL / BLOCKED

QUALITY ESCALATION:
WhisperX required: YES/NO
Better TTS required: YES/NO
Source separation required: YES/NO
Video retiming required: YES/NO

READY FOR C9: YES/NO
```

`READY FOR C9: YES` requires no unresolved quality/architecture blocker in C5–C8.

## Required automated assertions

### Media/source quality

- best/default selection no longer prefers an available lower-quality source solely because it is <=720p
- requested quality policy is respected
- actual downloaded media metadata is persisted
- specialized resolver is used only under documented conditions
- final render reports actual media properties
- no unintended repeated encoding path is introduced

### AI provider/local AI

- base URL persists through API/settings/UI
- model ID is manually editable
- compatible/local provider works without static-registry membership
- model discovery works where supported
- connection test returns explicit success/failure
- secrets are not leaked back to UI/logs

### Profiles

- saved profile round-trips
- job snapshots effective profile/config so later profile edits do not silently change an already-running job
- batch jobs consume the intended profile
- explicit job/segment override precedence is deterministic

### Transcript/timeline

- source and target coordinates use distinct fields
- no code path writes output timing into source timing
- segment state can survive persistence/reload
- downstream consumers reuse the canonical transcript rather than retranscribing unnecessarily

### ASR

- configured quality maps to the intended actual model
- failure/downgrade is explicit
- timestamps remain monotonic
- current faster-whisper word timestamps are evaluated on real speech/timing samples
- if those timestamps materially fail alignment or multi-speaker acceptance, the Phase-C implementation adds the smallest proven forced-alignment/diarization capability
- no duplicate pass is made when an existing canonical transcript is valid

### Translation

- context/preset/glossary reach the translation request
- segment order/identity is preserved
- malformed provider responses fail explicitly
- target-duration context is available to the translation/adaptation path
- if the initial contextual pass repeatedly fails semantic/naturalness/timing-fit review, a bounded refinement/adaptation pass is added rather than accepting systematic low quality

### Dubbing/timing

- TTS is generated per block/semantic unit rather than one joined whole-video text
- actual TTS duration is measured
- timing resolution follows configured policy
- safe rate limits are enforced
- unresolved overflow becomes NEEDS_REVIEW rather than silent distortion/truncation
- if overflow/unnatural rate is systematic across the representative acceptance set, bounded video retiming or another proven fitting step is implemented instead of classifying the whole problem away as NEEDS_REVIEW
- final audio block placement follows resolved timeline
- captions use resolved final timing

### Audio/mix

- background preservation path works on supported media
- gain/mix policy is deterministic
- missing separation output is explicit and handled according to documented policy
- speech+music/ambient samples are listened to for source-speech bleed and background damage
- if the simple mix path cannot preserve acceptable ambience while suppressing/replacing source speech, source separation becomes a Phase-C requirement
- no accidental total replacement/truncation of desired source ambience

### Render

- final resolution/aspect matches the selected profile
- final duration is plausible relative to the resolved timeline
- video/audio streams are valid
- caption/render stages do not silently add unnecessary lossy intermediate encoding when a single final encode is possible
- when an intermediate is required for correctness/timing, prefer stream-copy or a lossless/high-quality intermediate rather than forcing a brittle one-pass graph

### Automation/batch/QC

- multiple candidate jobs can be queued through the existing job system
- stage progression is persisted
- one failed job does not corrupt unrelated jobs
- READY / NEEDS_REVIEW / FAILED are explicit
- QC reason is actionable
- targeted expert rerun starts from the correct stage and does not redo unaffected expensive stages unless required

## Required real-media acceptance set

Final Phase-C acceptance uses at least 10 real source videos from actual supported discovery/media paths.

The set should include:

- Douyin talking-head short
- Douyin product/demo
- fast speech
- music/background-heavy short
- multiple additional Douyin examples
- Bilibili sample with longer/more complex speech
- Bilibili multi-speaker or similarly complex sample when obtainable

Do not use synthetic-only media as the final gate.

## Evidence captured per sample

Source:

- canonical URL/content ID
- width/height
- fps
- video/audio codec
- bitrate when available
- duration

Processing:

- effective Localization Profile
- ASR model actually used
- translation provider/model actually used
- TTS provider/voice actually used
- number of segments/blocks
- timing exception count
- stage failures/retries

Final:

- output width/height
- fps
- codec
- bitrate when available
- duration
- READY / NEEDS_REVIEW / FAILED
- QC reasons

## Human/perceptual checklist

For each final acceptance sample explicitly inspect:

- source meaning preserved
- Vietnamese sounds natural for the chosen preset
- important terminology/names/products preserved
- obvious ASR omissions/hallucinations absent
- dub starts/stops plausibly against the speaker/content
- no severe speech-rate distortion
- the configured TTS voice is publishable for the target language; a merely functional fallback voice is not enough
- captions match final speech timing
- background/music/ambience remains usable
- no obvious clipping
- no obvious visual degradation beyond the selected render profile
- beginning/end are not truncated

Record failures; do not mark them PASS merely because automated checks are green.

## Phase-C merge gate

Phase C may merge only when:

1. focused automated suites pass
2. frontend production build passes
3. stable local runtime smoke passes
4. cloud/OpenAI-compatible provider path is validated
5. at least one local compatible provider path is validated when the user's environment supports it
6. real-media acceptance set is completed
7. material quality regressions are fixed or explicitly accepted/documented
8. no Auto/Manual duplicate pipeline exists
9. no new second job system/media app exists
10. architecture/status/docs match implemented reality

The final report must state exact commands, counts, real samples inspected, provider/runtime limitations and any untested surface.
