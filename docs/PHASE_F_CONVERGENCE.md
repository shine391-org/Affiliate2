# Phase F — Publish / Performance / Messaging convergence

Date: 2026-09-25  
Status: F1 NATIVE PUBLISH OWNER WIRED — local QA pending

## Why Phase F exists

Affiliate2 currently has two generations of distribution code:

1. the canonical ViralMint application stack:
   - `GeneratedVideo`
   - `UploadAgent`
   - YouTube/TikTok/Instagram upload services
   - `VideoMetrics`
   - `performance_tracker`
   - MessagingManager
2. an older Affiliate2 overlay stack:
   - candidate/remake-specific Publish Gate
   - `content_publications`
   - overlay OAuth/account store
   - overlay performance snapshots

The overlay was useful for the earlier candidate/remake MVP, but it is not a
good long-term owner after Localization HQ + Smart Cut became canonical app
workflows and Creative Remake was deferred.

Phase F therefore converges product ownership onto the native app. It does not
delete compatibility paths until parity/evidence exists.

## F1 decision — canonical Publish owner

Canonical publish identity is now the exact `GeneratedVideo.id` the user sees
in Library/Studio.

Flow:

```
GeneratedVideo
  -> explicit human publish approval
  -> separate destination action + confirmation
  -> existing UploadAgent
  -> existing platform uploader
  -> GeneratedVideo platform IDs / uploaded_platforms
```

Approval is persisted in `GeneratedVideo.publish_approved_at`.

Important rules:

- approval never triggers an upload;
- an upload endpoint rejects unapproved videos;
- only finished `ready` / `uploaded` outputs can be approved;
- account credentials remain owned by Settings/UserSettings;
- TikTok discovery/browser sessions remain independent from upload credentials;
- the existing UploadAgent remains the upload orchestrator;
- no overlay candidate/remake ID is required by native Publish.

The primary `/publish` UI now lists native finished outputs, previews the exact
file, records/revokes approval and publishes via the existing YouTube/TikTok
connections.

## Transitional capabilities

Not all historical overlay functionality is migrated in F1:

- Facebook Page Reel adapter remains overlay-only;
- Instagram uploader exists natively but is not yet surfaced in the primary
  Publish UI;
- legacy overlay publication history remains readable by its old paths;
- overlay performance feedback remains until F2 parity.

The native UI states this honestly instead of displaying unsupported buttons.

## F2 — Performance convergence

Next:

- make native `VideoMetrics` / `performance_tracker` the one primary
  Performance owner;
- retain append-only snapshots;
- preserve YouTube/TikTok metrics where trustworthy;
- add manual snapshot entry where an API signal is unavailable;
- link every metric directly to `GeneratedVideo.id`;
- keep Radar feedback descriptive/human-controlled;
- retire overlay performance storage only after result parity.

## F3 — Messaging / Operations

MessagingManager is retained as transport, not business logic.

Priority channel: Telegram. Existing WhatsApp/Discord/Slack adapters remain
available but do not need equal product polish.

Useful events:

- render complete / failed;
- needs review;
- publish complete / failed;
- account/auth problem;
- performance snapshot / meaningful anomaly.

Inbound messages continue through the same PlannerAgent. No channel gets its own
pipeline, publish policy or duplicated command engine.

## F4 — End-to-end affiliate loop

Target:

```
Discover
 -> candidate
 -> source
 -> Localize / Smart Cut
 -> human review
 -> human publish approval
 -> publish
 -> performance evidence
 -> candidate/Radar feedback
 -> operator notifications
```

## F1 QA gate

1. database upgrade adds `publish_approved_at` without drift;
2. `/api/videos` and `/api/videos/{id}` expose approval state;
3. unapproved upload returns 403;
4. approval does not create a job;
5. approved video can queue the existing UploadAgent;
6. revoke closes the gate again;
7. unknown publish platform is rejected;
8. `/publish` desktop + ~375px works;
9. YouTube/TikTok connection state comes from Settings;
10. no `/api/publish/candidates/*` dependency remains in primary Publish UI;
11. existing uploader tests remain green.
