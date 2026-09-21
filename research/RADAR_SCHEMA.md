# RADAR_SCHEMA.md — Radar Ingestion Schema (Phase 2.1)

## Overview

Radar is a thin ingestion layer that normalizes content metadata from multiple source platforms into a unified SQLite database. It is **not** a ranking engine — it is purely a structured storage layer for raw discovery data.

```
crawler (MediaCrawler)
  → platform adapter (douyin.py / bilibili.py)
  → normalize.py
  → ingest.py
  → data/radar.db (SQLite)
```

---

## Database: `data/radar.db`

SQLite with WAL mode. Three tables.

---

## Table: `content`

Deduplicated content records. One row per `(platform, content_id)`.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto |
| `platform` | TEXT | `douyin` \| `bilibili` |
| `content_id` | TEXT | Platform native ID (aweme_id / video_id) |
| `canonical_url` | TEXT | Full URL to video |
| `creator_id` | TEXT | Platform user ID (anonymized hash where available) |
| `creator_name` | TEXT | Display name (may be partial-masked by MediaCrawler) |
| `title` | TEXT | Video title / desc |
| `description` | TEXT | Full description |
| `publish_time` | INTEGER | Unix timestamp of original publish |
| `source_keyword` | TEXT | Keyword that surfaced this content |
| `first_seen_at` | TEXT | ISO-8601 UTC — first time ingested |
| `last_seen_at` | TEXT | ISO-8601 UTC — most recent scan that found it |
| `created_at` | TEXT | Row creation time |
| `updated_at` | TEXT | Row last update time |

**Unique constraint:** `(platform, content_id)` — UPSERT on conflict.

---

## Table: `metric_snapshots`

Immutable engagement observations. One row per `(content_id, captured_at)` scan.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto |
| `platform` | TEXT | |
| `content_id` | TEXT | FK → content.content_id |
| `captured_at` | TEXT | ISO-8601 UTC snapshot time |
| `views` | INTEGER | NULL if platform doesn't return it |
| `likes` | INTEGER | NULL if unavailable |
| `comments` | INTEGER | NULL if unavailable |
| `shares` | INTEGER | NULL if unavailable |
| `favorites` | INTEGER | NULL if unavailable |

> **NULL policy:** A missing metric is stored as `NULL`, never as `0`. `0` means the field was present and equal to zero; `NULL` means the source did not provide it.

### Platform-specific notes

| Platform | views | likes | comments | shares | favorites |
|----------|-------|-------|----------|--------|-----------|
| Douyin | **NULL** (search API doesn't return play count) | liked_count | comment_count | share_count | collected_count |
| Bilibili | video_play_count | liked_count | video_danmaku | share_count (if present) | collect_count |

---

## Table: `source_runs`

One row per crawl invocation. Tracks lifecycle and result counts.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto |
| `platform` | TEXT | |
| `keyword` | TEXT | Search keyword used |
| `started_at` | TEXT | ISO-8601 UTC |
| `finished_at` | TEXT | ISO-8601 UTC (NULL while RUNNING) |
| `status` | TEXT | `SUCCESS` \| `EMPTY` \| `AUTH_REQUIRED` \| `ERROR` \| `RUNNING` |
| `records_raw` | INTEGER | Lines read from MediaCrawler JSONL |
| `records_normalized` | INTEGER | Records successfully parsed |
| `records_new` | INTEGER | Net new content rows created |
| `records_updated` | INTEGER | Existing content rows refreshed |
| `error` | TEXT | Error message if status=ERROR |

---

## Field Mappings

### Douyin → Radar

| MediaCrawler JSONL field | Radar field | Notes |
|--------------------------|-------------|-------|
| `aweme_id` | `content_id` | |
| `aweme_url` | `canonical_url` | |
| `creator_hash` | `creator_id` | Anonymized by MediaCrawler |
| `nickname` | `creator_name` | Partial-masked |
| `create_time` | `publish_time` | Unix ts |
| `title` / `desc` | `title` / `description` | |
| `liked_count` | `likes` | String → int |
| `comment_count` | `comments` | |
| `share_count` | `shares` | |
| `collected_count` | `favorites` | |
| *(absent)* | `views` | Always NULL |

### Bilibili → Radar

| MediaCrawler JSONL field | Radar field | Notes |
|--------------------------|-------------|-------|
| `video_id` | `content_id` | |
| `video_url` | `canonical_url` | |
| `user_id` / `uid` | `creator_id` | |
| `nickname` | `creator_name` | |
| `create_time` | `publish_time` | Unix ts |
| `title` | `title` | |
| `desc` | `description` | |
| `video_play_count` | `views` | |
| `liked_count` | `likes` | |
| `video_danmaku` | `comments` | Danmaku = primary comment metric |
| `share_count` | `shares` | May be NULL |
| `collect_count` | `favorites` | |

---

## CLI

```bash
# Scan Douyin keyword
python radar.py scan --platform douyin --keyword "减肥食谱"

# Scan Bilibili keyword
python radar.py scan --platform bilibili --keyword "减肥食谱"

# Show DB statistics
python radar.py stats
```

### Output format (per scan)

```
  platform  : douyin
  keyword   : 减肥食谱
  status    : SUCCESS
  raw       : 14
  normalized: 14
  new       : 14
  updated   : 0
  snapshots : 14
```

---

## Architecture Decision

| Component | Decision | Rationale |
|-----------|----------|-----------|
| Crawler | `REUSE` MediaCrawler | Already proven in Phase 1 |
| Douyin auth | Manual CDP session | WAF + login gate requires real browser session |
| Bilibili auth | None needed | Public search API works without login |
| Storage | SQLite WAL | Sufficient for Phase 2.1; no PostgreSQL/Redis needed |
| Ranking | Not built | Phase 2.2 concern |
| Dashboard | Not built | Phase 2.2+ concern |
