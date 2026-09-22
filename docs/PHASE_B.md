# Phase B — Sources + Radar Integration

## Goal

Move source discovery and Radar into the canonical `app/` application without changing the rest of the media-production stack.

Phase B is complete when:

- Scout can call real platform providers from the canonical app.
- Douyin no longer requires Affiliate2 to launch the whole MediaCrawler application as a subprocess and parse JSONL.
- Bilibili discovery is real Bilibili discovery; no silent YouTube substitution.
- Radar keeps repeated metric observations and candidate ranking.
- Scout remains the user-facing discovery surface.
- No second WebUI, second job system, or MediaCrawler runtime app is introduced.

## Scope

### B1 — Provider contract — IMPLEMENTED, QA PENDING

Create a small app-native provider boundary.

Provider responsibilities:

- search by keyword
- optionally fetch creator history
- optionally resolve detail/media metadata
- return platform-identifiable source records
- return explicit status: success / empty / auth-required / error

Rules:

- requested platform identity must be preserved
- no silent cross-platform fallback
- platform auth/session behavior stays inside the provider
- provider code must not own UI, DB schema, or job orchestration

### B2 — Bilibili provider — IMPLEMENTED, QA PENDING

Use the existing Affiliate2 direct Bilibili implementation as the first app-native provider because it already performs real search and creator-history/WBI behavior.

Port selectively from MediaCrawler only where it is stronger.

Do not copy its WebUI/store/global runtime.

### B3 — Douyin provider — IMPLEMENTED, QA PENDING

Port only the platform-specific pieces required from the captured MediaCrawler implementation:

- CDP/session behavior
- authenticated search
- detail/creator calls as needed

Remove the long-term dependency:

```
Affiliate2
→ subprocess MediaCrawler main.py
→ JSONL output
→ re-read JSONL
```

The target is an in-process provider callable by the application.

### B4 — Radar observation/ranking bridge — IMPLEMENTED, QA PENDING

Keep Affiliate2's repeated snapshots.

Scout results should be observable by Radar without creating a second discovery dashboard.

Fix known identity/baseline issues while porting:

- content identity is `(platform, content_id)`
- creator identity is `(platform, creator_id)`
- snapshot counts must be platform-scoped
- creator outlier signal compares current content performance against that creator's own historical baseline

### B5 — Scout integration — IMPLEMENTED, QA PENDING

Scout is the visible discovery surface.

Integrate provider/Radar data into the existing Scout result model/UI with minimal additional fields such as:

- Radar score
- confidence
- observed velocity when enough snapshots exist
- creator-relative outlier signal

Do not create a separate Radar dashboard.

## Explicitly out of scope

- downloader replacement
- Whisper migration
- intelligence merge
- Translate/Dub HQ
- Creative Remake HQ
- Review/Publish/Performance migration
- database convergence
- deleting legacy modules before parity is proven

## Migration strategy

Keep the app runnable after every commit.

Port one platform path at a time and preserve a fallback to the legacy adapter only during the transition when explicitly documented. Do not silently hide provider failures.

## QA policy

Use tiered tests.

Phase-B inner-loop QA should run only affected provider/Radar tests plus focused Scout/API smoke checks.

Full project suite is milestone/CI-only.


## Current checkpoint

Phase B implementation is complete and awaiting one combined focused QA pass.

Implemented:

- app-native provider contract and registry
- explicit SUCCESS / EMPTY / AUTH_REQUIRED / ERROR provider status
- real Bilibili keyword search
- Bilibili creator-history path with WBI signing
- real Douyin keyword discovery through the user's existing CDP browser session
- no canonical MediaCrawler subprocess / JSONL integration
- no native-provider cross-platform rescue: Douyin/Bilibili failures remain explicit
- platform names normalized at the Scout boundary
- app-native Radar observation service using the transitional radar.db
- content identity scoped by (platform, content_id)
- creator identity scoped by (platform, creator_id)
- snapshot counts scoped by platform + content
- observed velocity from repeated snapshots
- creator outlier compares the current content with that creator's own same-platform historical median
- Radar score/confidence/velocity/creator-outlier enrichment in Scout API and live result payload
- Radar signal chips/column in Scout UI
- focused tests added for Bilibili, Douyin and Radar behavior

Phase B intentionally still does NOT:
- replace the downloader
- migrate Radar into the ViralMint application DB
- remove legacy source files before parity is proven
- change Translate/Remake/Review/Publish/Performance behavior

One combined Phase-B QA pass should now validate all B1–B5 together.
