# Phase C0 — Donor / Source Audit and Decision Matrix

Date: 2026-09-23

Status: **COMPLETE**

This audit decides what Affiliate2 should keep, adapt, reimplement or reject before Phase-C runtime changes begin.

The objective is not to assemble multiple OSS applications. The objective is to keep one Affiliate2 application and reuse only proven ideas/contracts where they reduce risk or improve output quality.

## Ponytail decision

Do **not** download/vendor the donor repositories into Affiliate2.

Current decision:

- keep one application, one job system and one media/localization pipeline
- reuse existing installed/runtime dependencies before adding new ones
- port small patterns/contracts, not donor application architecture
- add a dependency only when a Phase-C benchmark proves the existing stack cannot meet the acceptance gate
- do not create provider factories/abstractions beyond the canonical interfaces that already exist

## Audited source snapshots

| Project | Head audited | License | Role in Phase C |
|---|---|---|---|
| YouDub-webui | `0f6c75935e7a208c4b8ea56140e31c5316953fec` | Apache-2.0 | production-oriented stage/cache/resume, OpenAI-compatible settings, Demucs/background-mix reference |
| SmartSub | `156d6a85e7c12a63b551efb7c03a004e1d661266` | MIT | translation validation, provider UX, timed per-line dubbing, exception workflow |
| VideoLingo | `11048cc4a78e2a36e657224566305ff7bb1a1ad9` | Apache-2.0 | semantic segmentation, terminology/context translation, duration-aware dubbing reference |
| pyVideoTrans | `16a5a047bf1bef9c68133ab500d76c11709617e8` | GPL-3.0 | synchronization/timing algorithm reference |
| OpenCreator | `a153ac073e6d03b55a142266aadce3d82109b37f` | Apache-2.0 | shared automation/expert workspace state and targeted stage rerun reference |
| faster-whisper | `ed9a06cd89a93e47838f564998a6c09b655d7f43` | MIT | canonical ASR implementation already installed by Affiliate2 |
| WhisperX | `2cfd7b7c5c7bba144954364db747319b50e8232b` | BSD-2-Clause | optional word alignment / diarization reference only until benchmark requires it |
| MoneyPrinterTurbo | `3d5f4e421927d61f3eac729cf4b711ac0b69d688` | MIT | future Phase-E composition/render reference; not Phase-C localization runtime |

License note:

Affiliate2's current `app/pyproject.toml` declares AGPL-3.0-only. This audit still avoids casual source copying. Any copied donor code must receive an explicit license/attribution review; reference-only algorithm study remains the default for pyVideoTrans.

## Current Affiliate2 findings

### Media

Current `app/backend/services/ytdlp_service.py` prefers:

```
bestvideo[height<=720]+bestaudio/best[height<=720]/best
```

before unrestricted best quality.

Decision: this is a quality bug/default for the Phase-C goal, not a behavior to preserve.

### AI provider

Current `app/backend/core/ai_provider.py` already contains:

- `AIProvider.COMPATIBLE`
- `base_url`
- OpenAI-compatible client creation
- user-setting resolution for `ai_base_url`

Current database model already contains `ai_base_url`.

But the Settings API/UI does not expose the complete compatible/local-provider path, and the frontend provider dropdown only exposes Anthropic, OpenAI and OpenRouter.

Decision: keep the backend provider abstraction; wire the missing settings/model-discovery UX instead of adding another AI framework.

### ASR

Current requirements already include `faster-whisper`.

Current Whisper service already maps:

- fast
- balanced
- accurate
- best

to different model sizes.

faster-whisper itself already supports `word_timestamps=True`.

Decision: use the installed stack first. Do not add WhisperX until real dubbing samples show that faster-whisper timestamps are insufficient.

### Translation / dubbing

Current Translate runner:

- uses a hard-coded translation prompt
- translates timed segments
- joins all translated text into one `full_text`
- calls Edge TTS directly for the complete text
- later burns captions separately

This bypasses the canonical TTS abstraction and discards per-segment speech timing for dubbing.

Decision: replace this path; do not preserve it for compatibility.

### Render quality

Current media services use several independent H.264 encode paths (commonly CRF 20), while caption burn uses another encode at CRF 23.

Decision: adapt existing FFmpeg utilities toward a minimum-loss/final-compose path instead of introducing another renderer.

## Capability decision matrix

