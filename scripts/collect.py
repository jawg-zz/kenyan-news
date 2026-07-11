#!/usr/bin/env python3
"""
Cron entry point — crawl all sources, persist to DB, check breaking news.
Silent when nothing interesting — only produces output on:
  - Breaking news alerts (urgent stories)
  - Significant new article counts (burst detection)
  - Source failures (site down / selector broken)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kenyan_news import db, scraper, breaking


def main():
    db.init_db()
    conn = db.get_conn()
    try:
        results = scraper.crawl_all(conn)
        messages = []
        all_alerts = []

        for r in results:
            # Report failures immediately
            if not r["success"]:
                messages.append(f"✗ {r['source']}: {r.get('error', 'unknown error')}")
                continue

            # Check for breaking news
            if r["new"] > 0:
                alerts = breaking.check_articles(conn, r["source"], limit=r["new"])
                for a in alerts:
                    all_alerts.append(a)

            # Report significant bursts (5+ new articles = site published a batch)
            if r["new"] >= 5:
                messages.append(f"📰 {r['source']}: {r['new']} new articles")

        # Output breaking alerts first (they're time-sensitive)
        for a in all_alerts:
            messages.append(breaking.format_alert(a))

        # Only output if there's something to say
        if messages:
            print("\n".join(messages))

    finally:
        conn.close()


if __name__ == "__main__":
    main()
