#!/usr/bin/env python3
"""
radar.py — CLI entrypoint for Radar (Phase 2.1 + 2.2)

Usage:
    python radar.py scan --platform douyin  --keyword "减肥食谱"
    python radar.py scan --platform bilibili --keyword "减肥食谱"
    python radar.py stats
    python radar.py top --limit 20
    python radar.py top --platform douyin --limit 20
    python radar.py top --format json
    python radar.py top --format markdown --output data/radar_top20.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure src/ is on the path when run from project root
sys.path.insert(0, str(Path(__file__).parent / "src"))

from radar.ingest import ingest
from radar.ranking import CandidateScore, rank_candidates
from radar.sources import bilibili as bili_source
from radar.sources import douyin as dy_source


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_result(result) -> None:
    print()
    print(f"  platform  : {result.platform}")
    print(f"  keyword   : {result.keyword}")
    print(f"  status    : {result.status}")
    print(f"  raw       : {result.records_raw}")
    print(f"  normalized: {result.records_normalized}")
    print(f"  new       : {result.records_new}")
    print(f"  updated   : {result.records_updated}")
    snapshots = len(result.snapshots) if result.snapshots else 0
    print(f"  snapshots : {snapshots}")
    if result.error:
        print(f"  error     : {result.error}")
    print()


def _print_stats() -> None:
    from radar.db import get_conn
    conn = get_conn()
    content_count = conn.execute("SELECT COUNT(*) FROM content").fetchone()[0]
    snap_count = conn.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0]
    run_count = conn.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0]

    by_platform = conn.execute(
        "SELECT platform, COUNT(*) FROM content GROUP BY platform"
    ).fetchall()

    print()
    print("=== Radar DB Stats ===")
    print(f"  content rows  : {content_count}")
    print(f"  snapshots     : {snap_count}")
    print(f"  source runs   : {run_count}")
    print()
    for row in by_platform:
        print(f"  [{row[0]}] {row[1]} content rows")

    recent_runs = conn.execute(
        """SELECT platform, keyword, status, records_new, records_updated, finished_at
           FROM source_runs ORDER BY id DESC LIMIT 5"""
    ).fetchall()
    print()
    print("  Recent runs:")
    for r in recent_runs:
        print(f"    {r['finished_at'] or '(running)'} | {r['platform']:8} | {r['keyword'][:20]:20} | {r['status']} | +{r['records_new']} ~{r['records_updated']}")
    print()


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_scan(args) -> int:
    platform = args.platform.lower()
    keyword = args.keyword
    max_count = args.max_count

    print(f"\n[radar] Scanning {platform} / keyword='{keyword}' / max={max_count}")

    if platform == "douyin":
        raw_result = dy_source.scan(keyword, max_count=max_count)
    elif platform == "bilibili":
        raw_result = bili_source.scan(keyword, max_count=max_count)
    else:
        print(f"[radar] Unknown platform: {platform}. Use 'douyin' or 'bilibili'.")
        return 1

    result = ingest(raw_result)
    _print_result(result)

    if result.status == "AUTH_REQUIRED":
        print("[radar] ACTION REQUIRED: Open Edge with Douyin CDP profile and retry.")
        return 2
    if result.status == "ERROR":
        return 1
    return 0


def cmd_stats(args) -> int:
    _print_stats()
    return 0


def _format_terminal(cands: list[CandidateScore], limit: int) -> str:
    """Format top candidates for terminal output."""
    lines = []
    lines.append("")
    lines.append("=" * 100)
    lines.append(f"  RADAR TOP {min(limit, len(cands))}")
    lines.append("=" * 100)
    for i, c in enumerate(cands[:limit], 1):
        pub_str = ""
        if c.publish_time:
            pub_str = datetime.fromtimestamp(c.publish_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        metrics_parts = []
        if c.latest_metrics.get("views") is not None:
            metrics_parts.append(f"views={c.latest_metrics['views']:,.0f}")
        if c.latest_metrics.get("likes") is not None:
            metrics_parts.append(f"likes={c.latest_metrics['likes']:,.0f}")
        if c.latest_metrics.get("comments") is not None:
            metrics_parts.append(f"comments={c.latest_metrics['comments']:,.0f}")
        if c.latest_metrics.get("shares") is not None:
            metrics_parts.append(f"shares={c.latest_metrics['shares']:,.0f}")
        metrics_str = " | ".join(metrics_parts)

        lines.append(f"  #{i:<3} [{c.platform:8}] score={c.total_score:6.2f}  conf={c.confidence:.2f}")
        lines.append(f"       title: {c.title[:60] if c.title else '(no title)'}")
        lines.append(f"       creator: {c.creator_name or '(unknown)'}")
        lines.append(f"       published: {pub_str}")
        lines.append(f"       metrics: {metrics_str}")
        bd_parts = [f"{k}={v:.1f}" for k, v in c.breakdown.items()]
        lines.append(f"       breakdown: {', '.join(bd_parts)}")
        lines.append(f"       url: {c.canonical_url}")
        lines.append("-" * 100)
    return "\n".join(lines)


def _to_dict(c: CandidateScore) -> dict:
    """Convert CandidateScore to a plain dict."""
    return {
        "platform": c.platform,
        "content_id": c.content_id,
        "title": c.title,
        "creator_name": c.creator_name,
        "creator_id": c.creator_id,
        "canonical_url": c.canonical_url,
        "publish_time": c.publish_time,
        "publish_time_iso": datetime.fromtimestamp(c.publish_time, tz=timezone.utc).isoformat() if c.publish_time else None,
        "latest_metrics": c.latest_metrics,
        "snapshot_count": c.snapshot_count,
        "total_score": c.total_score,
        "confidence": c.confidence,
        "breakdown": c.breakdown,
    }


def _format_json(cands: list[CandidateScore], limit: int) -> str:
    """Format top candidates as JSON."""
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": min(limit, len(cands)),
        "candidates": [_to_dict(c) for c in cands[:limit]],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def _format_markdown(cands: list[CandidateScore], limit: int) -> str:
    """Format top candidates as Markdown table."""
    lines = []
    lines.append(f"# Radar Top {min(limit, len(cands))}")
    lines.append("")
    lines.append(f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_")
    lines.append("")
    lines.append("| Rank | Platform | Title | Creator | Published | Score | Confidence | Views | Likes | Comments |")
    lines.append("|------|----------|-------|---------|-----------|-------|------------|-------|-------|----------|")
    for i, c in enumerate(cands[:limit], 1):
        pub_str = ""
        if c.publish_time:
            pub_str = datetime.fromtimestamp(c.publish_time, tz=timezone.utc).strftime("%Y-%m-%d")
        title = (c.title or "(no title)")[:50]
        creator = (c.creator_name or "(unknown)")[:15]
        views = f"{c.latest_metrics.get('views'):,}" if c.latest_metrics.get('views') is not None else "-"
        likes = f"{c.latest_metrics.get('likes'):,}" if c.latest_metrics.get('likes') is not None else "-"
        comments = f"{c.latest_metrics.get('comments'):,}" if c.latest_metrics.get('comments') is not None else "-"
        lines.append(
            f"| {i} | {c.platform} | {title} | {creator} | {pub_str} | {c.total_score:.2f} | {c.confidence:.2f} | {views} | {likes} | {comments} |"
        )
    lines.append("")
    lines.append("## Score Breakdown")
    lines.append("")
    for i, c in enumerate(cands[:limit], 1):
        bd = ", ".join(f"{k}={v:.1f}" for k, v in c.breakdown.items())
        lines.append(f"**#{i}** `{c.platform}` {c.title} — total={c.total_score:.2f} | {bd}")
    lines.append("")
    return "\n".join(lines)


def cmd_top(args) -> int:
    platform = args.platform.lower() if args.platform else None
    limit = args.limit
    fmt = args.format or "terminal"
    output_path = args.output

    print(f"\n[radar] Ranking candidates... platform={platform or 'all'}, limit={limit}")

    try:
        candidates = rank_candidates(platform_filter=platform)
    except Exception as e:
        print(f"\n[radar] ERROR: Ranking failed: {e}")
        return 1

    if not candidates:
        print("[radar] No candidates found.")
        return 0

    print(f"[radar] Scored {len(candidates)} candidates, outputting top {min(limit, len(candidates))}")

    if fmt == "terminal":
        output = _format_terminal(candidates, limit)
        print(output)
    elif fmt == "json":
        output = _format_json(candidates, limit)
        print(output)
    elif fmt == "markdown":
        output = _format_markdown(candidates, limit)
        print(output)
    else:
        print(f"[radar] Unknown format: {fmt}. Use 'terminal', 'json', or 'markdown'.")
        return 1

    # Save to file if --output specified
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output, encoding="utf-8")
        print(f"\n[radar] Output saved to {out}")

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="radar",
        description="Radar CLI — Phase 2.1 Ingestion + Phase 2.2 Ranking",
    )
    sub = parser.add_subparsers(dest="command")

    # scan subcommand
    p_scan = sub.add_parser("scan", help="Run a keyword scan on a platform")
    p_scan.add_argument("--platform", required=True, choices=["douyin", "bilibili"],
                        help="Platform to scan")
    p_scan.add_argument("--keyword", required=True, help="Search keyword")
    p_scan.add_argument("--max-count", type=int, default=15,
                        help="Max records to fetch (default: 15)")

    # stats subcommand
    sub.add_parser("stats", help="Show DB statistics")

    # top subcommand
    p_top = sub.add_parser("top", help="Show top ranked candidates")
    p_top.add_argument("--platform", choices=["douyin", "bilibili"], default=None,
                        help="Filter by platform (default: all)")
    p_top.add_argument("--limit", type=int, default=20,
                        help="Number of top candidates to show (default: 20)")
    p_top.add_argument("--format", choices=["terminal", "json", "markdown"], default="terminal",
                        help="Output format (default: terminal)")
    p_top.add_argument("--output", type=str, default=None,
                        help="Write output to file (optional)")

    args = parser.parse_args()

    if args.command == "scan":
        return cmd_scan(args)
    elif args.command == "stats":
        return cmd_stats(args)
    elif args.command == "top":
        return cmd_top(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