| Capability | Current Affiliate2 | Reference evidence | Decision | Phase-C implementation consequence |
|---|---|---|---|---|
| URL/media download | Existing yt-dlp service + provider fallbacks | YouDub/VideoLingo also center yt-dlp rather than a second crawler | **ADAPT** | Fix quality selection in `ytdlp_service.py`; retain one downloader; keep platform resolver as fallback |
| Source media metadata | Partial/probe utilities, not a canonical quality record | YouDub persists stage artifacts; Phase-C needs source-vs-final QA | **REIMPLEMENT MINIMAL** | Add only the metadata fields required by QA/profile decisions; reuse existing probe utilities |
| Render/composition | Existing FFmpeg services/caption service | MoneyPrinterTurbo demonstrates useful one-pass/FFmpeg composition ideas but is a different product | **ADAPT** | Keep FFmpeg service; reduce avoidable intermediate lossy encodes; no MPT runtime dependency |
| Cloud AI provider | Existing `AIClient` supports Anthropic/OpenAI/OpenRouter/compatible | YouDub and SmartSub expose base URL/model directly | **KEEP + ADAPT UI/API** | Complete Settings/API/UI; do not introduce LiteLLM or another provider framework |
| Local AI | Compatible backend exists but UI/settings path is incomplete | YouDub model discovery; SmartSub/Ollama-compatible patterns | **ADAPT** | Ollama/LM Studio/vLLM are presets over OpenAI-compatible transport where possible, not separate engines |
| Model registry | Static frontend choices | Donors allow explicit model IDs and/or model discovery | **REJECT AS ALLOWLIST** | Registry becomes suggestions; manual model ID remains valid |
| Localization Profile | No canonical reusable Phase-C profile | OpenCreator shared state + SmartSub config snapshots | **REIMPLEMENT MINIMAL** | One persisted profile/config snapshot used by auto/batch/expert; no separate manual config model |
| ASR engine | faster-whisper already installed | faster-whisper supports word timestamps | **KEEP** | Make requested quality reach the actual model; enable/use timestamps required by downstream timing |
| WhisperX | Not installed | Better forced alignment/diarization, but additional dependency/model complexity | **REJECT FOR INITIAL IMPLEMENTATION** | Add only if real-media timing gate fails with faster-whisper |
| Transcript/timeline contract | Existing features use incompatible segment shapes | SmartSub explicitly separates original slots/final timing; OpenCreator persists artifact/version state | **REIMPLEMENT MINIMAL** | Canonical source and target time fields; enough state for targeted rerun, no generic workflow framework |
| Translation quality | Hard-coded single prompt path | VideoLingo context/terminology/duration-aware translation; SmartSub structured batch validation/glossary | **REIMPLEMENT ON EXISTING AI CLIENT** | Context + small preset set + custom instructions + glossary + structured segment identity/validation |
| Translation multi-agent reflection | Not present | VideoLingo has richer multi-step translation | **REJECT INITIALLY** | Do not build a 3-agent translation chain until a benchmark shows one-pass contextual translation is inadequate |
| Canonical TTS service | Existing Edge/OpenAI abstraction | SmartSub has broader provider catalog | **KEEP** | First make Translate use the existing abstraction; add providers only when required |
| Whole-video TTS | Current Translate joins all translated segments | All serious localization donors operate on timed chunks/lines | **REJECT** | TTS per semantic/timed block |
| Dubbing timing | No real per-block fit engine | SmartSub measures synthesized duration and creates alignment plans; pyVideoTrans contains mature speed/video timing logic | **REIMPLEMENT MINIMAL** | Python timing engine: measure → bounded fit/rate → explicit exception; pyVideoTrans is algorithm reference, not imported runtime |
| Video slowdown to solve speech overflow | Not canonical | pyVideoTrans can split the burden between speech speed and video PTS | **DEFER** | Start with translation adaptation + bounded audio speed + silence borrowing; add video retiming only if benchmark proves necessary |
| Background/source separation | Current Translate can replace/mux audio but has no canonical vocal/background separation | YouDub/VideoLingo use Demucs | **DEFER DEPENDENCY, KEEP CONTRACT OPEN** | Start with canonical mix contract. Add Demucs only when perceptual gate demonstrates it is needed for acceptable dub quality |
| Batch jobs | Existing app job/progress infrastructure | YouDub persists stage progress and resumes cached stages | **KEEP + ADAPT** | Extend existing job states/stage artifacts; no Celery/Redis/second worker system |
| Stage resume / targeted rerun | Existing jobs exist, but localization-specific dependency invalidation is incomplete | YouDub caches completed stages; OpenCreator tracks versions/stale artifacts | **ADAPT** | Store effective config/artifact dependencies and rerun from earliest affected stage; no generic DAG engine |
| Exception review | Existing app review surfaces, no localization exception model | SmartSub filters failed/overlong lines and permits per-line regeneration | **ADAPT** | READY / NEEDS_REVIEW / FAILED + actionable per-block reasons |
| Expert workspace | Existing UI has tool/settings surfaces | OpenCreator shares current task state between automation and workspace | **ADAPT PATTERN** | Expert UI is a view/editor over the same job/profile/segments, not a second pipeline |
| Creative short assembly | Not Phase C | MoneyPrinterTurbo has broad short-video assembly/render workflows | **REJECT FOR PHASE C** | Revisit in Phase E only |

