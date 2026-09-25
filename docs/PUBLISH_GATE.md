# Phase 3.5 — Publish Gate

> **Legacy overlay reference.** Phase F moved primary Publish ownership to the
> canonical app: `GeneratedVideo` + explicit `publish_approved_at` +
> `UploadAgent` + `/publish`. The candidate/remake gate documented below
> remains compatibility evidence until native parity cleanup. Do not extend it
> with new product behavior. See `docs/PHASE_F_CONVERGENCE.md`.

Phase 3.5 turns an **APPROVED** final into an explicit per-platform publishing action.

No platform publish is triggered by approval alone.

```text
REMAKE_REVIEW
    ↓ human
APPROVED
    ↓
Publish Gate
    ├─ TikTok: manual download + copy caption
    ├─ Facebook Page Reel: one-click API publish
    └─ YouTube: one-click API upload after OAuth connect
```

## Why TikTok stays manual

The MVP intentionally does not call TikTok's Content Posting API and does not
use cookie/browser automation for upload. TikTok is treated as a manual
distribution handoff:

1. Download approved `final.mp4`
2. Copy caption
3. Post in TikTok
4. Paste the published TikTok URL back into the Publish Gate
5. Click **Mark posted**

That external URL is stored in `content_publications` for the later
performance-feedback phase.

## Facebook Page Reels

The adapter uses Meta's Page Reels publishing flow:

1. initialize `/{page-id}/video_reels?upload_phase=start`
2. upload the local binary to the returned `upload_url`
3. finish with `upload_phase=finish&video_state=PUBLISHED`

Local configuration:

```env
FACEBOOK_PAGE_ID=...
FACEBOOK_PAGE_ACCESS_TOKEN=...
# Optional; when omitted the app's/default Graph version is used.
FACEBOOK_GRAPH_VERSION=vXX.X
```

The Page token is expected to carry the Page permissions required by Meta for
Page/Reels publishing (including `pages_manage_posts` in the current Meta
reference collection).

The Publish Gate preflights the final as a Reel:
- 9:16
- at least 540x960
- approximately 4–60 seconds

If a full translated video is longer than the Reel window, Facebook publish is
disabled instead of silently changing the final.

## YouTube

YouTube uses OAuth 2.0 with the narrow upload scope:

```text
https://www.googleapis.com/auth/youtube.upload
```

Local configuration:

```env
YOUTUBE_CLIENT_ID=...
YOUTUBE_CLIENT_SECRET=...
# Optional:
YOUTUBE_REDIRECT_URI=http://localhost:16888/api/publish/youtube/callback
```

The Review Queue shows **Connect YouTube**. OAuth credentials/refresh tokens
are stored only in local `data/radar.db`, which is already ignored by Git.

Before upload the operator chooses:
- privacy: private / unlisted / public
- audience: made for kids / not made for kids

The UI requires a final confirmation click before starting the upload.

Important: Google documents that uploads from unverified API projects created
after 2020 can be restricted to private until the API project completes an
audit. The UI surfaces that warning instead of pretending a requested public
upload is guaranteed public.

## Persistence

Local SQLite tables:

- `publish_accounts` — local OAuth credential state (currently YouTube)
- `publish_oauth_states` — short-lived OAuth CSRF state
- `content_publications` — every publish attempt/manual-post record

No access token, refresh token, Page token, API key, MP4, or database is
committed to Git.

## Human gate

API publish is rejected unless:

- `final.mp4` exists; and
- the rendered-review state is exactly `APPROVED`.

Human approval and publishing are deliberately separate actions.

The UI also asks for confirmation immediately before Facebook/YouTube API
publish.

## Publishing jobs

Facebook/YouTube publish work reuses ViralMint's existing Job model and heavy
task dispatcher.

The UI polls the normal `/api/jobs/{job_id}` endpoint for progress/terminal
state.

TikTok manual posting creates no background job.

## Metadata

The Publish Gate pre-fills editable metadata from existing Vietnamese artifacts:

1. Creative Remake hook/script, else
2. Translate/Dub Vietnamese segments, else
3. original candidate title.

It does not invent hashtags, claims, or product copy.

## Scope excluded

- TikTok API upload
- cookie/browser TikTok uploader
- scheduler
- automatic publish after approval
- multi-account routing
- Instagram
- post-performance collection (Phase 4)
