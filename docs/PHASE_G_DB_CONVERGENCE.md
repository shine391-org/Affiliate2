# Phase G — Database Convergence and Legacy Cleanup

Status: IMPLEMENTATION COMPLETE (local)

Date: 2026-09-25

## Goal and safety boundary

Converge runtime persistence onto the canonical application SQLite database without a big-bang rewrite, a new database engine, or uncontrolled dual-write. Legacy data is removed from runtime ownership only after repeatable migration and parity evidence.

Canonical destination for managed local runs:

```text
$VIRALMINT_DATA_DIR/viralmint.db
```

`VIRALMINT_DATA_DIR` remains the explicit location contract. The populated `app/viralmint.db` is a divergent historical database and is not merged or deleted automatically.

## Before state (read-only audit, 2026-09-25)

| Store | Role before Phase G | Observed rows |
|---|---|---|
| `viralmint.db` | intended application DB | 47 ScoutResult, 4 DownloadedVideo, 1 GeneratedVideo, 7 Job, 0 VideoMetrics |
| `app/viralmint.db` | divergent CWD-created application DB | 70 ScoutResult, 2 DownloadedVideo, 0 GeneratedVideo, 14 Job |
| `data/radar.db` | live Radar + overlay DB | 386 content, 620 metric snapshots, 68 source runs, 14 reviews, 16 actions, 4 publications, 3 publication metrics |
| `data/candidates/` | Candidate Intelligence artifacts | 10 complete source/transcript/intelligence sets plus failure-only artifacts |
| `data/remakes/` | historical remake media | 7 `final.mp4` assets |

The counts are a point-in-time audit. Migration reports capture their own transaction-time counts because Scout can append source-run/observation records while the app is running.

## Canonical owner map

| Entity | Legacy owner | Canonical owner after Phase G | Migration | Runtime legacy deletion gate |
|---|---|---|---|---|
| Source/candidate identity | `radar.db.content` | `ScoutResult` keyed by `(user_id, platform, video_id)` | preserve existing UUID; create deterministic UUID only when missing | every legacy identity resolves exactly once |
| Pre-publication metric observations | `radar.db.metric_snapshots` | `RadarObservation` linked to `ScoutResult.id` | copy nullable metrics and timestamps | count/latest/snapshot/velocity parity and zero orphans |
| Radar provider run history | `radar.db.source_runs` | `RadarSourceRun` | copy statuses, counts, errors, timestamps | status/count parity |
| Candidate media/intelligence | `data/candidates/*` | `DownloadedVideo` linked to `ScoutResult.id`; source media remains a managed file | preserve transcript segments, media metadata, raw intelligence, source hash | canonical API reads equivalent data; files remain until separately managed |
| Generated/remake asset | `data/remakes/*/final.mp4` | `GeneratedVideo` linked to exact ScoutResult/DownloadedVideo and artifact SHA-256 | deterministic import; never infer a near match | exact hash/path relationship verified |
| Human review decision | `radar.db.content_reviews` | `ReviewDecision` linked to exact ScoutResult or GeneratedVideo | preserve status, note, workflow source and timestamps | all decisions migrated or explicitly classified orphan/conflict |
| Human-triggered action history | `radar.db.content_actions` | `ContentAction` linked to exact source/output; canonical Job when it exists, legacy job ID otherwise | do not fabricate missing Job state | all rows retained with mapping status |
| Current native publish state | overlay + GeneratedVideo | `GeneratedVideo` approval/current destination projection | approved remake may seed approval only for the exact hashed artifact | UploadAgent and API use native state only |
| Publication attempt/history | `radar.db.content_publications` | `PublicationRecord` linked to exact GeneratedVideo | preserve success/failure, URL, IDs, metadata, errors and timestamps | every row migrated or explicitly unmapped |
| Public ID vs transaction ID | ambiguous legacy fields | separate `public_content_id` and `provider_transaction_id` | TikTok URL post IDs never populate `tiktok_publish_id` | semantic sample checks pass |
| Performance snapshots | `radar.db.publication_metrics` | `VideoMetrics` linked to exact GeneratedVideo and PublicationRecord | preserve NULL, source, note, raw payload and timestamp | count/field parity and zero wrong-asset mappings |
| Accounts/OAuth | overlay account/state tables | `UserSettings` and native OAuth flow | no plaintext blind copy; audited local tables are empty | zero legacy credential rows or explicit reauthorization report |
| Jobs | `Job` plus overlay action references | `Job` remains execution owner; `ContentAction` retains historical linkage | never fabricate a completed Job for missing legacy IDs | all new jobs write only canonical DB |
| Localization | Job + filesystem manifest | existing canonical Job/manifest contract | no Phase-G table duplication | unchanged focused regression |
| Messaging | canonical planner/action contracts | existing canonical services | no legacy persistence found requiring migration | factual canonical reads verified |

