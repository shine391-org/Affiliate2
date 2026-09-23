# Phase C — Media, AI & Automated HQ Localization

## Status

**C1–C3 QA PASS. C4R–C11 IMPLEMENTED. FINAL LOCAL / REAL-MEDIA QA PENDING.**

Branch:
`phase-c-media-ai-hq-localization`

The first real C5–C8 checkpoint exposed a systematic C7 failure (15/21 timing overflows and mostly missing Vietnamese dub). Phase C was therefore rebuilt donor-first instead of continuing the prototype.

## Product goal

Radar candidate
→ best eligible source
→ clean vocal/background artifacts
→ ASR
→ forced word alignment
→ optional speaker diarization
→ semantic source cues
→ contextual translation
→ display cues
→ speaker-aware dubbing chunks
→ natural-rate TTS
→ measured audio duration
→ rewrite / silence borrowing / post-TTS fit
→ bounded video retime only if still needed
→ clean background mix
→ one final visual encode
→ automatic QC
→ review / targeted rerun
→ publish-ready output or explicit exception

Automation is the normal path. Expert mode edits the SAME persisted pipeline state; it is not a second editor engine.

## Donor policy

For quality-critical behavior:
1. reuse;
2. wrap;
3. adapt;
4. build Affiliate2-specific glue only when no donor behavior fits.

Apache/MIT donor behavior may be ported/adapted with attribution.
GPL code remains reference-only unless licensing changes.

Canonical donors:
- VideoLingo: WhisperX alignment, semantic/display/dubbing separation, reference-audio TTS workflow
- SmartSub: word/cue validation, speaker overlap assignment, provider registry / exception behavior
- OpenCreator: Smart Dubbing planner, chunk synthesis, measured timeline fitting, audio assembly
- YouDub: Demucs clean separation and durable stage/cache semantics
- pyVideoTrans: timing/retiming reference only

## C1 — Media Quality Foundation — QA PASS ✅

Direct video-quality controls:
- source-best-first download
- explicit quality caps
- real ffprobe metadata
- Draft / Balanced / HQ encode profiles
- HQ CRF 18
- audio-only stages do not re-encode picture
- reframe + optional retime + captions share one final lossy visual encode

## C2 — AI Provider + Local AI — QA PASS ✅

Provider/model control plane:
- Anthropic
- OpenAI
- OpenRouter
- Ollama
- LM Studio
- vLLM
- custom OpenAI-compatible
- free model IDs
- editable Base URL
- model discovery / connection test
- credential isolation

## C3 — Localization Profiles — QA PASS ✅ / EXTENDED

Profiles persist the quality contract:
- ASR quality/model
- WhisperX alignment mode
- diarization mode / speaker count
- semantic segmentation policy
- translation model/preset/instructions/glossary
- TTS provider/model/default voice
- speaker voice map
- manual or automatic speaker reference audio
- smart-dubbing chunk/speed/rewrite policy
- clean-dub audio policy
- bounded video retime policy
- caption/aspect/render/QC policy

Default profile: `vi-affiliate-hq`.

## C4R — Canonical Localization State — IMPLEMENTED

The pipeline distinguishes:
1. source word timestamps
2. semantic source cues
3. translated/display cues
4. speaker identity
5. expert overrides
6. dubbing PlanItems
7. dubbing Chunks
8. measured raw/fitted TTS artifacts
9. resolved target timeline

One raw Whisper segment is NOT automatically one translation line, caption line or TTS request.

## C5 — HQ ASR + Alignment + Speakers — IMPLEMENTED

Normal transcription:
- faster-whisper
- requested model/quality reaches runtime
- word timestamps retained

HQ alignment:
- WhisperX forced alignment, following VideoLingo's proven pattern
- uses separated vocal audio on full HQ clean-dub jobs

Multi-speaker:
- optional WhisperX/pyannote diarization
- Hugging Face gated-model token via encrypted Settings or .env
- SmartSub-style overlap-duration speaker assignment
- stable `speaker_id` propagated downstream
- dubbing chunks cannot silently cross known speaker boundaries

## C6R — Semantic Segmentation + Translation — IMPLEMENTED

Flow:
source words
→ deterministic word-time normalization
→ split long raw ASR units using real word timestamps
→ merge tiny dependent fragments
→ AI semantic grouping of stable word/cue IDs only
→ exact ID/order/coverage validation
→ deterministic fallback on bad model output
→ contextual translation with full context + glossary + style/profile
→ stable-ID structured validation
→ display cue state

AI segmentation may choose boundaries; it may not rewrite source transcript content.

## C7R — Smart Dubbing / Timing — IMPLEMENTED

OpenCreator-derived core:
- DubbingConfig
- DubbingCue
- PlanItem
- DubbingChunk
- DubbingReport

Behavior:
- speaker/voice changes are hard chunk boundaries
- short adjacent cues can merge
- max chunk size enforced
- following unused silence may be borrowed safely
- ONE natural-rate TTS request per dubbing chunk
- real generated duration is authoritative
- bounded AI rewrite/regeneration for overlong speech
- AI rewrite is best-effort: local/provider timeout or malformed rewrite records a review warning and continues through measured TTS / atempo / bounded retime instead of killing the whole dubbing stage
- local OpenAI-compatible endpoints get a longer request timeout than cloud endpoints
- post-TTS FFmpeg `atempo`
- fitted time redistributed to constituent display cues
- monotonic target timeline
- explicit gaps/silence
- acceptable/max speed thresholds
- no silent dropped Vietnamese blocks

