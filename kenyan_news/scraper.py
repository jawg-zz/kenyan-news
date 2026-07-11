"""
Scraping logic — newspaper4k (static), Playwright (JS), and RSS feeds.
"""

import logging
import time
from urllib.parse import urlparse

import httpx

from . import db, breaking

log = logging.getLogger(__name__)

CHROME_PATH = "/opt/hermes/.playwright/chromium-1228/chrome-linux/chrome"

# ─── Supported sources ───────────────────────────────────────────────

# ─── Google News RSS (primary source) ───────────────────────────────

GOOGLE_NEWS = {
    "url": "https://news.google.com/rss/search?q=kenya&hl=en-KE&gl=KE&ceid=KE:en",
    "mode": "google-news",
}

# ─── Site-specific scrapers (fallbacks) ─────────────────────────────

SOURCES = {
    "kenyans": {
        "url": "https://www.kenyans.co.ke/news",
        "mode": "lxml",
    },
    "citizen": {
        "url": "https://www.citizen.digital/",
        "mode": "playwright",
    },
    "the-star": {
        "url": "https://www.the-star.co.ke/",
        "mode": "playwright",
    },
    "standard": {
        "url": "https://www.standardmedia.co.ke/",
        "mode": "playwright",
    },
    "tuko": {
        "url": "https://www.tuko.co.ke/",
        "mode": "playwright",
    },
    "capitalfm": {
        "url": "https://www.capitalfm.co.ke/news/",
        "mode": "playwright",
    },
}

# RSS-based sources — pure HTTP + feedparser
# NOTE: Many Kenyan sites behind Cloudflare or removed RSS (Nation 403, The Star 404)
RSS_SOURCES = {
}


# ─── Scrapers ────────────────────────────────────────────────────────

