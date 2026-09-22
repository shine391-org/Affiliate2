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

### B1 — Provider contract

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

### B2 — Bilibili provider

Use the existing Affiliate2 direct Bilibili implementation as the first app-native provider because it already performs real search and creator-history/WBI behavior.

Port selectively from MediaCrawler only where it is stronger.

Do not copy its WebUI/store/global runtime.

### B3 — Douyin provider

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

### B4 — Radar observation/ranking bridge

Keep Affiliate2's repeated snapshots.

Scout results should be observable by Radar without creating a second discovery dashboard.

Fix known identity/baseline issues while porting:

- content identity is `(platform, content_id)`
- creator identity is `(platform, creator_id)`
- snapshot counts must be platform-scoped
- creator outlier signal compares current content performance against that creator's own historical baseline

### B5 — Scout integration

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
