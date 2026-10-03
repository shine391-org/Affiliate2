# Measured-first localization and speaker review

Date: 2026-10-03. Branch: `feat/localization-fast-mode`.
Reviewed source HEAD: `8d79c40657acb2a9d076bf1f2d4dc405f0311a76`.
Scope: corrective Phase C/Fast work on this branch; no merge to `master`.

## Contracts and evidence

| Contract | Implementation and regression evidence |
| --- | --- |
| Measured-first rewrite | Remove the calibrated pre-synthesis rewrite branch. Both the measured loop and `_adapt_chunk_items` require a positive real duration with required speed above `dub_speed_max`. Four deliberately wrong estimates, after enough calibration samples, still produce zero rewrites when measured audio fits. |
| Calibration ownership | Atomic job state replaces transcript/global calibration. Group observations by effective connection/provider/model/voice/reference identity, including reference-file content. Each observation stores the exact synthesized text, its own estimate and actual duration; reused identities replace observations rather than double-weighting them. Same-binding reruns retain state; incompatible bindings clear it. |
| Canonical audit/count | Count unique segment IDs with changed spoken or caption text, including multiple accepted revisions of one segment only once. Result, report, metadata, stage metadata and audit use that definition. Declare `rewrite_audit.json` as a durable stage artifact and save before the next synthesis, including on failure. Preserve historical audit when upgrading old jobs. |
| Meaning invariants | Reject changed negation, numeric values/counts, unit loss and changed numeric/unit associations; protect explicit glossary/terms rather than arbitrary bigrams. Bidirectional phrase-aware polarity checks reject antonym flips. C6 invalid translations follow bounded retry/fallback and fail or remain explicitly untranslated. Protected spoken forms remain guarded after pronunciation preparation. |
| Diarization | HQ auto failure remains explicit. Fast defaults to auto diarization with forced alignment off. The diarization-only conversion retains existing IDs, complete word data and timestamps; deterministic splitting preserves speaker turn boundaries. Missing token/dependencies, inference failure and empty results are not successful detection. |
| Speaker voices | Persist a per-job map, assign distinct available voices by default, retain it during resume/rerun and targeted chunk regeneration, and reset it for incompatible TTS bindings. Vietnamese Edge/Azure defaults resolve two speakers to HoaiMy/NamMinh. Unsupported/unavailable catalogues do not invent voice IDs: unresolved output stays in QC review. GPT-SoVITS diversity is evaluated by reference audio content, not its ignored voice ID, file path or prompt text: copying one reference into two files cannot fake distinct speakers. Reusable profiles strip `SPEAKER_\d+` labels. |
| QC/Studio | Report detection status, count, voice map and acoustic identities, unassigned speech and unresolved overlap. Shared acoustic identities across speakers cannot be clean READY unless shared mode is explicit. Studio and Expert expose per-speaker selection; segment corrections retain word anchors and secondary overlap evidence. Production React handlers are tested for polling, revision-bound saves, job navigation, shared mode, paused C6 review and stale-edit errors. |
| Existing contracts | Focused tests retain speaker/voice chunk boundaries, timing locks, cadence/onset handling, provider snapshot/rebind behavior, translation review, stage ownership, render/mix and Publish handoff. An older/incomplete dub rebuild also invalidates completed downstream consumers to avoid stale audio/QC. |

`rewrite_count` covers unique changed segments in the job's durable accepted-rewrite ledger; AI request/accepted-call counts are separate telemetry. Audit entries retain spoken and caption before/after, the measured reason/speed, ceiling, timing window, durations and timestamp. A subsequent synthesis failure leaves `duration_after_ms` null without losing the accepted change.

## Executed verification

Environment: Linux, Python 3.12, CPU PyTorch 2.6.0, Node 24.19.0, repository frontend dependencies and FFmpeg. Real-media ASR used faster-whisper 1.2.1 and the repository-locked PyAV 18.1.0. SDK/test dependencies were installed locally; repository dependency manifests were unchanged.

Backend: **586 passed**, no skipped tests in this focused invocation:

```sh
cd app
DEBUG=false python -m pytest -q \
  tests/test_localization_measured_speakers.py \
  tests/test_phase_c_alignment.py tests/test_phase_c_dubbing_planner.py \
  tests/test_phase_c_localization_profiles.py tests/test_phase_c_localization_timeline.py \
  tests/test_phase_c_localization_stages.py tests/test_phase_c_localization_qc.py \
  tests/test_phase_c_localization_soft_overflow.py tests/test_phase_c_localization_api.py \
  tests/test_phase_c_provider_binding.py tests/test_phase_c_rewrite_feedback.py \
  tests/test_phase_c_rhythm_allocation.py tests/test_phase_c_semantic_segmentation.py \
  tests/test_phase_c_tts_timing.py tests/test_phase_c_word_onset.py \
  tests/test_fast_mode_profiles.py tests/test_fast_mode_routing.py \
  tests/test_dubbing_rewrite_guard.py tests/test_dubbing_speech_preparation.py \
  tests/test_localization_profile_ui_contract.py tests/test_translate_batch_resilience.py \
  tests/test_phase_c_render_quality.py tests/test_phase_c_audio_separation.py \
  tests/test_phase_c_audio_separation_progress.py tests/test_localization_exceptions.py \
  tests/test_localization_publish_service.py --tb=short --show-capture=no
```