def retry(max_attempts=3, delay=2):
    """Decorator for simple retry with exponential backoff."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    log.warning("Attempt %d/%d failed: %s", attempt, max_attempts, e)
                    if attempt < max_attempts:
                        time.sleep(delay * attempt)
            raise last_exc
        return wrapper
    return decorator


@retry(max_attempts=2, delay=2)
def fetch_lxml(url: str, link_pattern: str = "/news/") -> list[dict]:
    """Fetch headlines using httpx + lxml (fast, no browser)."""
    import httpx
    from lxml import html as lhtml

    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"}
    resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    tree = lhtml.fromstring(resp.text)

    articles = []
    seen = set()
    for a in tree.xpath(f'//a[contains(@href, "{link_pattern}")]'):
        href = a.get("href", "")
        text = (a.text_content() or "").strip()
        if not href or not text or len(text) < 15 or text in seen:
            continue
        seen.add(text)
        if href.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        parent = a.getparent()
        img = parent.xpath(".//img/@src") if parent is not None else []
        img_src = img[0] if img else ""
        articles.append({"title": text[:120], "url": href, "top_image": img_src})
        if len(articles) >= 10:
            break
    return articles


@retry(max_attempts=2, delay=2)
def fetch_rss(rss_url: str) -> list[dict]:
    """Fetch headlines from an RSS/Atom feed — no browser needed."""
    import feedparser
    import httpx
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; KenyanNewsBot/2.0; +https://spidmax.win)",
    }
    resp = httpx.get(rss_url, headers=headers, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    articles = []
    for entry in feed.entries[:15]:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        # Extract image from media:content or enclosure
        top_image = ""
        if hasattr(entry, "media_content") and entry.media_content:
            top_image = entry.media_content[0].get("url", "")
        elif hasattr(entry, "enclosures") and entry.enclosures:
            top_image = entry.enclosures[0].get("href", "")
        articles.append({
            "title": title,
            "url": link,
            "top_image": top_image,
            "published": entry.get("published", ""),
        })
    return articles


@retry(max_attempts=2, delay=2)
def fetch_google_news() -> list[dict]:
    """Fetch headlines from Google News RSS (covers ALL Kenyan outlets)."""
    import feedparser

    url = "https://news.google.com/rss/search?q=kenya&hl=en-KE&gl=KE&ceid=KE:en"
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)

    # Known Kenyan outlet domains for source filtering
    kenyan_domains = {
        "nation.africa", "the-star.co.ke", "standardmedia.co.ke", "citizen.digital",
        "kenyans.co.ke", "tuko.co.ke", "capitalfm.africa", "ntvkenya.co.ke",
        "kbc.co.ke", "businessdailyafrica.com", "theeastafrican.co.ke",
    }

    articles = []
    seen_urls = set()
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link or link in seen_urls:
            continue

        # Get source name from RSS source tag
        src_tag = entry.get("source", {}) if hasattr(entry, "source") else {}
        source_name = (src_tag.get("title") or "").strip() if isinstance(src_tag, dict) else ""

        # Filter: only Kenyan outlets or general Kenya news
        is_kenyan = any(d in link.lower() for d in kenyan_domains)
        is_kenyan = is_kenyan or any(k in title.lower() for k in ("kenya", "kenyan", "nairobi", "ruto"))

        if not is_kenyan and source_name and "kenya" not in source_name.lower():
            continue

        seen_urls.add(link)

        # Extract image from media:content, media:thumbnail, or enclosure
        top_image = ""
        if hasattr(entry, "media_content") and entry.media_content:
            top_image = entry.media_content[0].get("url", "")
        elif hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
            top_image = entry.media_thumbnail[0].get("url", "")
        elif hasattr(entry, "enclosures") and entry.enclosures:
            top_image = entry.enclosures[0].get("href", "")

        articles.append({
            "title": title,
            "url": link,
            "top_image": top_image,
            "source_name": source_name or "Google News",
            "published": entry.get("published", ""),
        })

        if len(articles) >= 25:
            break

    return articles


# ─── Playwright selectors per site ───────────────────────────────────

PLAYWRIGHT_JS = {
    "citizen": """
        () => {
            const containers = document.querySelectorAll('[class*="card_"], [class*="featured_"]');
            const articles = [];
            const seen = new Set();
            containers.forEach(container => {
                const link = container.querySelector('a[href*="/article/"]');
                if (!link) return;
                const raw = (link.textContent || '').trim();
                const parts = raw.split('\\\\n').map(s => s.trim()).filter(s => s.length > 10);
                const sorted = [...parts].sort((a, b) => a.length - b.length);
                const title = sorted.find(s => !s.match(/^[•\\\\-]|^(News|Sports|Business|By\\\\s|\\\\d+\\\\s*(minutes?|hours?|days?))/i)) || parts[0];
                if (title.length < 15 || seen.has(title)) return;
                seen.add(title);
                const img = container.querySelector('img');
                articles.push({
                    title: title.slice(0, 120),
                    url: link.href,
                    top_image: img ? (img.src || img.getAttribute('data-src') || '') : ''
                });
            });
            return articles.slice(0, 10);
        }
    """,
    "the-star": """
        () => {
            const links = document.querySelectorAll('article a, .article a, h3 a, h2 a, .card a, [class*="title"] a');
            const seen = new Set();
            return Array.from(links).filter(a => {
                const text = (a.textContent || '').trim();
                return text.length > 20 && text.length < 200 && !seen.has(text) && seen.add(text);
            }).slice(0, 10).map(a => {
                const img = a.querySelector('img') || a.closest('article')?.querySelector('img') || a.parentElement.querySelector('img');
                return {
                    title: (a.textContent || '').trim().slice(0, 120),
                    url: a.href,
                    top_image: img ? (img.src || img.getAttribute('data-src') || '') : ''
                };
            });
        }
    """,
    "standard": """
        () => {
            window.scrollTo(0, document.body.scrollHeight);
            const articles = [];
            const seen = new Set();
            const containers = document.querySelectorAll('div[class*="col-"], div.mb-4');
            containers.forEach(container => {
                const links = container.querySelectorAll('a[href*="/article/"], a[href*="/opinion/"], a[href*="/national/"], a[href*="/politics/"], a[href*="/education/"]');
                let link = null;
                for (const l of links) {
                    if ((l.textContent || '').trim().length > 20) { link = l; break; }
                }
                if (!link) return;
                const title = (link.textContent || '').trim();
                if (title.length < 20 || seen.has(title)) return;
                seen.add(title);
                let imgSrc = '';
                const imgs = container.querySelectorAll('img');
                for (const img of imgs) {
                    const src = img.src || '';
                    const original = img.getAttribute('data-original') || '';
                    if (src.includes('logo') || src.includes('flagcdn') || src.includes('icon')) continue;
                    const realSrc = (src.startsWith('data:') && original) ? original : src;
                    if (realSrc.includes('cdn.standardmedia.co.ke') && img.naturalWidth > 50) {
                        imgSrc = realSrc; break;
                    }
                }
                articles.push({ title: title.slice(0,120), url: link.href, top_image: imgSrc });
            });
            return articles.slice(0, 10);
        }
    """,
    "tuko": """
        () => {
            const cards = document.querySelectorAll('[class*="article-card"], [class*="c-article-card"], [class*="card-breaking"]');
            const articles = [];
            const seen = new Set();
            cards.forEach(card => {
                const link = card.querySelector('a[href*="tuko"]');
                if (!link) return;
                const text = (link.textContent || '').trim();
                if (text.length < 20 || seen.has(text)) return;
                seen.add(text);
                const img = card.querySelector('img');
                const src = img ? (img.src || img.getAttribute('data-src') || '') : '';
                articles.push({ title: text.slice(0, 120), url: link.href, top_image: src.startsWith('http') ? src : '' });
            });
            return articles.slice(0, 10);
        }
    """,
    "capitalfm": """
        () => {
            const links = document.querySelectorAll('a');
            const articles = [];
            const seen = new Set();
            links.forEach(a => {
                const text = (a.textContent || '').trim();
                const href = a.href || '';
                if (text.length < 25 || seen.has(text) || !href.includes('capitalfm.africa')) return;
                if (!/^[A-Z]/.test(text)) return;
                seen.add(text);
                let img = '';
                let parent = a.parentElement;
                for (let i = 0; i < 3; i++) {
                    if (!parent) break;
                    const i2 = parent.querySelector('img');
                    if (i2) { img = i2.src || i2.getAttribute('data-src') || ''; break; }
                    parent = parent.parentElement;
                }
                articles.push({ title: text.slice(0, 120), url: href, top_image: img });
            });
            return articles.slice(0, 10);
        }
    """,
}


@retry(max_attempts=2, delay=3)
def fetch_playwright(url: str, site_name: str) -> list[dict]:
    """Fetch headlines using Playwright (handles JS-rendered pages)."""
    from playwright.sync_api import sync_playwright

    js = PLAYWRIGHT_JS.get(site_name)
    if not js:
        raise ValueError(f"No selector for site: {site_name}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME_PATH, headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(8000)
        if site_name == "standard":
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000)
        articles = page.evaluate(js)
        browser.close()
        return articles


# ─── Unified crawler ─────────────────────────────────────────────────

def crawl_source(conn, source_name: str) -> dict:
    """
    Crawl a single source and persist new articles to the database.
    Returns crawl stats dict.
    """
    cfg = SOURCES.get(source_name) or RSS_SOURCES.get(source_name)
    if not cfg:
        return {"source": source_name, "found": 0, "new": 0, "success": False, "error": "unknown source"}

    mode = cfg.get("mode", "rss")
    url = cfg.get("rss_url") if mode == "rss" else cfg["url"]

    src_id = db.get_or_create_source(conn, source_name, cfg["url"], mode)
    event_id = db.start_crawl(conn, source_id=src_id)
    t0 = time.time()

    try:
        if mode == "lxml":
            articles = fetch_lxml(url)
        elif mode == "playwright":
            articles = fetch_playwright(url, source_name)
        elif mode == "rss":
            articles = fetch_rss(url)
        else:
            raise ValueError(f"Unknown mode: {mode}")

        latency = time.time() - t0
        new_count = 0
        enriched = 0
        for a in articles:
            art_id, is_new = db.upsert_article(
                conn, src_id, a["url"], a["title"],
                top_image=a.get("top_image", ""),
                published=a.get("published", ""),
            )
            if is_new:
                new_count += 1
            db.find_or_create_story(conn, art_id, a["title"], a.get("top_image", ""))

            # Fetch full text for breaking/scored articles immediately
            if is_new and len(a.get("title", "")) > 15:
                urgency = breaking.score_article(a["title"], source_name)
                if urgency >= 4:
                    try:
                        content = fetch_article(a["url"])
                        text = content.get("text", "")
                        if text and len(text) > 200 and not content.get("paywalled"):
                            conn.execute(
                                "UPDATE articles SET text=?, text_fetched=1 WHERE id=?",
                                (text[:50000], art_id),
                            )
                            enriched += 1
                    except Exception:
                        pass

        db.finish_crawl(conn, event_id, len(articles), new_count, success=True)
        db.record_health(conn, src_id, success=True, latency=latency, articles_found=len(articles))
        conn.commit()

        log.info("%s: %d articles, %d new (%.1fs)", source_name, len(articles), new_count, latency)
        return {"source": source_name, "found": len(articles), "new": new_count, "success": True}

    except Exception as e:
        latency = time.time() - t0
        err = str(e)
        log.error("%s failed: %s", source_name, err)
        db.finish_crawl(conn, event_id, 0, 0, success=False, error_msg=err)
        db.record_health(conn, src_id, success=False, latency=latency, articles_found=0)
        db.commit()
        return {"source": source_name, "found": 0, "new": 0, "success": False, "error": err}


def crawl_google_news(conn) -> dict:
    """Fetch Google News RSS and persist articles. Replaces all individual scrapers."""
    source_name = "google-news"
    url = "https://news.google.com/rss/search?q=kenya&hl=en-KE&gl=KE&ceid=KE:en"
    mode = "google-news"

    src_id = db.get_or_create_source(conn, source_name, url, mode)
    event_id = db.start_crawl(conn, source_id=src_id)
    t0 = time.time()

    try:
        articles = fetch_google_news()
        latency = time.time() - t0
        new_count = 0

        for a in articles:
            src = a.get("source_name", "") or source_name
            # Map source to a consistent name for source_id lookup
            mapped_name = _map_source_name(src)
            mapped_id = db.get_or_create_source(conn, mapped_name, url, "google-news")

            art_id, is_new = db.upsert_article(
                conn, mapped_id, a["url"], a["title"],
                top_image=a.get("top_image", ""),
                published=a.get("published", ""),
            )
            if is_new:
                new_count += 1
            db.find_or_create_story(conn, art_id, a["title"], a.get("top_image", ""))

            # Fetch full text for breaking-scored articles
            if is_new and len(a.get("title", "")) > 15:
                urgency = breaking.score_article(a["title"], src)
                if urgency >= 4:
                    try:
                        content = fetch_article(a["url"])
                        text = content.get("text", "")
                        if text and len(text) > 200 and not content.get("paywalled"):
                            conn.execute(
                                "UPDATE articles SET text=?, text_fetched=1 WHERE id=?",
                                (text[:50000], art_id),
                            )
                    except Exception:
                        pass

        db.finish_crawl(conn, event_id, len(articles), new_count, success=True)
        db.record_health(conn, src_id, success=True, latency=latency, articles_found=len(articles))
        conn.commit()

        log.info("%s: %d articles, %d new (%.1fs)", source_name, len(articles), new_count, latency)
        return {"source": source_name, "found": len(articles), "new": new_count, "success": True}

    except Exception as e:
        latency = time.time() - t0
        err = str(e)
        log.error("%s failed: %s", source_name, err)
        db.finish_crawl(conn, event_id, 0, 0, success=False, error_msg=err)
        db.record_health(conn, src_id, success=False, latency=latency, articles_found=0)
        conn.commit()
        return {"source": source_name, "found": 0, "new": 0, "success": False, "error": err}


def _map_source_name(raw: str) -> str:
    """Map Google News source names to consistent slug."""
    mapping = {
        "daily nation": "nation",
        "nation.africa": "nation",
        "the star": "the-star",
        "the-star.co.ke": "the-star",
        "standard media": "standard",
        "standardmedia.co.ke": "standard",
        "citizen digital": "citizen",
        "kenyans.co.ke": "kenyans",
        "tuko.co.ke": "tuko",
        "capital fm": "capitalfm",
        "capitalfm.africa": "capitalfm",
        "ntv": "ntv",
        "kbc": "kbc",
        "business daily": "business-daily",
        "the east african": "east-african",
    }
    key = raw.lower().strip()
    return mapping.get(key, raw.lower().replace(" ", "-")[:20])


def crawl_all(conn, sources: list[str] | None = None) -> list[dict]:
    """Crawl specified sources (or all available).

    Default behaviour: fetch Google News RSS (covers all Kenyan outlets in one call, ~0.5s).
    Pass specific source names to fall back to individual site scrapers (Playwright, ~90s total).
    """
    # Google News is the default primary source
    if sources is None:
        return [crawl_google_news(conn)]

    targets = sources
    results = []
    for name in targets:
        results.append(crawl_source(conn, name))
    return results


# ─── Full article content fetcher ────────────────────────────────────

def fetch_article(url: str) -> dict:
    """Fetch full article using the best available method."""
    result = {"paywalled": False}

    paywalled_domains = ["standardmedia.co.ke"]
    if any(d in url for d in paywalled_domains):
        result = _read_article_playwright(url)
        result["paywalled"] = "subscribe" in result.get("text", "").lower()[:800]
        return result

    # Try newspaper4k first (covers Kenyans, most static sites)
    try:
        result = _read_article_newspaper4k(url)
        text = result.get("text", "")
        if text and len(text) > 200 and "subscribe" not in text.lower()[:500]:
            return result
        result["paywalled"] = "subscribe" in text.lower()[:500]
    except Exception:
        pass

    # Fallback to Playwright
    pw = _read_article_playwright(url)
    pw["paywalled"] = "subscribe" in pw.get("text", "").lower()[:800]
    return pw


def _read_article_newspaper4k(url: str) -> dict:
    from newspaper import Article
    a = Article(url)
    a.download()
    a.parse()
    return {
        "title": a.title,
        "authors": a.authors,
        "publish_date": str(a.publish_date) if a.publish_date else "",
        "text": a.text,
        "top_image": a.top_image or "",
    }


def _read_article_playwright(url: str) -> dict:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME_PATH, headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)

        if "standardmedia" in url:
            page.evaluate("""() => {
                document.querySelectorAll('[class*="popup"], [class*="modal"], [class*="overlay"], [id*="popup"]').forEach(el => el.remove());
                document.querySelectorAll('.modal-backdrop, .popup-backdrop').forEach(el => el.remove());
                document.body.style.overflow = '';
                document.body.style.position = '';
                window.scrollTo(0, document.body.scrollHeight);
            }""")
            page.wait_for_timeout(3000)
            try:
                close_btn = page.query_selector('[class*="close"], [class*="dismiss"], button:has-text("Continue"), .modal button')
                if close_btn:
                    close_btn.click()
                    page.wait_for_timeout(1000)
            except Exception:
                pass

        data = page.evaluate("""() => {
            const candidates = [
                document.querySelector('article'),
                document.querySelector('[class*="story-body"]'),
                document.querySelector('[class*="article-body"]'),
                document.querySelector('[class*="content-body"]'),
                document.querySelector('[class*="post-content"]'),
                document.querySelector('main'),
            ];
            for (const el of candidates) {
                if (el) {
                    const text = el.innerText.trim();
                    if (text.length > 200) return { title: document.title, text: text.slice(0, 10000) };
                }
            }
            const pars = document.querySelectorAll('p');
            const texts = Array.from(pars).map(p => p.innerText.trim()).filter(t => t.length > 40);
            return { title: document.title, text: texts.slice(0, 50).join('\\n\\n') };
        }""")

        img = page.evaluate("""() => {
            const img = document.querySelector('meta[property="og:image"]');
            if (img) return img.content;
            const lead = document.querySelector('article img, [class*="featured"] img, [class*="hero"] img');
            return lead ? (lead.src || '') : '';
        }""")

        browser.close()
        return {
            "title": data.get("title", ""),
            "text": data.get("text", ""),
            "top_image": img or "",
        }
