# Phase E — Creative Remake HQ donor audit

Date: 2026-09-25  
Status: E0 DONOR AUDIT COMPLETE — implementation not started

## Goal

Creative Remake HQ is not Smart Cut and is not a second standalone video app.

Target product flow:

```
DownloadedVideo source
  -> canonical transcript + source timeline
  -> physical shot/scene map
  -> multimodal shot understanding
  -> Vietnamese creative beats
  -> beat <-> source / stock / user / motion asset map
  -> persisted storyboard
  -> HUMAN APPROVAL
  -> composition/render on canonical media primitives
  -> QC / Review
  -> GeneratedVideo(source_type="creative_remake")
  -> Library lineage back to source
```

The donor audit is intentionally capability-first. We reuse mature pieces but do
not import another WebUI, database, job system, media library, downloader or
renderer as a cooperating mini-app.

---

## 1. What Affiliate2 / ViralMint already has

### 1.1 Canonical source, transcript and lineage — KEEP

Already canonical:

- `DownloadedVideo` is the Studio source identity.
- persisted transcript/timed transcript segments are reusable;
- Library lineage already understands `source_downloaded_video_id`;
- `creative_remake` is already a registered Library producer identity;
- one job/progress system and one React/FastAPI application already exist.

Decision: **KEEP**. Phase E must consume these contracts, not create a second
source/media graph.

### 1.2 Stock / Smart Video scene assembly — ADAPT, not canonical storyboard

Relevant code:

- `backend/services/pexels_service.py`
  - `extract_visual_scenes()`: script -> visual scene/search query plan;
  - Pexels search + quality filtering;
  - user images claim specific scene slots;
  - clip normalization, duration fitting, transitions and stitching.
- `backend/agents/generator.py`
  - source-aware script generation;
  - TTS, captions, background music, SFX;
  - saves generated output with source lineage.

Useful capability:

```
script
  -> scene queries
  -> stock / user stills
  -> duration allocation
  -> normalize/stitch
```

Limitations for Creative Remake:

- scenes are derived from narration duration, not physical source-video shots;
- no persistent source shot identity;
- no multimodal source understanding;
- no beat -> source-shot candidate map;
- no storyboard approval artifact;
- stock generation can replace source visuals rather than intentionally reuse
  the strongest original shots.

Decision: **ADAPT** scene/material acquisition and FFmpeg assembly primitives.
Do **not** promote the current Smart Video scene list into the Phase-E canonical
storyboard model.

### 1.3 B-roll planner — ADAPT

`backend/services/broll_service.py` already has:

- AI B-roll cue planning;
- exact trigger text / position percentage;
- desired duration;
- stock search query;
- visual description / purpose / priority;
- mapping cue text onto word timestamps.

Important limitation: the OSS code only plans/maps cues. Its own tests record
that the separate SaaS version had an execution pipeline that is not present in
this repository.

Decision: **ADAPT** the cue vocabulary and transcript timing mapper into the
Phase-E asset planner. Do not pretend the missing SaaS compositor is available.

### 1.4 SceneStoryboard React component — UI PRIMITIVE ONLY

`frontend/src/components/create/SceneStoryboard.jsx` already provides a simple
editable scene-card list with add/remove/split hooks.

It is not a durable Creative Remake storyboard contract by itself: it has no
stable beat/shot/asset IDs, source-shot thumbnails, candidate assets, approval
state, provenance or stale-artifact handling.

Decision: **ADAPT UI primitive**, after the backend owns a persisted storyboard
schema. Do not let React-local state become the source of truth.

### 1.5 Motion Studio / AI Compose — KEEP AS CONTEXTUAL ASSET/OVERLAY PROVIDER

Current Motion stack already provides:

- staged local image/video/audio assets;
- AI-authored deterministic/seekable HTML/GSAP composition;
- validation + repair loop;
- archive-before-replace behavior;
- local offline render;
- Library import of finished motion pieces.

Strong safety properties in `composition_author.py` / `studio_service.py`:
deterministic frame seeking, asset validation, no CDN runtime, archived previous
composition, structural/lint checks before replacement.

Limitations:

- current authored pieces are intentionally small motion compositions;
- duration choices are 5/10/15/30 seconds;
- no source-shot/beat/storyboard semantic model;
- using it as the whole Remake engine would create an unnecessary second
  timeline/composition owner beside the canonical media pipeline.

Decision: **KEEP** Motion as a provider for kinetic type, lower thirds, stat
cards, transitions and short generated inserts. **REJECT** making Motion Studio
the Phase-E orchestration owner.

---

## 2. External donor audit

### 2.1 MoneyPrinterTurbo — ADAPT MATERIAL ACQUISITION / ALLOCATION

Repository: `harry0703/MoneyPrinterTurbo`  
License: MIT

Current relevant capability:

- topic/custom-script -> multi-scene video;
- local image/video materials;
- Pexels, Pixabay and Coverr stock providers;
- AI image / AI video material providers;
- aspect-aware material search;
- material duration/order controls;
- TTS, subtitles, BGM and final short-video assembly;
- records material source/provenance metadata.

Why it matters:

