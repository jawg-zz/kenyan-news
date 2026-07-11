"""
╔══════════════════════════════════════════════════════════════╗
║              Kenyan News Aggregator  v2                     ║
║  Multi-source crawling · Dedup · Breaking alerts · Health   ║
╚══════════════════════════════════════════════════════════════╝

Usage:
  uv run kenyan-news                        # crawl all sources, print headlines
  uv run kenyan-news --site citizen         # single source
  uv run kenyan-news --json                 # JSON output
  uv run kenyan-news --collect              # crawl + persist to SQLite + check breaking
  uv run kenyan-news --stories              # show deduped stories
  uv run kenyan-news --search "emeralds"    # search headlines
  uv run kenyan-news --health               # source health report
  uv run kenyan-news --open URL             # full article text
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from kenyan_news import db, scraper, breaking

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("kenyan_news")


def cmd_collect(conn, sites):
    """Crawl + persist + check breaking news."""
    results = scraper.crawl_all(conn, sources=sites)
    for r in results:
        status = "✓" if r["success"] else "✗"
        extra = f" — {r.get('error', '')}" if not r["success"] else ""
        print(f"  {status} {r['source']}: {r['found']} found, {r['new']} new{extra}")
        if r["success"] and r["new"] > 0:
            alerts = breaking.check_articles(conn, r["source"], limit=r["new"])
            for a in alerts:
                print(breaking.format_alert(a))
    return results


def cmd_headlines(conn, sites):
    """Fetch and display headlines (no persistence)."""
    results = scraper.crawl_all(conn, sources=sites)
    for r in results:
        status = "✓" if r["success"] else "✗"
        if r["success"]:
            articles = db.get_articles(conn, source=r["source"], limit=8)
        else:
            articles = []
        print(f"\n{'='*65}")
        print(f"  {r['source'].upper()}")
        print(f"{'='*65}")
        if not r["success"]:
            print(f"  ✗ {r.get('error', 'unknown error')}")
            continue
        for i, a in enumerate(articles, 1):
            print(f"\n  {i}. {a['title']}")
            print(f"     {a['url']}")
            if a.get("top_image"):
                print(f"     📷 {a['top_image']}")


def cmd_stories(conn):
    stories = db.get_stories(conn, limit=15)
    for s in stories:
        print(f"\n📌 {s['title']}")
        print(f"   ({s['article_count']} articles from {len(set(a['source_name'] for a in s['articles']))} sources)")
        for a in s["articles"]:
            print(f"     [{a['source_name']}] {a['url']}")


def cmd_search(conn, query: str):
    results = db.search_articles(conn, query)
    if not results:
        print(f"No results for '{query}'")
        return
    for a in results:
        print(f"\n[{a['source_name']}] {a['title']}")
        print(f"  {a['url']}")


def cmd_health(conn):
    rows = db.get_health(conn, days=7)
    if not rows:
        print("No health data yet (run --collect first)")
        return
    print(f"{'Source':<16} {'Date':<12} {'OK':>4} {'Fail':>5} {'Articles':>9} {'Latency':>8}")
    print("-" * 60)
    for r in rows:
        avg_lat = f"{r['avg_latency']:.1f}s" if r['avg_latency'] else "-"
        print(f"{r['name']:<16} {r['date']:<12} {r['successes']:>4} {r['failures']:>5} "
              f"{r['total_articles']:>9} {avg_lat:>8}")


def cmd_open(url: str):
    """Fetch and display full article text."""
    result = scraper.fetch_article(url)
    title = result.get("title", "")
    text = result.get("text", "")
    img = result.get("top_image", "")
    authors = result.get("authors", [])
    date = result.get("publish_date", "")
    paywalled = result.get("paywalled", False)

    print(f"\n{'='*65}")
    print(f"  {title}")
    if date:
        print(f"  {date}")
    if authors:
        print(f"  By {', '.join(authors)}")
    print(f"{'='*65}")
    if img:
        print(f"\n  📷 {img}")
    if paywalled:
        print(f"\n  {'─'*65}")
        print(f"  ⚠️  Full article behind paywall. Showing preview only.")
        print(f"  {'─'*65}")
    print(f"\n{text}")


def cmd_briefing(conn, since_hours: int = 24, md: bool = False):
    """Generate a morning briefing summary."""
    import time
    from datetime import datetime, timezone

    cutoff = time.time() - since_hours * 3600
    total = db.article_count_since(conn, cutoff)
    stories = db.get_stories_since(conn, cutoff, min_articles=2)
    articles = db.get_articles_since(conn, cutoff, limit=5)
    health = db.get_health(conn, days=1)

    now = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    sources_ok = sum(1 for h in health if h['successes'] > 0)
    sources_total = len(set(h['name'] for h in health)) if health else 0

    if md:
        # Markdown output (for Telegram / cron delivery)
        lines = [f"📰 *Kenyan News Briefing* — {now}", ""]
        if total == 0:
            lines.append("No new articles in the last 24h. (Sources may be quiet or the pipeline needs a crawl cycle.)")
            print("\n".join(lines))
            return

        lines.append(f"**{total}** articles across **{sources_ok}/{sources_total}** sources.")
        lines.append("")

        if stories:
            lines.append(f"### 🔗 Top Stories ({len(stories)})")
            for s in stories[:5]:
                src_list = ", ".join(set(a["source_name"] for a in s["articles"]))
                lines.append(f"• *{s['title']}*")
                lines.append(f"  _{s['article_count']} articles from {src_list}_")
                for a in s["articles"][:3]:
                    lines.append(f"  → [{a['source_name']}] {a['url']}")
            lines.append("")

        lines.append("### 📋 Latest Headlines")
        for a in articles:
            lines.append(f"• [{a['source_name']}] {a['title']}")
        lines.append("")

        if health:
            failed = [h for h in health if h['failures'] > 0]
            if failed:
                lines.append("### ⚠️ Source Issues")
                for h in failed:
                    lines.append(f"• {h['name']}: {h['failures']} failures")

        print("\n".join(lines))
    else:
        # Terminal output
        print(f"\n{'='*60}")
        print(f"  📰 KENYAN NEWS BRIEFING — {now}")
        print(f"{'='*60}")
        if total == 0:
            print("  No new articles in the last 24h.")
            print("  (Sources may be quiet or need a --collect cycle.)")
            return

        print(f"\n  {total} articles across {sources_ok}/{sources_total} sources.")

        if stories:
            print(f"\n  🔗 Top Stories ({len(stories)}):")
            for s in stories[:5]:
                src_list = ", ".join(set(a["source_name"] for a in s["articles"]))
                print(f"\n    📌 {s['title']}")
                print(f"       ({s['article_count']} articles from {src_list})")
                for a in s["articles"][:3]:
                    print(f"       → [{a['source_name']}] {a['url']}")

        print(f"\n  📋 Latest headlines:")
        for a in articles:
            print(f"    • [{a['source_name']}] {a['title']}")

        if health:
            failed = [h for h in health if h['failures'] > 0]
            if failed:
                print(f"\n  ⚠️  Source issues:")
                for h in failed:
                    print(f"    • {h['name']}: {h['failures']} failures")


def main():
    parser = argparse.ArgumentParser(description="Kenyan News Aggregator v2")
    parser.add_argument("--site", choices=list(scraper.SOURCES.keys()) + list(scraper.RSS_SOURCES.keys()) + ["all"],
                        default=["all"], nargs="+", help="Source(s) to crawl, or 'all'")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--collect", action="store_true", help="Crawl + persist to SQLite + check breaking news")
    parser.add_argument("--stories", action="store_true", help="Show deduped stories")
    parser.add_argument("--search", type=str, help="Search headlines")
    parser.add_argument("--health", action="store_true", help="Source health report")
    parser.add_argument("--open", type=str, help="Fetch full article text from URL")
    parser.add_argument("--briefing", action="store_true", help="Generate 24h briefing summary")
    parser.add_argument("--briefing-hours", type=int, default=24, help="Hours to look back for briefing")
    parser.add_argument("--briefing-md", action="store_true", help="Briefing in markdown format (for Telegram)")
    parser.add_argument("--init-db", action="store_true", help="Initialize database and exit")
    args = parser.parse_args()

    # Commands that don't need DB
    if args.open:
        cmd_open(args.open)
        return

    # Init DB if needed
    db.init_db()
    if args.init_db:
        print(f"Database initialized at {db.DB_PATH}")
        return

    conn = db.get_conn()

    # Determine which sites
    if "all" in args.site:
        sites = list(scraper.SOURCES.keys()) + list(scraper.RSS_SOURCES.keys())
    else:
        sites = args.site

    try:
        if args.collect:
            cmd_collect(conn, sites)
        elif args.stories:
            cmd_stories(conn)
        elif args.search:
            cmd_search(conn, args.search)
        elif args.health:
            cmd_health(conn)
        elif args.briefing:
            cmd_briefing(conn, since_hours=args.briefing_hours, md=args.briefing_md)
        else:
            # Default: headlines mode (no persistent storage)
            cmd_headlines(conn, sites)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
