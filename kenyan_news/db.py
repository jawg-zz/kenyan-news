"""
Kenyan News Aggregator — database layer.

SQLite schema for articles, sources, stories, and crawl health tracking.
"""

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DB_DIR / "news.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    url         TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT 'playwright',   -- 'newspaper4k', 'playwright', 'rss'
    enabled     INTEGER NOT NULL DEFAULT 1,
    added_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES sources(id),
    url         TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    top_image   TEXT DEFAULT '',
    text        TEXT DEFAULT '',           -- full article text (populated on demand)
    text_fetched INTEGER NOT NULL DEFAULT 0,  -- 1 = full text retrieved
    published   TEXT DEFAULT '',           -- publisher's timestamp if available
    crawled_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_crawled ON articles(crawled_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published DESC);

CREATE TABLE IF NOT EXISTS crawl_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES sources(id),
    started_at  REAL NOT NULL,
    finished_at REAL,
    articles_found INTEGER NOT NULL DEFAULT 0,
    articles_new   INTEGER NOT NULL DEFAULT 0,
    success     INTEGER NOT NULL DEFAULT 1,
    error_msg   TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    top_image   TEXT DEFAULT '',
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS story_articles (
    story_id    INTEGER NOT NULL REFERENCES stories(id),
    article_id  INTEGER NOT NULL REFERENCES articles(id),
    PRIMARY KEY (story_id, article_id)
);

CREATE TABLE IF NOT EXISTS health_daily (
    source_id   INTEGER NOT NULL REFERENCES sources(id),
    date        TEXT NOT NULL,              -- 'YYYY-MM-DD'
    attempts    INTEGER NOT NULL DEFAULT 0,
    successes   INTEGER NOT NULL DEFAULT 0,
    failures    INTEGER NOT NULL DEFAULT 0,
    total_articles INTEGER NOT NULL DEFAULT 0,
    avg_latency REAL NOT NULL DEFAULT 0,
    latency_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source_id, date)
);
"""


def get_conn() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()


# ─── Sources ─────────────────────────────────────────────────────────

def get_or_create_source(conn, name: str, url: str, mode: str) -> int:
    cur = conn.execute("SELECT id FROM sources WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row["id"]
    conn.execute(
        "INSERT INTO sources (name, url, mode, added_at) VALUES (?, ?, ?, ?)",
        (name, url, mode, time.time()),
    )
    return conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()["id"]


# ─── Articles ────────────────────────────────────────────────────────

def _maybe_normalise_domain(url: str) -> str:
    """Normalise known domain variations (e.g. citizen.digital vs www.citizen.digital)."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    # Strip leading www.
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return f"{parsed.scheme}://{netloc}{parsed.path}{'?' + parsed.query if parsed.query else ''}"


def upsert_article(conn, source_id: int, url: str, title: str, top_image: str = "",
                   published: str = "") -> int:
    """Insert a new article or update existing. Returns article id and whether it was new."""
    url_norm = _maybe_normalise_domain(url)
    now = time.time()
    cur = conn.execute("SELECT id, title FROM articles WHERE url = ?", (url_norm,))
    existing = cur.fetchone()
    if existing:
        # Update title/image if they changed (site may improve article after publish)
        conn.execute(
            "UPDATE articles SET title=?, top_image=COALESCE(NULLIF(?, ''), top_image), "
            "published=COALESCE(NULLIF(?, ''), published), updated_at=? WHERE id=?",
            (title, top_image, published, now, existing["id"]),
        )
        return existing["id"], False
    conn.execute(
        "INSERT INTO articles (source_id, url, title, top_image, published, crawled_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source_id, url_norm, title, top_image, published, now, now),
    )
    return conn.execute("SELECT id FROM articles WHERE url = ?", (url_norm,)).fetchone()["id"], True


# ─── Stories (dedup / clustering) ────────────────────────────────────

_STRIP_WORDS = {
    "kenya", "kenyan", "says", "after", "over", "under", "amid", "a", "an", "the",
    "in", "of", "for", "on", "to", "at", "by", "with", "from", "as", "into", "and",
}


