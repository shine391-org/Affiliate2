# C13 Historical Regression Report

## Git
- **branch:** phase-c-media-ai-hq-localization
- **HEAD:** d3306dd7103d5c2f24b511dfa04afbd987ef5601
- **remote HEAD:** d3306dd7103d5c2f24b511dfa04afbd987ef5601
- **ahead/behind:** 0 0
- **working tree:** 4 modified, 20 untracked (unchanged during this run)

## Environment
- **Python:** 3.11.16 (`.venv/Scripts/python.exe`)
- **Node/npm:** v22.23.2 / 10.9.8
- **FFmpeg/ffprobe:** 9.0.1
- **Torch/CUDA/GPU:** 2.8.0+cpu / False / none
- **faster-whisper model:** (default, quality=best)
- **WhisperX:** imported successfully
- **pyannote:** imported successfully
- **Demucs:** imported successfully, device=cpu
- **AI provider/model:** compatible / gemini-3.5-flash-lite (via Google Generative AI)
- **TTS provider/model/voice:** edge_tts / null / vi-VN-HoaiMyNeural
- **HF token configured:** NO

## Source
- **path:** `E:\Affiliate ver 1\storage\videos\qa\sample.mp4`
- **hash/identity:** sha256 `1e38f6cf353d194199cae813148c3441cfde6f7ed13e596cd636f676863e7793` (12,649,947 bytes)
- **duration:** 65.7s
- **resolution/fps:** 1280x720 / 30fps
- **codecs:** H.264 + AAC
- **timeline provenance valid:** YES (ASR segments 84ms–63297ms, all within 65700ms source)

> **Note:** Sample was NOT at the expected path. Found at `app/storage/localization_jobs/qa-phase-c-e2e-1790145270/source.mp4` (same hash). Copied to `storage/videos/qa/sample.mp4` for the canonical run.

## Pipeline Job
- **job_id:** `qa-c13-regression-20260924-0610`
- **profile:** vi-affiliate-hq
- **manifest version:** 2
- **stages:** all 8 complete (separation → asr → semantic → translation → dubbing → audio_mix → final_render → qc)

## C5/C6
- **ASR segments:** 22
- **words:** 126
- **alignment:** YES (WhisperX forced alignment applied)
- **diarization:** NO — reason: `missing_huggingface_token`
- **speakers:** 0 (single-speaker or unknown)
- **semantic cues:** 24
- **translations:** 24 (all success)
- **translation failures:** 0
- **duration rewrites:** 1 (sem-0003 rewritten for shorter Vietnamese)

## C7
- **chunks:** 12
- **cue IDs per chunk:** see plan.json (chunks 7, 8, 9, 10, 11, 12 merge adjacent cues)
- **TTS provider/model/voice:** edge_tts / null / vi-VN-HoaiMyNeural
- **actual raw TTS durations:** per-chunk measured (e.g., chunk 1=3192ms, chunk 3=4248ms, chunk 5=4656ms)
- **rewrite count:** 1
- **rewrite AI unavailable:** 0
- **max raw required speed:** 1.2718x (chunk 5)
- **max fitted speed:** 1.2718x (chunk 5)
- **video slowdown:** 1.0 (no retime needed)
- **over-accept count:** 2 (chunks 3, 5 exceed 1.15 accept threshold)
- **unresolved over-max:** 0 (both within 1.3 max ceiling)
- **target speech coverage:** 1.1661 (116.61% — Vietnamese dub slightly longer than source, no speech dropped)
- **timing issues:** 2 speed warnings (chunks 3: 1.1939x, 5: 1.2718x) — within max, above accept

## C8
- **background:** `separation/audio_bgm.wav` (11.6MB, Demucs htdemucs_ft clean separation)
- **audio mix:** `audio_mix/mixed_audio.wav` (12.6MB, HQ clean dub + background at -6dB)
- **single final encode:** YES (H.264 1080x1920, 30fps, CRF-based HQ profile)
- **peak/clipping:** max volume -4.9 dB (no clipping)
- **final ffprobe:**
  - duration: 65.7s
  - resolution: 1080x1920
  - fps: 30/1
  - video codec: h264
  - audio codec: aac (48kHz, stereo)
  - audio stream: YES
- **final path:** `E:\Affiliate ver 1\storage\tools\out\qa-c13-regression-20260924-0610.mp4`

## C9
- **manifest version:** 2
- **resume:** VERIFIED — rerun with same job_id reused separation, ASr, semantic, translation (timestamps unchanged); only dubbing/audio_mix/final_render/qc rerun
- **expensive stages reused:** YES (separation, ASR, semantic, translation all preserved)

## C10
- **QC status:** NEEDS_REVIEW
- **failures:** [] (none)
- **warnings:**
  1. `preview_tts_provider` — Edge TTS is the free preview/fallback tier; human voice-quality approval required before publish
  2. `high_speech_speed` — 2 chunks (3, 5) exceed 1.15 accept threshold (within 1.3 max)
  3. `segment_needs_review` — 2 segments flagged for review

## C11
- **target chunk:** N/A (no targeted regeneration performed in this run — pipeline completed without expert intervention)
- **non-target chunks unchanged:** N/A
- **final rerender:** N/A
- **QC after regen:** N/A

## Regression Comparison

### OLD
- 21 timing units
- 15 overflows (71% failure rate)
- most Vietnamese dub missing

### NEW
- semantic cues: 24
- dubbing chunks: 12 (smart merging of adjacent cues)
- unresolved over-max: 0
- target speech coverage: 116.61%
- max raw speed: 1.2718x
- max fitted speed: 1.2718x
- video slowdown: 1.0
- QC: NEEDS_REVIEW (no failures)

### OLD REGRESSION FIXED: YES
- 15/21 overflows → 0 unresolved over-max
- most Vietnamese dub missing → 116.61% target speech coverage
- 21 raw timing units → 12 smart chunks with measured TTS fit
- No timing threshold relaxed; no quality gate disabled

## Fixes
- **commits:** none (no code bugs found during this run)
- **tests:** none (pipeline behaved as designed)

## Verdict
- **HISTORICAL SAMPLE TECHNICAL PASS:** YES
- **USER PERCEPTUAL VERDICT:** PENDING

### Summary
The canonical Phase-C pipeline fixed the historical regression end-to-end. The old C7 prototype produced 15/21 timing overflows and most Vietnamese dub was missing. The new pipeline produced 0 unresolved over-max, 116.61% target speech coverage, and 12 well-fitted dubbing chunks. QC reports NEEDS_REVIEW (not FAILED) due to Edge TTS preview-tier warning and 2 speed warnings within acceptable bounds. No code changes were required.

### Next
- User must inspect the final video for perceptual quality
- Final video path: `E:\Affiliate ver 1\storage\tools\out\qa-c13-regression-20260924-0610.mp4`
