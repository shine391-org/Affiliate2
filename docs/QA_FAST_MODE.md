# FAST MODE — implementation and QA evidence

Date: 2026-10-02. Branch: `feat/localization-fast-mode`.
Base: `1ae60c99bdb41a06e5c244e683b609af11cffd54`.

**Overall acceptance: BLOCKED, not COMPLETE.** Implementation and focused gates
are exercised, but the required intact 5–10 minute media/listening matrix and a
successful, warmed HQ/Fast >=50% benchmark are not available. No merge or push.

## Change scope

- Existing profile API offers an unsaved canonical Fast preset. Creating it uses
  existing CRUD, generates a new ID and preserves saved C6/C7 routes/prompts.
- Fast pins Edge/HoaiMy, dry Vietnamese audio, explicit local `small` by default,
  alignment/diarization/semantic AI off, one dubbing rewrite attempt and balanced
  render. Existing speed ceilings (1.15 / 1.30 / emergency 1.35), planner and strict
  QC remain unchanged. The UI warns about lost background or source-voice bleed.
- One Groq adapter uses the encrypted saved compatible connection, the
  transcription endpoint, mono 16 kHz FLAC, word/segment timings, finite retries,
  Retry-After, 25,000,000-byte upload guard, actual audio-duration bounds and cleanup.
- Decoder errors, including FFmpeg's exit-0 `partial file`, are terminal, not a
  successful prefix transcription. `-xerror` also rejects corrupt input packets.
- Only opted-in transient failures fall back to the explicitly selected local
  model. Credential/config/output failures do not. Remote wait holds no GPU gate;
  local transcription/alignment/separation use the existing gate.
- Job-flag cancellation cancels the remote task and prevents ASR publication.
- Snapshot/rebind includes ASR identity. Audio-mode changes invalidate separation;
  gains/preservation only invalidate mixing. Upstream override clears stale targeted
  chunk retry IDs. UI and delivered job output expose actual ASR/fallback metadata.

## Automated/local integration gates

Focused backend: **332 passed** (43.85 seconds). No full suite was run.

```bash
# Windows Git Bash; keep this TEST directory distinct from the live-QA DB.
VIRALMINT_DATA_DIR='C:/Users/shine/AppData/Local/hermes/profiles/affiliate/cache/scratch/fast-mode-tests' \
E:/Affiliate2/.venv/Scripts/python.exe -m pytest \
  app/tests/test_fast_mode_profiles.py app/tests/test_fast_mode_routing.py \
  app/tests/test_groq_asr_service.py app/tests/test_phase_c_provider_binding.py \
  app/tests/test_phase_c_localization_api.py app/tests/test_phase_c_localization_profiles.py \
  app/tests/test_localization_profile_ui_contract.py app/tests/test_whisper_service.py \
  app/tests/test_phase_c_semantic_segmentation.py app/tests/test_phase_c_tts_timing.py \
  app/tests/test_translate_batch_resilience.py --rootdir=app -o pythonpath=app -q
```

Frontend: `node --test src/providerBinding.test.js src/studioProviderBinding.test.js`
**21 passed**; `npm run build` passed (existing large-chunk warning).

**LOCAL VERIFIED:** real FFmpeg + local HTTP multipart wire checks, response
normalization, retry/Retry-After, credential failures, cancellation cleanup, malformed
and corrupt input rejection. Local/fallback GPU routing tests use doubles and are
**MOCK VERIFIED**, not a live forced Groq outage or live local fallback run.

Real Edge against isolated backend `127.0.0.1:16901`: create/save/read Fast;
old profile/default and C6/C7 routes preserved; select saved Groq connection without
copying its chat model; reload persists turbo model and connection; no page errors.
Only the compatible Groq connection appeared in the ASR selector.

## Real execution, not mock transcription

Production settings were read-only. Jobs, profiles, generated media and API/browser
mutations were isolated under the active Hermes scratch `fast-mode-live` directory.
Production table counts were unchanged at the safety check. Test fixtures must not
share this live-QA DB; they can reset its settings. During final QA the isolated
settings were reseeded from read-only production and UI checks rerun successfully.

Source: `storage/localization_jobs/3f4b108b-a810-41df-8341-f0ec9b79057c/source.mp4`
(70.423991 seconds, intact). Existing saved translation/rewrite routes were used,
not invented endpoints/models. Fast ASR: Groq/turbo; TTS: Edge/HoaiMy.

**LIVE VERIFIED:** final adapter revision transcribed this source in **2.351 s**,
43 segments / 216 nonblank words, with no remaining Groq temp files.

Canonical full Fast runner, fresh artifacts:

| Stage | Seconds |
| --- | ---: |
| Separation | skipped |
| ASR | 2.289 |
| Rule semantic normalization | 0.008 |
| Translation record (includes persisted dubbing rewrites) | 223.619 |
| Dubbing | 119.076 |
| Audio mix | 0.641 |
| Final render/publication record | 12.750 |
| QC | 1.702 |
| **Total wall clock** | **241.630** |

**Do not sum stage records:** existing translation and dubbing record intervals
overlap because C7 rewrites are persisted back into translation. Total is measured
independently around the canonical runner. No source download was included. Fast
uses remote ASR, so there was no local-model download to subtract.

Cached rerun: **3.234 s**, no upstream calls. Not a fresh-speed benchmark.
Instrumentation: **Demucs 0, WhisperX 0, semantic AI 0** on fresh and cached Fast.

Artifact checks: 216 words survive ASR -> semantic -> translation -> dubbing;
all 38 semantic cues have successful translations; 11 dubbing chunks. Dry mixed
PCM exactly matches independently executed voice-only volume/limiter FFmpeg
reference, proving that the source track was not mixed in. Frames at 5/20/40/63 s
were inspected: no blank/corrupt video; burned source subtitles can overlap the new
Vietnamese captions. This is not evidence of audio intelligibility/listening approval.

Fast output: 72.789333 s, **NEEDS_REVIEW**, not publish-ready. Warnings retain
Edge human-approval requirement, high speech speed, video retime (1.033789) and
segment review. No QC threshold was relaxed or human approval fabricated.

## HQ comparison / unmet acceptance

HQ same source, fresh warm-up attempt: **522.245 s**, **FAILED** at existing timing
gate; separation 69.315 s, ASR/alignment 77.847 s, semantic AI 35.032 s,
translation 91.632 s, dubbing 245.951 s. Demucs/WhisperX/semantic AI each called once.
Overflow reports include 1.6906x, 3.0168x, 1.4058x, 2.1714x and 2.4858x chunks.
There is no HQ delivery. This warm-up/failure is NOT a warmed successful baseline
and **must not be used to claim >=50% full-pipeline improvement**.

The long local samples are damaged: the roughly 575 s MP4 decodes only about 185 s
of audio and logs `partial file`; the roughly 933 s sample likewise decodes only
about 91 s. The adapter now rejects damaged input instead of calling a surviving
prefix a long-video success. The earlier prefix Groq call is excluded from DoD.

Still needed: intact 5–10 minute speech media; clear/loud-music/dense-speech
listening matrix; human confirmation of source fidelity and final utterance;
successful HQ baseline after warm-up, then fresh Fast with the same saved AI/TTS
choices and no threshold relaxation. The 70 s delivery remains a review artifact.

Independent review worker failed with a model repetition loop. No independent
review PASS is claimed. Parent checked diff and real artifacts; automated/live
execution evidence above remains distinct from code inspection.