def _normalise_title(title: str) -> str:
    """Lowercase, strip punctuation, remove common words, return word set for similarity."""
    import re
    cleaned = re.sub(r"[^a-z0-9\s]", "", title.lower())
    words = cleaned.split()
    return " ".join(w for w in words if w not in _STRIP_WORDS and len(w) > 2)


def find_or_create_story(conn, article_id: int, title: str, top_image: str) -> int:
    """
    Try to match this article to an existing story by title similarity.
    Uses a simple word-overlap heuristic (Jaccard on non-stopword tokens).
    """
    normalised = _normalise_title(title)
    new_tokens = set(normalised.split())
    if len(new_tokens) < 2:
        # Too short to cluster — create singleton story
        return _create_story(conn, title, top_image, article_id)

    # Check recent stories (last 7 days) for similarity
    week_ago = time.time() - 7 * 86400
    cur = conn.execute(
        "SELECT id, title FROM stories WHERE last_seen > ? ORDER BY last_seen DESC",
        (week_ago,),
    )
    for row in cur.fetchall():
        existing_tokens = set(_normalise_title(row["title"]).split())
        if not existing_tokens:
            continue
        overlap = len(new_tokens & existing_tokens)
        smaller = min(len(new_tokens), len(existing_tokens))
        # Jaccard-ish: overlap / smaller set >= 0.4
        if smaller > 0 and overlap / smaller >= 0.4:
            # Match — associate
            conn.execute(
                "INSERT OR IGNORE INTO story_articles (story_id, article_id) VALUES (?, ?)",
                (row["id"], article_id),
            )
            conn.execute(
                "UPDATE stories SET last_seen=?, top_image=COALESCE(NULLIF(?, ''), top_image) WHERE id=?",
                (time.time(), top_image, row["id"]),
            )
            return row["id"]

    return _create_story(conn, title, top_image, article_id)


def _create_story(conn, title: str, top_image: str, article_id: int) -> int:
    now = time.time()
    conn.execute(
        "INSERT INTO stories (title, top_image, first_seen, last_seen) VALUES (?, ?, ?, ?)",
        (title, top_image, now, now),
    )
    story_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT OR IGNORE INTO story_articles (story_id, article_id) VALUES (?, ?)",
        (story_id, article_id),
    )
    return story_id


# ─── Crawl Events ────────────────────────────────────────────────────

def start_crawl(conn, source_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO crawl_events (source_id, started_at) VALUES (?, ?)",
        (source_id, time.time()),
    )
    return cur.lastrowid


def finish_crawl(conn, event_id: int, articles_found: int, articles_new: int,
                 success: bool, error_msg: str = ""):
    conn.execute(
        "UPDATE crawl_events SET finished_at=?, articles_found=?, articles_new=?, "
        "success=?, error_msg=? WHERE id=?",
        (time.time(), articles_found, articles_new, 1 if success else 0, error_msg, event_id),
    )


# ─── Health ──────────────────────────────────────────────────────────

def record_health(conn, source_id: int, success: bool, latency: float, articles_found: int):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if success:
        conn.execute(
            "INSERT INTO health_daily (source_id, date, attempts, successes, failures, "
            "total_articles, avg_latency, latency_count) "
            "VALUES (?, ?, 1, 1, 0, ?, ?, 1) "
            "ON CONFLICT(source_id, date) DO UPDATE SET "
            "attempts=attempts+1, successes=successes+1, "
            "total_articles=total_articles+excluded.total_articles, "
            "avg_latency=avg_latency+excluded.avg_latency, "
            "latency_count=latency_count+1",
            (source_id, today, articles_found, latency),
        )
    else:
        conn.execute(
            "INSERT INTO health_daily (source_id, date, attempts, successes, failures, "
            "total_articles, avg_latency, latency_count) "
            "VALUES (?, ?, 1, 0, 1, 0, 0, 0) "
            "ON CONFLICT(source_id, date) DO UPDATE SET "
            "attempts=attempts+1, failures=failures+1",
            (source_id, today),
        )


# ─── Queries ─────────────────────────────────────────────────────────