Last-resort timing:
- bounded video slowdown can enlarge timing windows only after semantic/chunk/rewrite/atempo strategies are insufficient
- configured quality ceiling defaults to 1.12x slowdown
- slowdown is applied in the same final visual encode, not another H.264 generation

Expert overrides:
- translation
- target start/end
- segment voice
- speaker identity

Targeted chunk regeneration can reuse raw audio for all unaffected chunks.

## C8R — HQ Clean Dub / TTS / Final Media — IMPLEMENTED

### Audio separation
YouDub-derived:
- Demucs `htdemucs_ft`
- long-file chunking
- overlap/crossfade
- peak protection
- atomic completed artifacts
- RF64-safe intermediates

HQ ASR can consume the vocal stem.

### TTS provider layer
Implemented:
- Edge TTS
- OpenAI TTS
- OpenAI-compatible TTS
- Azure Speech
- ElevenLabs
- GPT-SoVITS (donor-compatible Chinese/English clone path only)

Edge remains useful as preview/fallback; the pipeline is no longer architecturally tied to it.
For Vietnamese publish-quality dubbing, use a provider that actually supports Vietnamese
(e.g. Azure, ElevenLabs, or a verified OpenAI-compatible TTS backend).

### Voice cloning/reference audio
GPT-SoVITS supports the donor-compatible Chinese/English path:
- manual global reference
- manual speaker-specific reference
- automatic longest suitable source reference
- automatic per-speaker reference extraction
- source prompt text/language propagation

On clean-dub jobs automatic references are extracted from the vocal input rather than requiring the user to pre-cut WAV files.

### Mix/render
- target dub mixes against clean background/music
- final limiter protects peaks
- fast source-duck mode remains explicit preview/fallback
- audio work never encodes picture
- external mixed audio + optional setpts retime + reframe + ASS captions = ONE final H.264 encode

## C9 — Durable Automation + Batch — IMPLEMENTED

No second queue system.

Every localization job gets:
`storage/localization_jobs/<job_id>/manifest.json`

Current manifest contract: **version 2**. Older manifests are rejected instead
of silently resuming artifacts produced by an incompatible pipeline contract.

Persisted stages:
1. separation
2. ASR/alignment
3. semantic
4. translation
5. dubbing
6. audio mix
7. final render
8. QC

Capabilities:
- persisted effective profile
- persisted source copy
- atomic JSON/artifact publication
- stage completion verification
- resume completed expensive stages
- invalidate/rerun downstream only
- targeted chunk regeneration
- duration-aware C7 spoken rewrites persist back to C6 translated text so reused raw chunks cannot drift from captions
- failed-stage evidence
- batch endpoint up to 20 videos
- Localization Batch UI using the same canonical runner

## C10 — Automatic QC + Exception Queue — IMPLEMENTED

QC combines:
- media/ffprobe evidence
- translated coverage
- target speech coverage
- timing issues
- duration/aspect checks
- clipping/peak evidence

Statuses:
- READY
- NEEDS_REVIEW
- FAILED

QC does not pretend a rendered MP4 is publishable merely because FFmpeg exited 0.

Exception API + Localization Review UI show jobs requiring attention.

## C11 — Expert Override — IMPLEMENTED

The Expert workspace is an escape hatch over the SAME artifacts.

It supports:
- inspect final video
- inspect stage states
- inspect QC
- inspect semantic/translated segments
- inspect dubbing chunks
- edit translated speech
- override target timing
- override segment voice
- regenerate one chunk
- rerun translation/dubbing/final-render/QC downstream

It does not become a CapCut clone.

## C12 — Intelligence Convergence — AFTER ACCEPTANCE GATE

Legacy `src/radar/intel/*` still owns a transitional candidate-intelligence downloader/transcriber.

It is outside the localized-video render path.

Do NOT remove it before C4–C11 real-media parity is proven. After acceptance:
- route intelligence to canonical media/transcript/provider paths where practical;
- remove duplicate download/transcribe paths only after focused parity tests.

This cleanup should not require another human perceptual-video round.

## C13 — Final Real-Media Acceptance

One final QA campaign after C4–C11 implementation.

First regression:
`E:\Affiliate ver 1\storage\videos\qa\sample.mp4`

Old evidence:
- ~65.7s
- 21 timed units
- 15 overflows
- most Vietnamese dub missing

Then representative supported media supplied by the user:
- fast speech
- music-heavy
- multi-speaker
- longer-form
- normal product/review short

Agent must ASK the user for URLs only when additional real samples are required.

The user is the final perceptual judge of:
- Vietnamese naturalness/meaning
- TTS publishability
- timing
- source-language bleed
- caption sync
- background/music quality
- visible quality loss
- final publishability

## Definition of done

Phase C output engine is accepted only when:
- automated tests/build pass;
- old regression is fixed;
- HQ clean dub runs end-to-end;
- WhisperX path works;
- multi-speaker path works on a suitable sample;
- stage resume/rerun works;
- QC catches real exceptions;
- Expert targeted rerun works;
- user accepts representative final outputs.

After that, C12 can converge legacy intelligence plumbing without reopening the video-quality architecture.
