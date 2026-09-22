# Phase 3.3 — Human Review Console

## Architecture

The review console uses ViralMint's documented overlay seam instead of forking or
editing the tracked OSS application.

```text
Affiliate2 (source of truth)
├─ viralmint_overlay/                  # backend FastAPI plugin
├─ overlay/frontend/                   # frontend Review Queue plugin
├─ scripts/sync_review_overlay.py      # stages overlay into ignored ViralMint clone
├─ data/candidate_intelligence.json    # analyzed candidates
├─ data/candidates/*                   # original media + transcript + intelligence
├─ data/remakes/*                      # adaptation + final.mp4
└─ data/radar.db                       # Radar data + content_reviews table

tools/ViralMint/                       # ignored upstream clone
└─ receives staged overlay at runtime/build time
```

This keeps ViralMint as the media workshop while Affiliate2 owns Radar,
intelligence, remakes, and human decisions.

## Review checkpoints

### Candidate review
- `REVIEW_PENDING`
- `APPROVED_FOR_REMAKE`
- `REJECTED`

### Rendered remake review
- `REMAKE_REVIEW`
- `APPROVED`
- `REWORK`
- `REJECTED`

`REWORK` requires a note. Review decisions are stored in the local
`data/radar.db` table `content_reviews` and survive restart.

The current three Phase 3.2 renders are seeded as `REMAKE_REVIEW`; the other
analyzed candidates are seeded as `REVIEW_PENDING`. Existing decisions are
never overwritten by seeding.

## Install/sync into local ViralMint

From the Affiliate2 repository root:

```powershell
.venv\Scripts\python.exe scripts\sync_review_overlay.py --build
```

The sync script refuses to overwrite a non-standard ViralMint plugin registry
unless `--force` is explicitly supplied.

Then start ViralMint normally:

```powershell
$env:VIRALMINT_NO_BROWSER="1"
.venv\Scripts\python.exe tools\ViralMint\run.py
```

Open:

```text
http://localhost:16888/review
```

## API

- `GET /api/review/candidates`
- `GET /api/review/candidates/{platform}/{content_id}`
- `POST /api/review/candidates/{platform}/{content_id}/decision`
- `GET /api/review/media/source/{platform}/{content_id}`
- `GET /api/review/media/final/{platform}/{content_id}`

Local media routes resolve only known platform/content identifiers and verify
that the resolved path remains inside `data/candidates` or `data/remakes`.

## Scope

This phase intentionally does **not** implement:
- automatic remake after approval
- automatic rerender after REWORK
- publishing
- scheduling
- platform credentials
- roles/auth
- analytics dashboards
- Redis/Postgres/WebSockets

The UI is an operator checkpoint, not a new orchestration platform.
