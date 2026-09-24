# Affiliate2 UI/UX Convergence Plan

Status: **UX1 + UX2 SHELL IMPLEMENTED — later convergence phases remain planned**

Implementation checkpoint (September 24, 2026):

- workflow-first sidebar now owns Discover, Library, Studio, Review, Publish,
  Performance, Utilities, and Settings;
- Discover is the parent workspace for Spy, Channels, and persisted Candidates;
- legacy `/scout`, `/channels`, `/videos`, `/clips`, and `/tools/*` URLs redirect
  without dropping query strings or hashes;
- candidate acquisition still uses the canonical Scout/download APIs and
  downloaded assets remain visible through the canonical Library index;
- Candidates exposes persisted discovery/download/analyze state and opens linked
  local assets in the existing Studio by canonical downloaded-video ID;
- Saved/shortlist is intentionally not represented until a real persisted state
  exists; no client-only fake shortlist was introduced.

This plan replaces incremental ViralMint-page patching with one migration path
toward a workflow-first Affiliate2 interface.

The backend remains canonical and reusable. Frontend migration must not create a
second job system, second media model, second localization pipeline, or another
application shell.

---

## 1. Why convergence is needed

The current frontend still reflects ViralMint's tool-centric information
architecture:

- Chat
- Scout
- My Channels
- Clip Studio
- Library
- Stock Video
- Motion Graphics
- Tools
- Messaging
- Settings

The Tools hub alone exposes more than twenty utilities, while Affiliate2 is
becoming a connected workflow:

```
Discover
  -> Candidate / Source
  -> Library
  -> Studio
     -> Localize
     -> Smart Cut
     -> Creative Remake
     -> Manual Edit
  -> Review
  -> Publish
  -> Performance
  -> Learn
```

Adding new Phase-C/D/E controls directly into isolated ViralMint pages would
duplicate configuration and make workflow ownership ambiguous.

Therefore, until this plan is implemented, new frontend controls should be
treated as provisional capability surfaces, not final product IA.

---

## 2. Product navigation target

Primary navigation:

```
Discover
Library
Studio
Review
Publish
Performance
Utilities
Settings
```

Optional global surfaces:

- Activity / jobs — global drawer/panel, not a page-specific log
- Assistant / Chat — contextual assistant, not the primary home architecture
- Help / documentation

### Navigation ownership rules

Each top-level area owns one user question:

| Area | User question |
|---|---|
| Discover | What source/content should I work on? |
| Library | What media and project artifacts do I already own? |
| Studio | What should I make from this source? |
| Review | What requires human attention before proceeding? |
| Publish | Where and when should approved output go live? |
| Performance | What happened after publishing? |
| Utilities | I just need a one-off media operation |
| Settings | What providers, defaults and reusable profiles does the app use? |

A capability must have one canonical workflow owner. Other pages may link to it,
but must not silently duplicate its state/configuration.

---

# 3. Discover architecture

Discover becomes the canonical source-intelligence workspace.

Target subsections:

```
Discover
├─ Spy
├─ Channels
├─ Candidates
└─ Saved
```

## 3.1 Spy

Based on current Scout/Radar behavior.

Responsibilities:

- trend/outlier discovery
- platform search
- Radar score and evidence
- source metrics
- content intelligence
- shortlist/save actions
- open source URL
- download/add to Library
- open candidate in Studio

The current `Scout.jsx` is migrated here rather than remaining a separate
top-level application.

## 3.2 Channels

Current ViralMint `My Channels` functionality moves under Discover.

Responsibilities:

- connect a channel/account by search or URL
- support platform-specific source providers
- display tracked channel videos
- refresh/rescan a channel
- show outlier/trend metrics
- save candidate
- download video
- analyze video
- open in Studio after acquisition

Channel tracking is **not** the same feature as Download.

A channel may be monitored indefinitely without downloading every video.

## 3.3 Candidates / Saved

Canonical shortlist for source items worth acting on.

Candidate states may include:

- discovered
- saved
- analyzed
- downloaded
- opened in Studio
- rejected/archived

A candidate is metadata/evidence, not necessarily a local media file.

---

# 4. Download architecture

Download remains a canonical backend capability, but should not become another
top-level application.

Entry points:

### Discover

Candidate/video actions:

- Download
- Download + Analyze
- Add to Library
- Open in Studio

### Library

Import action:

- Upload file
- Paste source URL
- Import existing local file where supported

### Utilities

Keep a standalone **Video Downloader** for one-off use.

### Required behavior