Affiliate2 currently has a strong Pexels path, but Phase E needs a richer
fallback ladder when source shots do not cover a beat:

```
source shot first
  -> user/Library asset
  -> stock provider
  -> motion graphic
  -> optional generated visual
```

Decision: **ADAPT**, specifically:

- provider-neutral material result shape;
- multi-provider stock fallback (Pexels/Pixabay/Coverr pattern);
- material provenance;
- duration/aspect-aware candidate filtering;
- scene/beat material allocation patterns.

Reject:

- importing MoneyPrinterTurbo as another app/runtime;
- replacing the canonical downloader/job/Library/TTS/subtitle layers;
- MoviePy ownership when Affiliate2 already has FFmpeg primitives.

### 2.2 OpenCreator — STRONG ADAPT FOR STORYBOARD / ARTIFACT CONTRACTS

Repository: `krillinai/OpenCreator`  
License: Apache-2.0

Most useful reference is its Stickman creator flow, not its desktop/Codex shell.

Relevant design:

- strict script segment schema with stable IDs;
- strict storyboard shot schema;
- every source segment must map exactly once;
- duplicate/missing IDs are rejected;
- measured narration timing is authoritative;
- timeline manifest verifies storyboard timing against current audio timing;
- artifacts carry explicit IDs, hashes and source-artifact lineage;
- stale storyboard/timing mismatches fail loudly instead of silently rendering;
- exact one-current-image/one-current-audio checks per shot.

Decision: **STRONG ADAPT** of the data-contract principles:

```
stable beat IDs
stable source shot IDs
explicit asset IDs
artifact lineage
schema validation
stale-plan detection
approval before expensive render
```

Reject:

- OpenCreator's Electron/Codex agent runtime;
- stickman-specific image generation/render pipeline;
- direct provider HTTP clients where Affiliate2 already has a canonical AI
  provider abstraction.

### 2.3 PySceneDetect — ADOPT FOR PHYSICAL SHOT BOUNDARIES

Repository: `Breakthrough/PySceneDetect`  
License: BSD-3-Clause

Relevant capability:

- mature Python shot/scene boundary library;
- Content, Adaptive, Histogram and Threshold detectors;
- current releases also include a TransNetV2 ONNX detector;
- returns deterministic physical cut boundaries independent of the LLM.

Why this is needed:

A language model should not invent where a camera cut occurred. Phase E needs a
physical source-shot map first, then AI can describe/rank those shots.

Decision: **ADOPT** as the initial shot-boundary owner behind an Affiliate2
`scene_map` service.

Initial policy:

- deterministic Content/Adaptive detector first;
- generate shot IDs + start/end + representative frame(s);
- map transcript segments onto shots by time overlap;
- keep the detector behind one app interface so the implementation can be
  swapped without changing storyboard data.

### 2.4 TransNetV2 — DEFER DIRECT INTEGRATION

Repository: `soCzech/TransNetV2`  
License: MIT

It is a proven neural shot-boundary detector and has PyTorch inference code.

However PySceneDetect now exposes a TransNetV2 ONNX detector itself.

Decision: **DEFER direct donor/runtime integration**.

If Content/Adaptive detection is insufficient on real Douyin/Bilibili samples,
enable TransNetV2 **through the same PySceneDetect-facing scene-map interface**
rather than installing a second shot-detection subsystem.

This avoids a separate TensorFlow/PyTorch model owner merely to solve one
boundary-detection task.

### 2.5 Remotion — REFERENCE ONLY / REJECT AS RUNTIME

Repository: `remotion-dev/remotion`  
License: custom/tiered Remotion license, not a simple permissive OSS license.

Capability is excellent for React-based deterministic video composition,
timelines and programmatic rendering.

Decision: **REFERENCE ONLY**.

Reasons:

- Affiliate2 already owns FFmpeg media rendering + Motion/HyperFrames;
- adding Remotion would create another renderer/runtime and Node-side video
  composition system;
- licensing is more complicated than the permissive donor set;
- no Phase-E requirement currently justifies the duplication.

Revisit only if a measured composition requirement cannot be implemented safely
on the existing renderer.

---

## 3. Existing Phase-C donors

VideoLingo, SmartSub, YouDub, pyVideoTrans and the existing localization donor
set remain useful for translation/TTS/timing behavior but do not solve the
Creative Remake source-visual/storyboard problem.

Decision: **NO NEW PORT** from those projects for E0.

Creative Remake should call the already-canonical Phase-C localization/TTS/
caption layers after the storyboard is approved.

---

## 4. Capability matrix after audit

