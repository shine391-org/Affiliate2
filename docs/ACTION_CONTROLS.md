# Phase 3.4 — Review Queue Action Controls

Phase 3.4 connects human review decisions to real production work without
introducing a second dashboard or a second job system.

## Operator flow

```text
REVIEW_PENDING
    ↓ human
APPROVED_FOR_REMAKE
    ↓
┌───────────────────────┐
│ Translate / Dub       │
│ Creative Remake       │
└───────────────────────┘
    ↓ ViralMint job
REMAKE_REVIEW
    ↓ human
APPROVED / REWORK / REJECTED

REWORK
    ↓
RERENDER
    ↓
REMAKE_REVIEW
```

Publishing is still out of scope.

## Translate / Dub

Uses ViralMint services directly:

```text
source.mp4
→ Whisper
→ ViralMint resilient AI translation
→ optional Vietnamese Edge TTS
→ Vietnamese captions
→ output validation
→ final.mp4
```

Modes:

- `full_dub`: Vietnamese voice + Vietnamese captions
- `captions_only`: original audio + Vietnamese captions

Voices:

- `vi-VN-NamMinhNeural`
- `vi-VN-HoaiMyNeural`

This is a faithful translation workflow, not a creative rewrite.

## Creative Remake

Uses the existing Affiliate2 remake pipeline:

```text
intelligence + transcript
→ Gemini Vietnamese adaptation
→ segment plan
→ ViralMint Edge TTS / FFmpeg / captions
→ output validation
→ final.mp4
```

Supported target windows:

- 20–35 seconds
- 35–60 seconds

A human `REWORK` note is passed into the creative adaptation prompt during
rerender so the next version explicitly addresses the feedback.

## Rerender

Rerender repeats the production mode stored in
`data/remakes/<candidate>/action_manifest.json`.

Legacy Phase 3.2 prototypes do not have a manifest, so they default to
`Creative Remake`.

For a prior Translate / Dub output, rerender repeats Translate / Dub and the
operator can change voice or translate mode. The free-text REWORK note is
preserved in the manifest but is not used as an AI rewrite instruction for a
literal translation job.

## Safety / state rules

Production is allowed only when:

- original source media exists;
- a new candidate is `APPROVED_FOR_REMAKE`; or
- an existing final is `REWORK` with a non-empty review note.

Only one pending/running action is allowed per candidate.

A production action never auto-approves its result. Successful output always
returns to `REMAKE_REVIEW`.

## Job model

The action uses ViralMint's existing public `Job` model and heavy-task
dispatcher, so it inherits:

- Activity tracking;
- max-heavy-job concurrency;
- restart stale-job handling;
- cancellation state.

Affiliate2 stores only the job reference in its own `content_actions` table.

## Atomic output promotion

Renders are written to a hidden staging directory first.

Only after:

1. media generation succeeds; and
2. ViralMint output validation passes

is the staged directory promoted to:

```text
data/remakes/<platform>_<content_id>/
```

For rerenders, the currently reviewed final remains available while the new
version is rendering. A failed or cancelled render does not replace it.

Large media remains ignored by Git.

## API

```text
POST /api/review/candidates/{platform}/{content_id}/action
GET  /api/review/candidates/{platform}/{content_id}/action
```

POST body:

```json
{
  "action": "translate | remake | rerender",
  "voice_id": "vi-VN-NamMinhNeural",
  "target_duration": "20-35s",
  "translate_mode": "full_dub"
}
```

Fields irrelevant to the resolved action are ignored.

## UI

Review Queue now exposes:

- **Run Translate / Dub**
- **Run Creative Remake**
- **Rerender**

with job progress/status inline.

The existing Approve / Rework / Reject controls remain the human decision
boundary.