```
source URL
  -> canonical downloader
  -> source-native/best eligible media
  -> canonical local asset
  -> Library
  -> optional Studio action
```

Downloaded media must end up in Library with source lineage. Discover should not
become a second file library.

---

# 5. Library architecture

Library means: **media/artifacts the user actually owns locally**.

It must not contain pure discovery leads.

Target views:

- All media
- Projects
- By source
- By derivative/type
- Search/filter

Core relationship:

```
Source
├─ Localized VN
├─ Smart Cut A
├─ Smart Cut B
├─ Creative Remake
├─ Manual edit
└─ Published derivative
```

The existing Library lineage work is retained and extended rather than replaced.

Important actions from an asset:

- Open
- Open in Studio
- Localize
- Smart Cut
- Creative Remake
- Manual Edit
- Download/save copy
- Publish approved derivative
- Delete

Contextual actions should replace the need to navigate through the Tools hub for
common source workflows.

---

# 6. Studio architecture

Studio becomes the central creation workspace.

```
Studio
├─ Localize
├─ Smart Cut
├─ Creative Remake
└─ Manual Edit
```

The same source/project shell is reused across modes.

Common Studio shell:

- source selector
- source media preview
- canonical transcript/timeline
- project/job identity
- activity/progress
- profile selection
- artifact lineage
- save/resume
- review handoff
- final output

No Phase-D or Phase-E mode may introduce a second app shell or separate media
database.

---

## 6.1 Localize mode

Replaces the fragmented conceptual ownership currently spread across:

- Translate + Dub
- Localization Batch
- Localization Review
- Localization Expert panel

Single-job Localize steps:

```
1. Source
2. Transcript
3. Translation
4. Voice & Timing
5. Render
6. QC
```

### Source

Show:

- video
- source metadata
- source platform/url
- duration/resolution/codecs
- selected Localization Profile

### Transcript

Show:

- source transcript
- timing
- speakers
- alignment/diarization status
- only necessary expert corrections

### Translation

This is the canonical translation-quality workspace.

Three-column conceptual structure:

```
Source media | Source ↔ Target segments | Translation config
```

Controls:

- C6 translation model
- provider/endpoint inherited from Settings
- style preset
- custom instructions
- glossary
- translate/retranslate selected segments
- issue filtering
- human approval

This step may pause before C7.

### Voice & Timing

Controls:

- C7 rewrite model
- TTS provider/voice
- per-speaker mapping
- preferred/hard rate ceilings
- rewrite attempts
- timing warnings
- expert exceptions

### Render

Controls:

- audio mix policy
- captions
- aspect
- render profile
- final encode

### QC

Show:

- READY / NEEDS_REVIEW / FAILED
- timing/audio/media/translation warnings
- direct route to Review if human action is required

Batch localization reuses the same profile/pipeline but is initiated as a batch
action from Localize/Library instead of appearing as a separate tool concept.

---

# 7. Smart Cut mode — Phase D reserved contract

Phase D plugs into Studio, not into the sidebar as another independent app.

Smart Cut reuses:

- canonical source media
- transcript
- source timeline
- scene/shot intelligence where available
- existing Clip Studio primitives where suitable

Target flow:

```
Source
 -> AI range proposals
 -> human bounded edit/approval
 -> reorder/retime
 -> format/captions
 -> render
 -> QC
```

Smart Cut means source-selection/repackaging.

It must not silently become Creative Remake.

---

# 8. Creative Remake mode — Phase E reserved contract

Creative Remake also plugs into the same Studio shell.

Target flow:

```
Source
 -> transcript + visual scene map
 -> Vietnamese creative beats
 -> source/B-roll/asset mapping
 -> storyboard
 -> HUMAN APPROVAL
 -> automated composition
 -> voice/captions/audio
 -> render
 -> QC
```

This mode may use Stock Video and Motion Graphics as asset providers/panels
rather than retaining those systems as primary top-level navigation forever.

---

# 9. Manual Edit mode

Current Clip Studio becomes the manual/bounded editing workspace inside Studio.

Keep high-value behavior:

- source range selection
- trim/cut
- clip extraction
- timeline
- caption controls
- format/reframe where useful
- virality metadata when relevant

Do not attempt to turn Affiliate2 into a full CapCut clone.

Manual Edit exists for corrections and bounded creator intervention.

---

# 10. Review architecture

Review becomes a top-level exception queue.

It replaces the idea of `Localization Review` being one card inside Tools.

Sources of review items eventually include:

- Localize
- Smart Cut
- Creative Remake
- Publish validation

Example filters:

```
Needs Review
Translation
Timing
Audio
Visual
QC
Publish
```

