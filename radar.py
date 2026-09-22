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
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure src/ is on the path when run from project root
sys.path.insert(0, str(Path(__file__).parent / "src"))

from radar.ingest import ingest
from radar.ranking import CandidateScore, rank_candidates
from radar.scanplan import load_scan_plan, run_scan_all
from radar.sources import bilibili as bili_source
from radar.sources import douyin as dy_source
from radar.sources.bili_creator import scan_creator, scan_creator_via_search


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


def cmd_scan_all(args) -> int:
    plan_path = Path(args.plan) if args.plan else None
    print("\n[radar] scan-all: running all enabled sources sequentially")
    try:
        results = run_scan_all(path=plan_path)
    except FileNotFoundError as e:
        print(f"[radar] ERROR: {e}")
        return 1

    if not results:
        print("[radar] No enabled sources in scan plan.")
        return 0

    ok = sum(1 for _, r in results if r.status == "SUCCESS")
    empty = sum(1 for _, r in results if r.status == "EMPTY")
    errors = sum(1 for _, r in results if r.status in ("ERROR", "AUTH_REQUIRED"))
    total_new = sum(r.records_new for _, r in results)
    total_upd = sum(r.records_updated for _, r in results)

    print()
    print(f"[radar] scan-all done: {len(results)} sources | {ok} success | {empty} empty | {errors} error/auth")
    print(f"[radar] new content: {total_new}, updated: {total_upd}")
    return 0 if errors == 0 else 2


def cmd_creators(args) -> int:
    """Crawl video history for top Bilibili creators (creator baseline signal)."""
    from radar.db import get_conn

    top_n = args.top
    max_videos = args.max_videos

    conn = get_conn()
    rows = conn.execute(
        """SELECT creator_id, creator_name, COUNT(*) as cnt
           FROM content
           WHERE platform = 'bilibili' AND creator_id IS NOT NULL AND creator_id != ''
           GROUP BY creator_id
           ORDER BY cnt DESC, creator_id
           LIMIT ?""",
        (top_n,),
    ).fetchall()
    conn.close()

    if not rows:
        print("[radar] No Bilibili creators found.")
        return 0

    print(f"\n[radar] creators: crawling history for top {len(rows)} Bilibili creators (max {max_videos} videos each)")
    total_new = 0
    errors = 0
    for r in rows:
        mid = r["creator_id"]
        name = r["creator_name"] or ""
        print(f"  [creator] {mid} {name or '(unknown)'} (has {r['cnt']} in DB)")
        # Primary: space API; Fallback: search by creator name (risk-control safe)
        result = scan_creator(mid, creator_name=name, max_count=max_videos)
        if result.status != "SUCCESS" and name:
            print(f"    (space API {result.status}, falling back to name search)")
            result = scan_creator_via_search(mid, name, max_count=max_videos)
        if result.status == "SUCCESS":
            result = ingest(result)
            print(f"    -> SUCCESS: raw={result.records_raw} norm={result.records_normalized} +new={result.records_new} ~upd={result.records_updated}")
            total_new += result.records_new
        else:
            errors += 1
            print(f"    -> {result.status}: {result.error or 'no data'}")
        time.sleep(2)   # polite pause between creators

    print()
    print(f"[radar] creators done: {total_new} new content, {errors} errors")
    return 0 if errors == 0 else 2


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


def cmd_analyze(args) -> int:
    """Candidate Content Intelligence pipeline (Phase 3.1)."""
    import json as _json
    from radar.intel.pipeline import run_intelligence
    from radar.intel.report import build_report, write_reports

    limit = args.limit
    force = args.force
    content_id_filter = args.content_id

    print(f"\n[radar] analyze: building candidate intelligence (limit={limit}, force={force})")

    # Load candidates from ranking (reuse current ranking output)
    if content_id_filter:
        # single candidate by id
        ranked = rank_candidates()
        cands = [c for c in ranked if c.content_id == content_id_filter]
        if not cands:
            print(f"[radar] ERROR: content_id {content_id_filter} not found in ranking")
            return 1
        cand_dicts = [_to_dict(c) for c in cands]
    else:
        top_path = Path("data/radar_top20.json")
        if not top_path.exists():
            print("[radar] Ranking output not found. Run 'python radar.py top --limit 20' first.")
            return 1
        with top_path.open(encoding="utf-8") as f:
            data = _json.load(f)
        cand_dicts = data["candidates"][:limit]

    if not cand_dicts:
        print("[radar] No candidates to analyze.")
        return 0

    print(f"[radar] Analyzing {len(cand_dicts)} candidates\n")
    summary = run_intelligence(cand_dicts, force=force)

    # Build + write reports
    json_str, md_str = build_report(summary, cand_dicts)
    jpath, mpath = write_reports(json_str, md_str)

    print()
    print("[radar] analyze done:")
    print(f"  requested:          {summary['requested']}")
    print(f"  media downloaded:   {summary['media_downloaded']} (+{summary['media_already']} cached, {summary['media_unavailable']} unavailable)")
    print(f"  transcripts:        {summary['transcripts']}")
    print(f"  intelligence:       {summary['intelligence_generated']} generated, {summary['intelligence_cached']} cached, {summary['intelligence_errors']} errors")
    print(f"  model calls:        {summary['model_calls']} (retries: {summary['model_retries']})")
    print(f"  JSON report:        {jpath}")
    print(f"  Markdown report:    {mpath}")
    return 0 if summary["intelligence_errors"] == 0 else 2