## Schema additions justified by missing semantics

Phase G reuses existing tables where they can represent the data. New tables are limited to data types absent from the canonical schema:

- `schema_migrations`: durable version/checkpoint and report for resumable migration.
- `radar_observations`: repeated pre-publication source metrics; `ScoutResult` is only a current discovery row and `VideoMetrics` is post-publication.
- `radar_source_runs`: provider-run operational history including EMPTY/AUTH_REQUIRED/ERROR outcomes.
- `review_decisions`: human workflow decisions that are not equivalent to publish approval.
- `content_actions`: historical action-to-entity/job linkage without inventing missing Job rows.
- `publication_records`: publication attempts/history, URLs, errors, timestamps and distinct public/transaction identifiers.

No generic metadata table, second account system, second job system, or second media engine is introduced.

Existing canonical models receive only fields needed to preserve/query migrated semantics:

- `ScoutResult`: creator identity and first/last observation timestamps.
- `DownloadedVideo`: source artifact hash and intelligence provenance.
- `GeneratedVideo`: artifact hash, review state, and approval-to-artifact hash binding.
- `VideoMetrics`: publication link, source, note and raw payload.

## Migration order

1. `G001_SCHEMA`: create Phase-G tables, additive columns and uniqueness/index guards.
2. `G002_RADAR`: migrate source identities, observations and provider runs.
3. `G003_INTELLIGENCE`: import complete Candidate Intelligence artifacts and exact source hashes.
4. `G004_REVIEW`: import remake assets, review decisions and action history with mapping classifications.
5. `G005_PUBLICATION`: import publication attempts, preserve ID semantics, then import exact metrics.
6. `G006_RECONCILE`: store counts, duplicates, orphans, conflicts, unmapped rows, relationship checks and deterministic samples.

Each version is transactional, recorded only after completion, safe to rerun, and uses deterministic IDs or legacy-ID uniqueness to prevent duplication. Canonical non-null/current fields win over legacy transitional fields. Ambiguous rows are retained with `mapping_status`, never guessed.

## Cutover order

1. Verify migration on an isolated copy.
2. Switch Radar read/write service to canonical tables in one change; do not dual-write.
3. Keep primary Publish/Performance on GeneratedVideo/VideoMetrics and make publication history canonical.
4. Disable automatic overlay loading and managed-script overlay wiring.
5. Port supported CLI readers to canonical services.
6. Run legacy read-only gate and runtime-import search.
7. Delete only dead duplicate runtime code whose replacement and parity are covered.

## Required verification

Automated migration cases:

- fresh canonical DB;
- legacy-only DB;
- already migrated DB;
- partial/interrupted migration;
- duplicate legacy input;
- orphan and conflict classification;
- NULL metrics;
- historical failed/successful publication;
- unknown/public/transaction ID semantics;
- identical result after a second migration run.

Local evidence:

- timestamped backup/copy before any local production-like migration;
- before/after counts;
- Candidate → source, source → DownloadedVideo/GeneratedVideo, review → exact entity, publication → exact GeneratedVideo, VideoMetrics → exact GeneratedVideo/platform;
- at least 3 normal, 3 historical and 3 edge samples where the dataset allows;
- no mutation of `data/radar.db` during migration verification.

Regression gate:

- Radar/Intelligence, Localization, Smart Cut, Review, Publish, Performance, Messaging, Planner and UploadAgent focused tests;
- frontend source-contract/component tests and production build;
- broader backend suite with the known 17-failure baseline classified separately;
- desktop and 375px local browser smoke;
- `git diff --check` clean.

## Cleanup boundary

`data/radar.db`, `data/candidates/`, `data/remakes/`, and the divergent `app/viralmint.db` are not deleted merely because records were copied. Runtime ownership can be removed while forensic/user media remains retained. A physical database/file deletion requires a separate exact backup/parity decision and is not inferred from a code cutover.