Each item contains:

- source/project identity
- workflow/mode
- exact blocking/warning reasons
- affected segments/chunks/scenes
- action required
- resume/rerun action

Automatic happy-path jobs should not require manual inspection at every stage.

---

# 11. Publish architecture

Publish becomes the owner of distribution.

Current functionality from:

- channel account plumbing
- uploads
- publish approval
- scheduling
- publishing metadata
- remaining overlay migration

should converge here during Phase F.

Important distinction:

```
Discover -> Channels
```

means **source channels being watched**.

```
Publish -> Accounts
```

means **destination accounts we publish to**.

Do not mix these concepts.

Messaging/integration capabilities should be evaluated as notification/
distribution support rather than remain a permanent unrelated top-level page.

---

# 12. Performance architecture

Performance owns post-publication measurement.

Target capabilities:

- per-publication metrics
- performance deltas
- source/remake/localization lineage
- content pattern comparison
- feedback into Discover/Radar/Intelligence

This closes the Affiliate2 loop:

```
Discover -> Make -> Review -> Publish -> Measure -> Learn
```

---

# 13. Utilities architecture

The existing ViralMint tool catalog remains useful, but no longer defines the
primary product architecture.

Target categories may include:

### Video

- Video Downloader
- Trim / Cut
- Crop
- Compress
- Merge
- Transform
- Speed
- Reframe
- GIF
- Watermark

### Audio

- Voiceover
- Enhance Audio
- Remove Silence
- Music Visualizer

### Text / Captions

- Add Captions
- Export Subtitles
- Metadata
- Auto Chapters
- Hook Analysis

Tools that become native steps inside Studio should be hidden from the default
Utilities catalog only after parity exists.

Do not delete backend implementations just because navigation changes.

---

# 14. Settings architecture

Settings is for reusable/global configuration, not daily production work.

Target groups:

```
General
AI Connections
Localization Profiles
TTS / Voices
Source Providers
Publish Accounts
Storage
Add-ons
System Health
```

Configuration hierarchy:

```
Settings connection/defaults
       ↓
Saved Profile
       ↓
Per-job override
       ↓
Expert segment/chunk override
```

No duplicated provider credentials inside each workflow.

## Localization Profiles

Profiles own reusable production defaults such as:

- ASR quality/model
- alignment/diarization
- C6 translation model
- translation style
- translation instructions
- glossary
- C7 rewrite model
- TTS provider/model/voice
- timing ceilings
- audio mix
- captions
- render quality
- QC policy

Studio exposes relevant profile controls contextually, with advanced overrides
collapsed by default.

---

# 15. Current page migration map

| Current ViralMint/Affiliate2 surface | Target |
|---|---|
| `Chat` | Contextual Assistant; no longer primary IA owner |
| `Scout` | Discover -> Spy |
| `My Channels` | Discover -> Channels |
| channel video download action | canonical Download capability -> Library |
| `Library` | Keep; expand lineage/project actions |
| `Clip Studio` | Studio -> Manual Edit |
| `Translate + Dub` | Studio -> Localize |
| `Localization Batch` | Batch action inside Localize / Library |
| `Localization Review` | Review |
| `Stock Video` | Studio asset source / optional Utilities surface |
| `Motion Graphics` | Studio asset/composition provider |
| `Tools` | Utilities |
| `Messaging` | Phase-F Publish/notification integration decision |
| `Settings` | Keep, reorganize around connections/profiles/accounts |
| plugin routes | evaluated individually; no automatic top-level entitlement |

---

# 16. Migration phases

The UI migration is intentionally incremental.

## UX0 — Freeze + inventory

**No new feature-page proliferation.**

Deliverables:

- this convergence plan
- page/API/component ownership inventory
- KEEP / MOVE / MERGE / HIDE / DEPRECATE classification
- route aliases to preserve bookmarks
- identify technical dependencies per page

Gate:

- every existing top-level route has a target owner

## UX1 — Navigation shell

Implement new top-level navigation:

- Discover
- Library
- Studio
- Review
- Publish
- Performance
- Utilities
- Settings

Requirements:

- old routes still resolve or redirect
- no backend behavior removed
- global Activity remains accessible
- mobile/collapsed navigation works

Do not redesign every page yet.

## UX2 — Discover convergence

Move:

- Scout -> Spy
- My Channels -> Channels
- saved candidates -> Candidates/Saved

Integrate contextual Download actions.

Gate:

```
spy result -> save candidate -> download -> Library
channel video -> download -> Library
candidate -> Studio
```

with explicit lineage.