def get_articles(conn, source: str | None = None, limit: int = 20, offset: int = 0):
    if source:
        cur = conn.execute(
            "SELECT a.*, s.name AS source_name FROM articles a "
            "JOIN sources s ON s.id = a.source_id "
            "WHERE s.name = ? ORDER BY a.crawled_at DESC LIMIT ? OFFSET ?",
            (source, limit, offset),
        )
    else:
        cur = conn.execute(
            "SELECT a.*, s.name AS source_name FROM articles a "
            "JOIN sources s ON s.id = a.source_id "
            "ORDER BY a.crawled_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    return [dict(r) for r in cur.fetchall()]


def get_stories(conn, limit: int = 20, min_articles: int = 1):
    cur = conn.execute(
        "SELECT st.*, COUNT(sa.article_id) AS article_count "
        "FROM stories st "
        "LEFT JOIN story_articles sa ON sa.story_id = st.id "
        "GROUP BY st.id "
        "HAVING article_count >= ? "
        "ORDER BY st.last_seen DESC LIMIT ?",
        (min_articles, limit),
    )
    stories = []
    for row in cur.fetchall():
        story = dict(row)
        # Fetch constituent articles
        cur2 = conn.execute(
            "SELECT a.*, s.name AS source_name FROM articles a "
            "JOIN story_articles sa ON sa.article_id = a.id "
            "JOIN sources s ON s.id = a.source_id "
            "WHERE sa.story_id = ? ORDER BY a.crawled_at",
            (row["id"],),
        )
        story["articles"] = [dict(r) for r in cur2.fetchall()]
        stories.append(story)
    return stories


def get_stories_since(conn, since: float, min_articles: int = 1):
    """Get stories with articles crawled after `since` timestamp."""
    cur = conn.execute(
        "SELECT st.*, COUNT(sa.article_id) AS article_count "
        "FROM stories st "
        "JOIN story_articles sa ON sa.story_id = st.id "
        "JOIN articles a ON a.id = sa.article_id "
        "WHERE a.crawled_at > ? "
        "GROUP BY st.id "
        "HAVING article_count >= ? "
        "ORDER BY st.last_seen DESC",
        (since, min_articles),
    )
    stories = []
    for row in cur.fetchall():
        story = dict(row)
        cur2 = conn.execute(
            "SELECT a.*, s.name AS source_name FROM articles a "
            "JOIN story_articles sa ON sa.article_id = a.id "
            "JOIN sources s ON s.id = a.source_id "
            "WHERE sa.story_id = ? AND a.crawled_at > ? "
            "ORDER BY a.crawled_at",
            (row["id"], since),
        )
        story["articles"] = [dict(r) for r in cur2.fetchall()]
        stories.append(story)
    return stories


def get_articles_since(conn, since: float, limit: int = 50):
    """Get all articles crawled after `since` timestamp."""
    cur = conn.execute(
        "SELECT a.*, s.name AS source_name FROM articles a "
        "JOIN sources s ON s.id = a.source_id "
        "WHERE a.crawled_at > ? ORDER BY a.crawled_at DESC LIMIT ?",
        (since, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def article_count_since(conn, since: float):
    """Total articles across all sources since timestamp."""
    cur = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE crawled_at > ?", (since,)
    )
    return cur.fetchone()[0]


def search_articles(conn, query: str, limit: int = 20):
    """Search across headlines AND article text."""
    pattern = f"%{query}%"
    cur = conn.execute(
        "SELECT a.*, s.name AS source_name FROM articles a "
        "JOIN sources s ON s.id = a.source_id "
        "WHERE a.title LIKE ? OR a.text LIKE ? "
        "ORDER BY a.crawled_at DESC LIMIT ?",
        (pattern, pattern, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def get_health(conn, days: int = 7):
    """Get health summary per source for last N days."""
    cur = conn.execute(
        "SELECT s.name, h.date, h.attempts, h.successes, h.failures, "
        "h.total_articles, "
        "CASE WHEN h.latency_count > 0 THEN h.avg_latency / h.latency_count ELSE 0 END AS avg_latency "
        "FROM health_daily h "
        "JOIN sources s ON s.id = h.source_id "
        "WHERE h.date >= date('now', '-' || ? || ' days') "
        "ORDER BY h.date DESC, s.name",
        (days,),
    )
    return [dict(r) for r in cur.fetchall()]