| Phase-E capability | Current Affiliate2/ViralMint | Donor | Decision |
|---|---|---|---|
| canonical source/transcript | yes | — | KEEP |
| physical shot boundaries | no canonical owner | PySceneDetect | ADOPT |
| neural difficult-cut fallback | no | TransNetV2 | DEFER via PySceneDetect |
| source-shot transcript overlap | partial timeline primitives | app-owned | IMPLEMENT THIN |
| multimodal shot description | no Phase-E contract | canonical AI provider | EXTEND, no new app |
| creative beat schema | no | OpenCreator contract pattern | ADAPT |
| artifact/stale-plan validation | localization has related patterns | OpenCreator | ADAPT |
| source shot reuse | clip primitives exist | app-owned | ADAPT |
| stock fallback | Pexels only, strong existing path | MoneyPrinterTurbo | ADAPT provider pattern |
| user/Library asset fallback | Library + Motion staging exist | MoneyPrinterTurbo pattern | ADAPT |
| motion overlays/inserts | yes | ViralMint Motion | KEEP contextual |
| storyboard cards | primitive exists | OpenCreator contract principles | ADAPT |
| human storyboard approval | reserved UI only | OpenCreator pattern | IMPLEMENT |
| final composition/render | FFmpeg + current media primitives | — | KEEP canonical |
| TTS/captions/audio | yes, Phase C | existing donors already absorbed | KEEP |
| output lineage | `creative_remake` already reserved | — | KEEP |
| second rendering framework | not needed | Remotion | REJECT |

---

## 5. The actual missing core

After donor audit, Phase E is **not** "build another video generator".

ViralMint already covers much of the bottom half:

```
asset search
asset staging
stock assembly
motion inserts
TTS
captions
audio
FFmpeg
Library
jobs
```

The genuinely missing Phase-E core is:

```
SOURCE VIDEO
   |
physical shot map
   |
shot understanding
   |
creative VN beats
   |
beat <-> asset candidates
   |
PERSISTED STORYBOARD
   |
HUMAN APPROVAL
   |
existing composition/render capabilities
```

That is where implementation effort should go.

---

## 6. Proposed Phase-E implementation order

### E1 — Canonical source shot map

Add one app-owned service/artifact:

```
shot_id
source_start_ms
source_end_ms
representative_frame(s)
transcript_segment_ids
```

Use PySceneDetect behind that interface.

No AI decision is allowed to change physical shot boundaries.

### E2 — Multimodal shot understanding

For representative shot frames, use the existing AI-provider ownership and add
a canonical vision-capable request path rather than direct provider clients.

Per-shot semantic output should be structured, for example:

```
subjects
product / object
action
setting
on_screen_text
camera / composition
visual_quality
source_reuse_score
safety / unusable reason
```

No silent text-only downgrade when a profile requires visual understanding.

### E3 — Creative beat planner

Generate stable Vietnamese remake beats from source transcript + shot semantics.

Minimum beat contract:

```
beat_id
source_meaning_refs
narration
purpose
target_duration_ms
preferred_asset_strategy
must_preserve_facts
```

Use strict-ID/schema principles from OpenCreator.

### E4 — Asset mapper

For every beat:

1. rank reusable source shots;
2. allow Library/user assets;
3. search stock fallback;
4. allow Motion insert;
5. generated visual only as an explicit optional fallback.

Persist candidate provenance and why a candidate was chosen.

### E5 — Storyboard approval

Promote the reserved Storyboard Review category to a live source only when a
real persisted storyboard exists.

UI can adapt `SceneStoryboard.jsx`, but backend artifact state is authoritative.

Each beat should show:

- narration/intent;
- source reference;
- selected visual;
- alternative candidates;
- source timestamps/provider provenance;
- duration;
- replace/reorder/edit;
- approval state.

**No expensive final composition/render before explicit approval.**

### E6 — Composition

After approval, convert storyboard into one canonical composition plan.

Prefer existing FFmpeg/media primitives for source/stock video and use Motion
Studio only for contextual generated inserts/overlays.

Reuse Phase-C TTS/caption/audio quality paths instead of Stock Video's weaker
defaults when the project requests HQ.

Aim for one final visual encode.

### E7 — QC + Library

Validate:

- every approved beat has an asset;
- all source ranges stay within source timeline;
- no stale source/shot/storyboard identity;
- output duration matches resolved timeline;
- audio/caption failures surface as Review exceptions.

Persist:

```
GeneratedVideo.source_type = "creative_remake"
GeneratedVideo.source_downloaded_video_id = <source>
```

so existing Library lineage remains canonical.

---

## 7. Explicit non-goals

Do not add:

- a second React/WebUI app;
- a second downloader;
- a second ASR model owner;
- a second generic AI provider client;
- a second Library;
- a second job queue;
- Remotion as a parallel renderer;
- OpenCreator's desktop/Codex runtime;
- MoneyPrinterTurbo as a subprocess/service;
- direct TransNetV2 runtime before real-media evidence requires it.

---

## 8. Next engineering task

**E1 — source shot map**, not storyboard UI first.

Reason: storyboard, visual understanding and source-asset reuse all depend on a
stable physical shot identity. Building UI or creative prompts before that
would create another transient scene model that has to be replaced later.

E1 acceptance:

1. one downloaded source -> deterministic ordered shot list;
2. every shot has stable ID/start/end;
3. representative frames are generated without duplicating the downloader;
4. transcript segments map to shot IDs by source-time overlap;
5. artifact is durable/resumable and bound to source media identity;
6. changing source media invalidates the artifact;
7. no LLM is required for the boundary-detection stage;
8. focused tests + one real Douyin/Bilibili sample.