def cmd_performance(args) -> int:
    """Export observed post-publication performance evidence (Phase 4)."""
    from viralmint_overlay.performance_service import build_feedback_summary
    from viralmint_overlay.performance_store import PerformanceStore
    from viralmint_overlay.review_store import ReviewStore

    summary = build_feedback_summary(PerformanceStore(), ReviewStore())
    fmt = args.format or "json"

    if fmt == "json":
        output = json.dumps(summary, ensure_ascii=False, indent=2)
    else:
        lines = [
            "# Performance Feedback",
            "",
            f"_Generated: {summary['generated_at']}_",
            "",
            f"Published posts: {summary['published_posts']}",
            f"Posts with metrics: {summary['posts_with_metrics']}",
            "",
            "## Posts",
            "",
            "| Platform | Source | Title | Views | Likes | Comments | Shares | ER | Velocity/h |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
        for post in summary["posts"]:
            latest = post.get("latest_metrics") or {}
            derived = post.get("derived") or {}
            er = derived.get("engagement_rate")
            vel = derived.get("view_velocity_per_hour")
            lines.append(
                "| {platform} | {source_platform}:{content_id} | {title} | {views} | {likes} | "
                "{comments} | {shares} | {er} | {vel} |".format(
                    platform=post.get("platform") or "-",
                    source_platform=post.get("source_platform") or "-",
                    content_id=post.get("content_id") or "-",
                    title=(post.get("title") or "-").replace("|", "\\|")[:60],
                    views=latest.get("views") if latest.get("views") is not None else "-",
                    likes=latest.get("likes") if latest.get("likes") is not None else "-",
                    comments=latest.get("comments") if latest.get("comments") is not None else "-",
                    shares=latest.get("shares") if latest.get("shares") is not None else "-",
                    er=f"{er:.4f}" if er is not None else "-",
                    vel=f"{vel:.1f}" if vel is not None else "-",
                )
            )

        lines += ["", "## Observed patterns", ""]
        if summary["patterns"]:
            lines += [
                "| Distribution platform | Dimension | Value | n | Avg views | Avg ER | Avg velocity/h |",
                "|---|---|---|---:|---:|---:|---:|",
            ]
            for p in summary["patterns"]:
                av = p.get("avg_views")
                ae = p.get("avg_engagement_rate")
                vv = p.get("avg_view_velocity_per_hour")
                lines.append(
                    "| {platform} | {dimension} | {value} | {n} | {views} | {er} | {vel} |".format(
                        platform=p["platform"],
                        dimension=p["dimension"],
                        value=str(p["value"]).replace("|", "\\|"),
                        n=p["sample_count"],
                        views=f"{av:.1f}" if av is not None else "-",
                        er=f"{ae:.4f}" if ae is not None else "-",
                        vel=f"{vv:.1f}" if vv is not None else "-",
                    )
                )
        else:
            lines.append("No pattern evidence yet.")

        lines += [
            "",
            "> Radar ranking weights were not changed automatically. This report is observed market evidence.",
            "",
        ]
        output = "\n".join(lines)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(output, encoding="utf-8")
        print(f"[radar] Performance feedback saved to {out}")
    else:
        print(output)
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

    # scan-all subcommand
    p_all = sub.add_parser("scan-all", help="Run all enabled sources from scan plan")
    p_all.add_argument("--plan", type=str, default=None,
                       help="Path to scan plan YAML (default: data/radar_sources.yaml)")

    # creators subcommand
    p_cre = sub.add_parser("creators", help="Crawl history for top Bilibili creators")
    p_cre.add_argument("--top", type=int, default=10,
                       help="Number of top creators to crawl (default: 10)")
    p_cre.add_argument("--max-videos", type=int, default=30,
                       help="Max videos per creator (default: 30)")

    # analyze subcommand
    p_ana = sub.add_parser("analyze", help="Build candidate content intelligence (Phase 3.1)")
    p_ana.add_argument("--limit", type=int, default=10,
                       help="Number of top candidates to analyze (default: 10)")
    p_ana.add_argument("--content-id", type=str, default=None,
                       help="Analyze a single candidate by content_id")
    p_ana.add_argument("--force", action="store_true",
                       help="Re-run even if cached intelligence.json exists")

    # performance feedback subcommand
    p_perf = sub.add_parser("performance", help="Show/export post-publication performance feedback")
    p_perf.add_argument("--format", choices=["json", "markdown"], default="json",
                        help="Output format (default: json)")
    p_perf.add_argument("--output", type=str, default=None,
                        help="Write output to file (optional)")

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
    elif args.command == "scan-all":
        return cmd_scan_all(args)
    elif args.command == "creators":
        return cmd_creators(args)
    elif args.command == "analyze":
        return cmd_analyze(args)
    elif args.command == "top":
        return cmd_top(args)
    elif args.command == "performance":
        return cmd_performance(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
