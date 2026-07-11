"""
Kenyan News API — lightweight FastAPI server for browsing/searching.
Run: uv sync --group server && uv run uvicorn kenyan_news.api:app --host 0.0.0.0 --port 8090
"""

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import db

app = FastAPI(title="Kenyan News Aggregator", version="2.0.0")


@app.on_event("startup")
def startup():
    db.init_db()


def _get_conn():
    return db.get_conn()


# ─── HTML ────────────────────────────────────────────────────────────


def _page(body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Kenyan News</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,system-ui,sans-serif; background:#0f0f0f; color:#e0e0e0; padding:20px; }}
  h1 {{ font-size:1.5rem; margin-bottom:8px; color:#fff; }}
  .meta {{ color:#888; font-size:0.85rem; margin-bottom:20px; }}
  nav {{ margin-bottom:24px; display:flex; gap:12px; flex-wrap:wrap; }}
  nav a {{ color:#58a6ff; text-decoration:none; font-size:0.9rem; }}
  nav a:hover {{ text-decoration:underline; }}
  form {{ display:flex; gap:8px; margin-bottom:24px; }}
  input[type=text] {{ flex:1; padding:8px 12px; border:1px solid #333; border-radius:6px; background:#1a1a1a; color:#e0e0e0; font-size:0.9rem; }}
  button {{ padding:8px 16px; border:none; border-radius:6px; background:#238636; color:#fff; cursor:pointer; }}
  .card {{ background:#1a1a1a; border:1px solid #2a2a2a; border-radius:8px; padding:16px; margin-bottom:12px; }}
  .card h2 {{ font-size:1rem; margin-bottom:4px; }}
  .card h2 a {{ color:#58a6ff; text-decoration:none; }}
  .card h2 a:hover {{ text-decoration:underline; }}
  .card .src {{ font-size:0.8rem; color:#888; }}
  .card .time {{ font-size:0.75rem; color:#666; }}
  .card p {{ margin-top:6px; font-size:0.85rem; color:#aaa; line-height:1.4; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:0.7rem; font-weight:600; }}
  .badge-breaking {{ background:#8b0000; color:#fff; }}
  .story {{ border-left:3px solid #238636; }}
  .story .src-list {{ margin-top:4px; font-size:0.8rem; color:#888; }}
  .pagination {{ display:flex; gap:12px; margin-top:20px; }}
  .pagination a {{ color:#58a6ff; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


# ─── Routes ──────────────────────────────────────────────────────────


@app.get("/")
def index():
    conn = _get_conn()
    try:
        articles = db.get_articles(conn, limit=20)
        stories = db.get_stories(conn, limit=5, min_articles=2)
        health = db.get_health(conn, days=1)
    finally:
        conn.close()

    cards = "".join(
        f'<div class="card"><h2><a href="{a["url"]}" target="_blank">{a["title"]}</a></h2>'
        f'<div class="src">{a["source_name"]}</div>'
        f'<div class="time">{a["crawled_at"]}</div>'
        + (f'<p>{a["text"][:200]}…</p>' if a.get("text") and len(a["text"]) > 100 else "")
        + "</div>"
        for a in articles
    )

    story_cards = ""
    for s in stories:
        sources = ", ".join(a["source_name"] for a in s["articles"])
        story_cards += (
            f'<div class="card story"><h2><a href="{s["articles"][0]["url"]}">{s["title"]}</a></h2>'
            f'<div class="src-list">📰 {s["article_count"]} articles from {sources}</div></div>'
        )

    health_table = ""
    if health:
        rows = "".join(
            f"<tr><td>{r['name']}</td><td>{r['date']}</td>"
            f"<td>{r['successes']}</td><td>{r['failures']}</td>"
            f"<td>{r['total_articles']}</td>"
            f"<td>{r['avg_latency']:.1f}s</td></tr>"
            for r in health
        )
        health_table = f"<table><tr><th>Source</th><th>Date</th><th>OK</th><th>Fail</th><th>Articles</th><th>Latency</th></tr>{rows}</table>"

    body = f"""
<h1>📰 Kenyan News</h1>
<div class="meta">Aggregating 6 sources · every 30 min</div>
<nav>
  <a href="/">Headlines</a>
  <a href="/stories">Stories</a>
  <a href="/health">Health</a>
  <a href="/docs">API</a>
</nav>
<form action="/search" method="get">
  <input type="text" name="q" placeholder="Search headlines &amp; articles…"/>
  <button type="submit">Search</button>
</form>
{"<h2>📌 Trending Stories</h2>" + story_cards if story_cards else ""}
<h2>📋 Latest Headlines</h2>
{cards}
"""
    if health_table:
        body += f"<h3>📊 Today's Health</h3>{health_table}"

    return HTMLResponse(_page(body))


@app.get("/stories")
def stories_route(min_articles: int = Query(1, ge=1)):
    conn = _get_conn()
    try:
        stories = db.get_stories(conn, limit=20, min_articles=min_articles)
    finally:
        conn.close()

    cards = "".join(
        f'<div class="card story"><h2><a href="{s["articles"][0]["url"]}" target="_blank">{s["title"]}</a></h2>'
        f'<div class="src-list">📰 {s["article_count"]} articles'
        + (" from " + ", ".join(a["source_name"] for a in s["articles"]) if s["articles"] else "")
        + "</div>"
        + "".join(f'<div style="margin-top:4px;font-size:0.8rem">→ <a href="{a["url"]}">{a["source_name"]}</a></div>'
                  for a in s["articles"])
        + "</div>"
        for s in stories
    )

    body = f"""
<h1>📌 Stories</h1>
<nav><a href="/">← Back</a></nav>
<form action="/stories" method="get" style="display:inline-flex;align-items:center;gap:8px">
  <label style="font-size:0.85rem">Min sources:</label>
  <input type="number" name="min_articles" value="{min_articles}" min="1" max="10" style="width:60px">
  <button type="submit">Filter</button>
</form>
{cards or "<p>No stories with multiple sources yet (need more crawl cycles).</p>"}
"""
    return HTMLResponse(_page(body))


@app.get("/search")
def search_route(q: str = Query("", min_length=2)):
    conn = _get_conn()
    try:
        results = db.search_articles(conn, q, limit=30)
    finally:
        conn.close()

    cards = "".join(
        f'<div class="card"><h2><a href="{a["url"]}" target="_blank">{a["title"]}</a></h2>'
        f'<div class="src">{a["source_name"]}</div>'
        + (f'<p>{a["text"][:300]}…</p>' if a.get("text") and len(a["text"]) > 100 else "")
        + "</div>"
        for a in results
    )

    body = f"""
<h1>🔍 Search: "{q}"</h1>
<nav><a href="/">← Back</a></nav>
<form action="/search" method="get">
  <input type="text" name="q" value="{q}" placeholder="Search…"/>
  <button type="submit">Search</button>
</form>
{cards or "<p>No results.</p>"}
"""
    return HTMLResponse(_page(body))


@app.get("/health")
def health_route(days: int = Query(7, ge=1, le=30)):
    conn = _get_conn()
    try:
        rows = db.get_health(conn, days=days)
    finally:
        conn.close()

    trs = "".join(
        f"<tr><td>{r['name']}</td><td>{r['date']}</td>"
        f"<td>{r['successes']}</td><td style='color:{'#f85149' if r['failures']>0 else '#3fb950'}'>{r['failures']}</td>"
        f"<td>{r['total_articles']}</td>"
        f"<td>{r['avg_latency']:.1f}s</td></tr>"
        for r in rows
    )

    body = f"""
<h1>📊 Source Health</h1>
<nav><a href="/">← Back</a></nav>
<table style="width:100%;border-collapse:collapse;margin-top:12px">
<tr style="background:#2a2a2a"><th>Source</th><th>Date</th><th>OK</th><th>Fail</th><th>Articles</th><th>Avg Latency</th></tr>
{trs or "<tr><td colspan='6' style='text-align:center;color:#888'>No data</td></tr>"}
</table>
"""
    return HTMLResponse(_page(body))


@app.get("/api/articles")
def api_articles(source: str = None, limit: int = Query(20, le=100), offset: int = Query(0, ge=0)):
    conn = _get_conn()
    try:
        articles = db.get_articles(conn, source=source, limit=limit, offset=offset)
    finally:
        conn.close()
    return JSONResponse(articles)


@app.get("/api/stories")
def api_stories(min_articles: int = Query(1, ge=1), limit: int = Query(20, le=50)):
    conn = _get_conn()
    try:
        stories = db.get_stories(conn, limit=limit, min_articles=min_articles)
    finally:
        conn.close()
    return JSONResponse(stories)


@app.get("/api/search")
def api_search(q: str = Query("", min_length=2), limit: int = Query(20, le=50)):
    conn = _get_conn()
    try:
        results = db.search_articles(conn, q, limit=limit)
    finally:
        conn.close()
    return JSONResponse(results)


@app.get("/api/health")
def api_health(days: int = Query(7, ge=1, le=30)):
    conn = _get_conn()
    try:
        rows = db.get_health(conn, days=days)
    finally:
        conn.close()
    return JSONResponse(rows)


@app.post("/api/upload-db")
async def api_upload_db(request: Request):
    """Upload a SQLite database snapshot. Replaces the local DB."""
    from tempfile import NamedTemporaryFile
    import shutil

    body = await request.body()
    if not body or len(body) < 100:
        return JSONResponse({"ok": False, "error": "Invalid DB content"}, status_code=400)

    # Write to temp file, validate it's a valid SQLite DB
    tmp = NamedTemporaryFile(delete=False, suffix=".db")
    tmp.write(body)
    tmp.close()

    try:
        import sqlite3
        conn = sqlite3.connect(tmp.name)
        conn.execute("SELECT COUNT(*) FROM articles")
        conn.close()
    except Exception as e:
        os.unlink(tmp.name)
        return JSONResponse({"ok": False, "error": f"Invalid DB: {e}"}, status_code=400)

    # Replace live DB
    backup = str(db.DB_PATH) + ".bak"
    if db.DB_PATH.exists():
        shutil.copy2(db.DB_PATH, backup)
    shutil.move(tmp.name, db.DB_PATH)
    return JSONResponse({"ok": True, "size": len(body)})


# ─── Telegram Bot Webhook ────────────────────────────────────────────
# Set up: Set TELEGRAM_NEWS_BOT_TOKEN env var, then configure your bot's
# webhook: https://api.telegram.org/bot<TOKEN>/setWebhook?url=<PUBLIC_URL>/telegram-webhook

import os
import time as time_mod
from datetime import datetime, timezone

TELEGRAM_API = "https://api.telegram.org/bot"


def _tg_send(chat_id: int, text: str, parse_mode: str = "Markdown"):
    """Send a message via Telegram Bot API."""
    token = os.environ.get("TELEGRAM_NEWS_BOT_TOKEN", "")
    if not token:
        return {"ok": False, "error": "TELEGRAM_NEWS_BOT_TOKEN not set"}
    import httpx
    try:
        resp = httpx.post(
            f"{TELEGRAM_API}{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode,
                  "disable_web_page_preview": True},
            timeout=10,
        )
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _tg_briefing() -> str:
    """Generate briefing text for Telegram."""
    conn = _get_conn()
    try:
        cutoff = time_mod.time() - 24 * 3600
        total = db.article_count_since(conn, cutoff)
        stories = db.get_stories_since(conn, cutoff, min_articles=2)
        articles = db.get_articles_since(conn, cutoff, limit=5)
        health = db.get_health(conn, days=1)
    finally:
        conn.close()

    now = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    sources_ok = sum(1 for h in health if h['successes'] > 0) if health else 0
    sources_total = len(set(h['name'] for h in health)) if health else 0

    lines = [f"📰 *Kenyan News Briefing* — {now}", ""]
    if total == 0:
        lines.append("No new articles in the last 24h.")
        return "\n".join(lines)

    lines.append(f"{total} articles across {sources_ok}/{sources_total} sources.")
    lines.append("")

    if stories:
        lines.append(f"🔗 *Top Stories* ({len(stories)}):")
        for s in stories[:5]:
            src_list = ", ".join(set(a["source_name"] for a in s["articles"]))
            lines.append(f"• {s['title']}")
            lines.append(f"  _{s['article_count']} articles from {src_list}_")
            for a in s["articles"][:2]:
                lines.append(f"  [{a['source_name']}]({a['url']})")

    lines.append("")
    lines.append("*Latest headlines:*")
    for a in articles[:8]:
        lines.append(f"• [{a['source_name']}] {a['title']}")

    return "\n".join(lines)


@app.post("/telegram-webhook")
async def telegram_webhook(payload: dict):
    """Receive Telegram bot webhook and respond to commands."""
    import httpx

    token = os.environ.get("TELEGRAM_NEWS_BOT_TOKEN", "")
    if not token:
        return {"ok": False, "error": "Bot token not configured"}

    # Extract message
    message = payload.get("message", {}) or payload.get("edited_message", {})
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return {"ok": True}  # Acknowledge silently

    # Parse command
    parts = text.split(maxsplit=1)
    command = parts[0].lower()
    query = parts[1] if len(parts) > 1 else ""

    conn = _get_conn()

    try:
        if command == "/start" or command == "/help":
            reply = (
                "📰 *Kenyan News Bot*\n\n"
                "Commands:\n"
                "`/news` — Latest headlines\n"
                "`/search <query>` — Search articles\n"
                "`/stories` — Top story clusters\n"
                "`/briefing` — 24h briefing\n"
                "`/health` — Source status\n"
                "`/help` — This message"
            )

        elif command == "/news":
            articles = db.get_articles(conn, limit=10)
            if not articles:
                reply = "No articles yet. Run a crawl first."
            else:
                lines = ["📋 *Latest Headlines*", ""]
                for a in articles:
                    title = a["title"].strip().split("\n")[0][:80]
                    lines.append(f"• [{a['source_name']}] {title}")
                reply = "\n".join(lines)

        elif command == "/search" and query:
            results = db.search_articles(conn, query, limit=8)
            if not results:
                reply = f"Nothing found for \"{query}\"."
            else:
                lines = [f"🔍 *Search: \"{query}\"*", ""]
                for a in results:
                    title = a["title"].strip().split("\n")[0][:80]
                    lines.append(f"• [{a['source_name']}] {title}")
                    lines.append(f"  {a['url']}")
                reply = "\n".join(lines)

        elif command == "/search" and not query:
            reply = "Usage: `/search <query>` — e.g. `/search accident`"

        elif command == "/stories":
            stories = db.get_stories(conn, limit=8, min_articles=2)
            if not stories:
                reply = "No multi-source stories yet. Need more crawl cycles."
            else:
                lines = ["🔗 *Top Stories*", ""]
                for s in stories:
                    src_list = ", ".join(set(a["source_name"] for a in s["articles"]))
                    lines.append(f"• {s['title']}")
                    lines.append(f"  _{s['article_count']} articles from {src_list}_")
                reply = "\n".join(lines)

        elif command == "/briefing":
            reply = _tg_briefing()

        elif command == "/health":
            health = db.get_health(conn, days=2)
            if not health:
                reply = "No health data yet."
            else:
                lines = ["📊 *Source Health*", ""]
                for h in health:
                    status = "✅" if h['failures'] == 0 else "⚠️"
                    lat = f"{h['avg_latency']:.1f}s" if h['avg_latency'] else "-"
                    lines.append(f"{status} *{h['name']}*: {h['successes']} OK, {h['failures']} fail, {lat}")
                reply = "\n".join(lines)

        else:
            reply = f"Unknown command: {command}\nUse /help for available commands."

    finally:
        conn.close()

    # Truncate if too long for Telegram
    if len(reply) > 4000:
        reply = reply[:3900] + "\n\n…truncated"

    _tg_send(chat_id, reply)
    return {"ok": True}