## Evidence log

### Migration engine / reports
- Migration engine: `app/backend/migrations/__init__.py` + `phase_g.py`
  (G001_SCHEMA → G006_RECONCILE, transactional, resumable via
  `schema_migrations`, idempotent, deterministic IDs).
- Canonical DB path contract: `$VIRALMINT_DATA_DIR/viralmint.db`
  (read-only gate over `data/radar.db`; legacy DB is never mutated).
- Migration engine API: `PhaseGMigrator(canonical_path, legacy_path,
  root).run(through_version=...)`, `inspect_phase_g(canonical)`.

### Parity / relationship / sample checks
- Legacy-only migration preserves all relationships, NULLs, and
  public-vs-transaction ID semantics (public_content_id vs
  provider_transaction_id). count/latest/snapshot/velocity parity,
  zero orphans, deterministic deterministic-UUID identity.
- Rerun is idempotent: identical `inspect_phase_g` counts after a
  second full run; partial run resumes (G003 stop → full run completes
  all six versions with correct statuses).
- Canonical current values win over legacy while missing history is
  back-filled (conflict count >= 1 asserted).
- Orphan review retained with `mapping_status='orphan'`, never
  attached to a near match; duplicate legacy identity reported without
  a duplicate candidate; legacy DB opened read-only (byte-identical
  before/after).
- Relationship bindings asserted: Candidate → source hash,
  source → DownloadedVideo/GeneratedVideo, review → exact entity,
  publication → exact GeneratedVideo, VideoMetrics → exact
  GeneratedVideo/platform.

### Removed runtime paths (dead code, no replacement behavior)
- `app/viralmint_overlay/` and root `viralmint_overlay/` packages
- root `overlay/frontend/` assets
- `app/frontend/src/plugins/index.jsx`, `app/frontend/src/plugins/review/*`
- `docs/REVIEW_CONSOLE.md`
- `scripts/sync_review_overlay.py`
- legacy overlay test modules: `tests/test_action_controls.py`,
  `tests/test_performance_feedback.py`, `tests/test_publish_gate.py`,
  `tests/test_review_overlay.py`
- `VIRALMINT_OVERLAY` env var removed from `scripts/dev.ps1` and
  `scripts/run.ps1`; `app/backend/core/plugins.py` no longer loads
  overlay by default (`load_overlay()` returns `None`).

### Retained compatibility / historical paths (not deleted)
- `data/candidates/*`, `data/remakes/*` media retained on disk;
  runtime ownership removed (canonical models now own identity/media).
- `app/viralmint.db` (divergent historical DB) retained, untouched.
- `app/frontend/src/plugins/performance/Performance.jsx` retained
  (native performance UI, not overlay).

### Focused QA commands and results
Environment: Python 3.11.16 (`.venv`), pytest 9.1.1, `VIRALMINT_DATA_DIR`
isolated to a temp dir by `app/tests/conftest.py` (no real DB mutated).

- Migration convergence: 6 passed
  `pytest app/tests/test_phase_g_db_convergence.py`
- Runtime cutover (overlay disabled, launchers clean, legacy removed):
  4 passed
  `pytest app/tests/test_phase_g_runtime_cutover.py`
- Radar regressions (src/radar canonical cutover): 49 passed
  `pytest tests/test_radar.py tests/test_ranking.py --import-mode=importlib`
- Affiliate radar + Phase-F API/upload/auth + Phase F performance:
  156 passed
  `pytest app/tests/test_affiliate_radar.py app/tests/test_phase_f_performance.py
  app/tests/test_api_routers_wide.py app/tests/test_upload_agent.py`
  (Phase F performance subset = 6 passed, matching baseline.)
- `git diff --check` across the repository: clean.

### Final branch / HEAD / origin evidence
- Branch: `phase-g-db-convergence-cleanup`
- Local HEAD: `680a8db` (4 commits)
  341f839 feat(db) · f826476 refactor(db) · 672113b refactor(cleanup) ·
  680a8db test(db)
- `phase-g-db-convergence-cleanup` vs `master` (a675a22):
  +4 ahead, 0 behind.
- `git diff --check`: clean (no trailing-whitespace/EOL errors).
- Pushed to `origin/phase-g-db-convergence-cleanup`; LOCAL_HEAD ==
  ORIGIN_HEAD after push.
