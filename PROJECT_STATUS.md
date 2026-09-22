# PROJECT_STATUS.md

## Overall status: CONSOLIDATION IN PROGRESS

The previous MVP phases produced useful functionality, but the architecture accumulated duplicate plumbing between Affiliate2, ViralMint and MediaCrawler. A full code audit on the synchronized workspace concluded that ViralMint should become the single application foundation and Affiliate2 should retain only its differentiated domain layers.

## Source baselines

- Immutable workspace snapshot: `workspace-sync-2026-09`
- Snapshot commit: `15a6800f4ea78d0809e2400ec39f308910d36360`
- Active Phase A branch: `consolidation-phase-a-app-baseline`
- Canonical runnable source during Phase A: `app/`
- ViralMint forensic reference: `vendor/viralmint/`
- MediaCrawler donor/reference: `vendor/mediacrawler/`

## Consolidation Phase A — Application baseline

Status: **IMPLEMENTATION IN PROGRESS**

Scope:

- Promote the captured ViralMint working tree to `app/`.
- Preserve behavior; no product refactor in this phase.
- Keep legacy Affiliate2 modules reachable through the existing overlay/import seams.
- Add standard project/agent documentation.
- Add Windows-native bootstrap/dev/stable/stop launch scripts.
- Establish `app/` as the only place new application-runtime changes should land.
- Keep `vendor/*` immutable.

QA contract: `docs/QA_PHASE_A.md`.

## Audited target ownership

**Reuse ViralMint:** application shell, jobs/WebSocket progress, Library, downloader/yt-dlp, Whisper, FFmpeg, captions, TTS abstraction, Clip Studio, common upload and metric-collection plumbing.

**Keep/port Affiliate2:** repeated Radar metric snapshots, ranking/learning, Vietnamese localization quality, domain intelligence, human review semantics, publish approval gate, performance intelligence.

**Port selectively from MediaCrawler:** real Douyin platform client/auth/CDP behavior and Bilibili capabilities where stronger than the current direct provider. Do not retain MediaCrawler as a second runtime application.

## Known architectural debt to remove in later phases

- Douyin subprocess → JSONL → Radar bridge.
- Custom Affiliate2 downloader in `src/radar/intel/media.py`.
- Duplicate faster-whisper wrapper in `src/radar/intel/transcribe.py`.
- Direct provider HTTP plumbing in Affiliate2 intelligence.
- Legacy overlay sync/copy architecture.
- Separate raw-sqlite review/publish/performance store after native DB migration.
- Duplicate YouTube upload/metrics collection.
- Legacy Creative Remake timestamp semantics.

## Critical known product gap

Legacy Creative Remake is not considered production-quality:

- prompt sees only the first 30 transcript segments,
- generated `reuse_plan.source_segments` / reorder intent is not consumed,
- output section timestamps are incorrectly reused as source-video cut timestamps,
- fallback may use the entire source instead of failing a bad creative plan.

Do not extend this legacy behavior. It will be superseded by Creative Remake HQ after the foundation migrations.

## Previous phase history

Phase 1 OSS validation, Phase 2 Radar, Phase 3.1 intelligence, Phase 3.2 remake pilot and Phase 3.3 human review were completed. Phase 3.4/3.5/4 functionality exists in the synchronized branch but is being consolidated before final end-to-end QA/merge.

The historical implementation remains recoverable from Git history and the immutable workspace snapshot.