Frontend: **70 passed**, including five React runtime speaker-panel tests:

```sh
cd app/frontend
node --test src/studioProviderBinding.test.js src/localizationPreview.test.js \
  src/localizationReviewResume.test.js src/providerBinding.test.js \
  src/providerBindingIntent.test.js src/studioReadinessModel.test.js \
  src/ttsLocalizationRecovery.test.js src/speakerVoicePanel.test.js
npm run build
```

Production build passed (1500 modules). Existing large-bundle and npm environment warnings remain. `python -m ruff check` on backend and changed test modules passed; `git diff --check` passed. This was focused module/integration verification, not a full application/release suite.

Some old fixtures required correction: they expected a measured-fit rewrite client error from an estimate, intentionally replaced "long" with "short", or omitted an explicit glossary target. They now provide measured overflow where rewriting is intended and preserve protected meaning. Existing single-speaker QC fixtures explicitly disable diarization so their unrelated assertions remain isolated; new tests cover auto failures and speaker mismatch.

## Media attempts and remaining acceptance

Repository samples accessible through GitHub were probed and run through canonical ASR with a Fast profile explicitly selecting `base` for this QA attempt. This explicit QA override does not change production's `small` default.

| Sample | Actual result |
| --- | --- |
| `data/samples/douyin_sample1.mp4` | 2.6-second HEVC video, no audio stream. Canonical ASR rejected it with `No audio stream found`. |
| `data/samples/local_sample1.mp4` | 15-second H.264/AAC video. Canonical ASR rejected it with `No speech detected — nothing to localize`. It does not establish single-speaker dub quality. |
| `data/samples/douyin_derived_sample3.mp4` | 20-second audio-only AAC despite its extension. Actual ASR produced four Chinese-language segments with 34 timed words. Fast diarization reported `missing_huggingface_token`, `diarization_applied=false`, four unassigned segments; it retained those ASR words. This verifies the missing-token status on real audio, not full video localization. |
| Larger Bilibili/real-Douyin samples | Present in the source tree, but the connected GitHub file API did not return binary content above its file-content limit; the generic fetch API rejects binary responses. They could not be used for this run. |

The first ASR attempt encountered an environment SOCKS dependency error and then a PyAV 19 API mismatch. Installing the missing SOCKS dependency and using locked PyAV 18.1.0 allowed the final outcomes above. No application dependency change was made for these setup issues.

Unproven cases: full single-speaker and two-speaker video localization; same-gender voice suitability; perceptual overlap handling; live gated WhisperX/pyannote inference success/failure; paid or compatible-provider voice catalogues/TTS; live GPT-SoVITS reference diversity; Windows/CUDA behavior; browser visual/layout QA. Missing-token behavior is exercised on real audio; inference failures and multi-speaker/overlap contracts are deterministic tests with mocked inference/audio durations, not live acceptance.

The guards are finite deterministic checks, not a semantic equivalence model. Numeric/unit notation, listed language negation/polarity patterns and explicit terms are protected; paraphrase, negation scope, unlisted/spelled-out quantities and broad multilingual meaning still require C13 human review. Unsupported voice availability is surfaced as unresolved QC rather than a claimed default distinct-voice success. Full real-media/perceptual DOD is therefore still pending; this report claims the engineering checks actually executed.

## Files changed

- Backend API/core: `app/backend/api/localization.py`, `app/backend/core/localization.py`, `app/backend/core/tool_runners.py`.
- Backend services: `alignment_service.py`, new `dubbing_job_state.py`, `dubbing_planner.py`, `dubbing_rewrite_guard.py`, `localization_qc_service.py`, `localization_service.py`, `localization_stage_service.py`, `semantic_segmentation.py` under `app/backend/services/`.
- Studio/Expert UI: new `app/frontend/src/components/tools/SpeakerVoicePanel.jsx`, `app/frontend/src/components/tools/LocalizationExpertPanel.jsx`, `app/frontend/src/pages/StudioLocalize.jsx`.
- Tests: new `app/tests/test_localization_measured_speakers.py`, new `app/frontend/src/speakerVoicePanel.test.js`; updates to dubbing guard/speech preparation, Fast profiles, localization API/profiles/QC/provider binding and translation resilience tests.
- Documentation: this report, `docs/PHASE_C.md`, `docs/ARCHITECTURE.md`, `docs/CONSOLIDATION_PLAN.md`, `PROJECT_STATUS.md`.