## Donor-specific conclusions

### YouDub — ADAPT patterns, do not embed app

Useful:

- explicit serial stages and artifact cache
- resume after failed stage
- OpenAI-compatible Base URL/model settings
- Demucs → vocals/background → ASR → translate → TTS → mix → FFmpeg flow

Do not take:

- its complete task/database/web application
- its exact provider set
- its Bilibili/YouTube-only product assumptions

### SmartSub — strongest direct reference for exception-driven dubbing

Useful:

- original subtitle window remains distinct from resolved target timing
- measured TTS duration, not text-length assumption, drives final decision
- overlong items remain explicit and reviewable
- per-line synthesis/regeneration
- glossary and structured translation validation
- provider settings are user-configurable rather than hard-coded

Do not take:

- Electron-specific architecture
- its large provider catalog wholesale
- every alignment option before real Affiliate2 samples require them

### VideoLingo — strongest translation/subtitle reference

Useful:

- semantic sentence splitting
- context/terminology extraction
- translation quality checks
- spoken-duration awareness before dubbing
- block-based TTS and silence-aware audio merge

Do not take initially:

- the complete multi-step prompt graph
- Streamlit/config architecture
- its complete TTS backend catalog

### pyVideoTrans — algorithm reference only

Useful:

- clear source vs target duration accounting
- measured dub duration
- audio time-stretch
- optional video PTS adjustment
- aligned final audio composition

Do not port its `SpeedRate` class wholesale. It is large, coupled to pyVideoTrans internals and far beyond the minimum Phase-C implementation.

### OpenCreator — state/UX reference only

Useful:

- automation and workspace share current task state
- persisted result versions/artifact state
- stage-level rerun semantics

Do not introduce a generic skill/agent/workflow framework into Affiliate2.

### faster-whisper — KEEP

It is already installed and supports word timestamps. This is the first timing source to benchmark.

### WhisperX — benchmark-gated

Potential value:

- forced word alignment
- diarization

Cost:

- additional dependency/model/runtime complexity

No Phase-C dependency is added until real samples show a timing or speaker-identification gap that materially affects output.

### MoneyPrinterTurbo — Phase E only

Useful later:

- automated short-video composition
- FFmpeg-oriented concat/render improvements
- source-fit/aspect/clip composition patterns

It does not solve Phase-C localization and must not become another media runtime.

## Canonical Phase-C contracts after C0

The implementation should converge on only these product-level concepts:

1. **Download quality policy**
2. **Canonical media metadata**
3. **Canonical AI provider settings**
4. **Localization Profile**
5. **Localization Segment with distinct source/target time**
6. **Canonical ASR result**
7. **Canonical TTS block result**
8. **Timing/QC result**
9. **Existing job + stage artifacts**

Do not add generic plugin systems, workflow engines, DAG frameworks, provider registries or media applications beyond these needs.

## Implementation order unlocked by C0

The lowest-rework order is:

1. **C1 media quality** — remove 720p-first default and establish source/final evidence
2. **C2 AI/local provider wiring** — expose the compatible backend already present
3. **C4 transcript/timeline contract** — define source vs target timing before Translate/TTS work
4. **C3 Localization Profiles** — snapshot settings against the contracts above
5. **C5 ASR routing/timestamps** — reuse faster-whisper first
6. **C6/C7 translation + timed dubbing**
7. **C8 mix**
8. **C9/C10/C11 automation, QC and expert override**
9. **C12 intelligence convergence**
10. **C13 real-media gate**

C1, C2 and the data-contract portion of C4 may be implemented independently, but they must converge before C6/C7.

## C0 exit criteria

- [x] Current Affiliate2 media/AI/ASR/Translate/TTS/render paths inspected
- [x] Required donor/reference repositories inspected at pinned snapshots
- [x] License identified for each required donor
- [x] KEEP / ADAPT / REIMPLEMENT / REJECT decisions recorded
- [x] No donor repo vendored into Affiliate2
- [x] No new dependency added
- [x] Initial WhisperX decision made: benchmark-gated, not installed
- [x] Initial MoneyPrinterTurbo decision made: Phase-E reference only
- [x] Phase-C implementation order fixed

Next implementation gate: **C1 Media Quality Foundation**.
