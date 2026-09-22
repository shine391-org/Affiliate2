# Phase 4 — Performance Feedback Loop

Phase 4 closes the MVP loop by storing real post-publication outcomes and
feeding that evidence back into content decisions.

```text
Discovery
→ Radar
→ Intelligence
→ Human Review
→ Translate / Remake
→ Human Review
→ Publish Gate
→ Published Post
→ Performance Metrics
→ Observed Pattern Feedback
```

This phase deliberately **does not auto-change Radar weights**. Early samples
are too small to justify automatic optimization.

## Metric snapshots

Each published post can have multiple append-only metric snapshots:

- views
- likes
- comments
- shares
- collected_at
- source
- optional note

Storage:

```text
publication_metrics
```

Supported sources:

- `manual`
- `youtube_api`

Manual snapshots are supported for every distribution platform. That keeps
TikTok usable without a TikTok publishing/analytics integration and avoids
binding Facebook learning to a changing Insights permission surface.

## YouTube automatic sync

For a YouTube publication with a stored video ID, **Sync YouTube** calls the
official YouTube Data API `videos.list(part=statistics)`.

The snapshot records:

- viewCount → views
- likeCount → likes
- commentCount → comments
- shares → unavailable / NULL

The existing connected YouTube OAuth credentials from Phase 3.5 are reused.

YouTube changed Shorts view counting starting 2026-08-24, so comparisons across
that boundary must be interpreted with that platform-definition change in mind.

## TikTok and Facebook

For MVP:

- TikTok metrics: manual snapshot
- Facebook metrics: manual snapshot
- YouTube metrics: manual or API sync

This is intentional. The feedback loop needs trustworthy outcome data before it
needs more automation.

## Derived metrics

For each publication:

```text
engagement_count = likes + comments + shares
engagement_rate  = engagement_count / views
view_velocity/h  = (latest_views - first_views) / elapsed_hours
```

Unavailable signals stay unavailable; they are not converted to fake zeros.

## Observed patterns

The Performance page joins published outcomes back to the candidate
intelligence and summarizes observed evidence by:

- distribution platform
- topic
- content format
- hook type

Each pattern includes:

- sample count
- average views
- average engagement rate
- average view velocity/hour

These are descriptive observations, not automatic recommendations.

## UI

ViralMint receives a new navigation item:

```text
Performance
```

The page shows:

- published post count
- posts with metrics
- per-platform measurement coverage
- observed topic/format/hook patterns
- latest metrics per post
- engagement rate
- view velocity
- metric snapshot count
- manual metric editor
- YouTube Sync button for YouTube posts
- external post link

## Radar CLI

Export the same feedback evidence:

```powershell
python radar.py performance --format json
python radar.py performance --format markdown
python radar.py performance --format json --output data/performance_feedback.json
```

This output is the explicit bridge back to Radar/research decisions.

## Learning policy

Phase 4 does not modify `src/radar/ranking.py`.

Reason:

- the first distribution sample will be small;
- platforms define views/engagement differently;
- automatic weight tuning would overfit quickly.

The project should accumulate real post outcomes first. A future change to Radar
weights should be based on enough evidence and separately reviewed.

## MVP completion condition

The content engine MVP is complete when a local QA run proves:

1. source discovery/ranking still works;
2. Review Queue still works;
3. Translate/Dub works;
4. Creative Remake works;
5. rerender/cancel/failure preservation works;
6. TikTok manual handoff works;
7. Facebook API publish works on a test Page;
8. YouTube OAuth + private upload works;
9. post metrics can be recorded;
10. YouTube metrics can be synced;
11. Performance page and Radar performance export agree;
12. no secrets/media/runtime DB are committed.

After this point, scheduler, multi-account automation, affiliate matching and
revenue attribution are product expansion, not MVP blockers.