## UX3 — Studio shell

Build one common project/source workspace and mode selector.

Modes initially:

- Localize
- Manual Edit

Reserved:

- Smart Cut
- Creative Remake

Do not implement D/E algorithms here yet.

Gate:

- same source can switch supported modes without re-importing media
- job/artifact identity remains canonical

## UX4 — Localize convergence

Move current Translate + localization expert UI into Studio Localize.

Implement the six-step workflow:

- Source
- Transcript
- Translation
- Voice & Timing
- Render
- QC

Move Batch into contextual batch action.

Gate:

- translation can be reviewed before C7
- profile settings have one source of truth
- existing C4-C13 backend invariants remain intact

## UX5 — Review convergence

Move localization exceptions into top-level Review.

Introduce generic review-item envelope so later D/E/F workflows can add their
own exception types without creating new review apps.

Gate:

- QC exception -> Review -> fix -> rerun affected stage -> resolved

## UX6 — Utilities cleanup

Reclassify existing Tools.

For every utility:

- keep standalone
- also expose as contextual action
- hide duplicate card after contextual parity
- deprecate only after evidence

Gate:

- no useful ViralMint function is lost
- no duplicate workflow owner remains

## UX7 — Settings cleanup

Reorganize provider/profile/account configuration.

Remove production-only clutter from Settings.

Gate:

- connection credentials exist once
- profile defaults are reusable
- Studio overrides do not mutate global config accidentally

## UX8 — Phase-D readiness

Before implementing Smart Cut:

- Studio shell stable
- Library lineage stable
- Review generic enough for Smart Cut exceptions
- source/transcript/timeline reusable
- no new top-level route required for Phase D

Then start Phase D inside `Studio -> Smart Cut`.

## UX9 — Phase-E readiness

Before Creative Remake:

- shared Studio project shell supports external assets
- storyboard review surface exists
- Stock/Motion asset providers accessible contextually
- Review supports creative/storyboard exceptions

Then Phase E plugs into `Studio -> Creative Remake`.

## UX10 — Phase-F native Publish convergence

Migrate remaining publish/performance overlay behavior into the new IA.

Delete transitional routes only after parity.

---

# 17. Explicit non-goals

This convergence does NOT mean:

- rewrite the whole frontend in one PR
- replace Material UI
- replace React/Vite
- create another editor framework
- migrate databases during UI work
- remove all ViralMint utilities
- implement Phase D/E before their backend phases
- build a CapCut clone
- make every expert setting permanently visible

---

# 18. Acceptance criteria

UI convergence is complete when:

1. primary sidebar is workflow-first;
2. Discover owns Spy + source Channels + Candidates;
3. Download is contextual/canonical and downloaded assets enter Library;
4. Library owns local media + lineage;
5. Studio owns Localize / Smart Cut / Creative Remake / Manual Edit;
6. Review is one generic exception queue;
7. Publish and Performance have clear reserved ownership;
8. Utilities retains one-off ViralMint tools without defining product IA;
9. Settings contains reusable configuration only;
10. configuration hierarchy is Settings -> Profile -> Job -> Expert override;
11. old routes are redirected or intentionally retained during migration;
12. Phase D and E require no new top-level app shell;
13. every migrated phase leaves Affiliate2 runnable.

---

# 19. Recommended implementation order relative to current roadmap

Current priority:

```
C13 translation-quality hardening
        ↓
UX0–UX5 UI/UX convergence core
        ↓
finish representative C13 acceptance through the new Localize/Review workflow
        ↓
Phase D Smart Cut inside Studio
        ↓
UX6–UX8 cleanup/readiness
        ↓
Phase E Creative Remake inside Studio
        ↓
Phase F Publish / Performance convergence
        ↓
Phase G DB convergence / dead-path cleanup
```

Why:

- backend Phase C is already substantial and technically proven;
- translation perceptual QA exposed that review/config placement matters;
- Smart Cut should not be built into the old tool-centric UI and then migrated
  again;
- the Studio/Review/Library contracts should stabilize before Phase D/E add more
  workflow states.

---

# 20. First implementation milestone

The first coding milestone after plan approval is **UX1 + UX2 shell work only**.

It should:

1. introduce the new sidebar structure;
2. create Discover parent workspace;
3. move/alias Scout as Spy;
4. move/alias Channels under Discover;
5. preserve existing APIs and page internals initially;
6. keep old routes as redirects;
7. leave Library/Studio/Review links pointing to current pages until their
   convergence phase begins.

This gives Affiliate2 the correct product map first without requiring a risky
big-bang page rewrite.
