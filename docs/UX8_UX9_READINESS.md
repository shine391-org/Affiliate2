# UX8 / UX9 — Phase D/E Readiness Contract

Status: **IMPLEMENTED / LOCAL QA PENDING**

This document is the handoff contract between UI/UX convergence and the next
creation phases. It does not implement Smart Cut or Creative Remake.

## Shared Studio source

Every Studio workflow reuses one persisted DownloadedVideo source selected by
`?source=<downloaded_video_id>`.

The detail endpoint now exposes the canonical source transcript fields:

- transcript
- transcript_language
- transcript_source
- transcript_segments

The frontend normalizes transcript segment timing into source milliseconds via
`studioReadinessModel.js`.

No Phase D/E implementation may introduce another source object merely because
it needs transcript/timeline data.

If a selected source has no transcript, later phases may invoke the canonical
ASR service and persist the result on the same source. They must not add another
ASR owner.

## Phase D — Smart Cut contract

Canonical route:

`/studio/smart-cut?source=<downloaded_video_id>`

Reserved flow:

```
source
 -> transcript
 -> range proposals
 -> human approval
 -> format
 -> render
 -> QC
```

Rules:

- source ranges use the canonical source timeline;
- transcript/timing are reused where already persisted;
- transcript end beyond source duration is a blocker, not something to clamp;
- Smart Cut remains source selection/reordering/repackaging;
- output registers one durable Library derivative using producer
  `smart_cut` and source_downloaded_video_id;
- Review categories are Timing / Visual / QC;
- no new top-level Smart Cut app.

## Phase E — Creative Remake contract

Canonical route:

`/studio/remake?source=<downloaded_video_id>`

Reserved flow:

```
source
 -> scene map
 -> creative beats
 -> asset map
 -> storyboard
 -> HUMAN APPROVAL
 -> compose
 -> render
 -> QC
```

Rules:

- the source/project identity remains the same Studio context;
- storyboard approval is mandatory before expensive composition/render;
- Stock / Smart Video and Motion Graphics remain contextual asset providers,
  not new top-level workflow owners;
- generated result registers one Library derivative using producer
  `creative_remake` and source_downloaded_video_id;
- Review categories are Storyboard / Visual / QC;
- no new top-level Remake app.

## Asset providers

Current contextual providers:

- Stock / Smart Video → `/stock?source=<id>`
- Motion Graphics → `/motion?source=<id>`

These are capabilities Phase E may call from Studio. Their current standalone
secondary routes remain available during migration.

## Library lineage

Reserved producer identities now exist on both backend and frontend taxonomy:

- `smart_cut` → video / edited
- `creative_remake` → video / edited

Future Phase D/E writers should use GeneratedVideo.source_type plus
source_downloaded_video_id so existing Library lineage automatically renders:

```
Downloaded source
├─ Smart Cut
└─ Creative Remake
```

No new media table is required for these outputs.

Library's downloaded-video drawer already hands the same source into:

- Localize
- Manual Edit
- Smart Cut (reserved)
- Creative Remake (reserved)

## Review extension contract

Review now has:

- one generic review-item envelope;
- workflow contracts for Localize / Smart Cut / Creative Remake;
- one source registry;
- current live source: localization exceptions;
- reserved source slots for Phase D and E.

Enabled categories:

- Needs Review
- Translation
- Timing
- Audio
- Storyboard
- Visual
- QC

Publish remains disabled until Phase F.

Adding Phase D/E Review data should mean registering the canonical endpoint +
adapter in `reviewSources.js`, not building a second Review page.

Current detail handler:

- Localize → existing LocalizationExpertPanel

Future Phase D/E detail panels plug into the same generic Review surface.

## Non-goals

UX8/UX9 do not:

- generate Smart Cut range proposals;
- create scene detection;
- create storyboard data;
- generate B-roll;
- perform automated composition;
- add DB migrations;
- add a project/media table;
- add a second ASR or Review pipeline;
- claim Phase D/E functional completion.

## QA gate

UX8 readiness passes when:

- selected Studio source resolves through existing DownloadedVideo identity;
- detailed source exposes canonical transcript segments;
- normalized transcript/source timeline is testable and provenance mismatches
  are surfaced;
- Library can hand one downloaded source to Smart Cut without re-import;
- `smart_cut` Library derivative identity is registered;
- Review has a Smart Cut workflow contract without fake live items;
- no new top-level Smart Cut route exists outside Studio.

UX9 readiness passes when:

- Creative Remake reuses the same Studio source context;
- Stock and Motion providers are reachable contextually with source identity;
- storyboard approval is represented as a mandatory future gate;
- Storyboard/Visual filters are real generic Review categories with zero counts
  until real items exist;
- `creative_remake` Library derivative identity is registered;
- no fake remake generation exists;
- no new top-level Remake app exists.
