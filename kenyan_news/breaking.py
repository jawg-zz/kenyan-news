"""
Breaking news detection — urgency scoring + alert generation.
"""

import logging
import re
import time

from . import db

log = logging.getLogger(__name__)

# Keywords that indicate urgency — tiered by severity
URGENT_WORDS = {
    # Tier 1 — immediate, life-safety
    "killed": 5, "death": 5, "dies": 5, "fatal": 5, "murder": 5,
    "crash": 4, "accident": 3, "collapse": 4, "explosion": 5,
    "fire": 4, "shoot": 4, "shooting": 5, "attack": 4,
    "blast": 5, "emergency": 4, "dead": 5,
    # Tier 2 — major events
    "breaking": 5, "urgent": 5,
    "arrest": 3, "charged": 3, "sentenced": 3,
    "resign": 3, "sack": 3, "fired": 3,
    "crisis": 4, "warning": 3, "alarm": 3,
    # Tier 3 — notable
    "court": 2, "lawsuit": 2, "ban": 3, "suspend": 3,
    "extradite": 3, "extradition": 3,
    "earthquake": 5, "flood": 4, "landslide": 4, "storm": 3,
}

# Trusted sources get a multiplier
TRUSTED_SOURCES = {"citizen": 1.2, "the-star": 1.1, "standard": 1.0, "capitalfm": 1.0}


def score_article(title: str, source_name: str) -> float:
    """
    Score an article for urgency.
    Returns 0 (not breaking) to ~25+ (very breaking).
    Threshold for alert: >= 5
    """
    title_lower = title.lower()
    score = 0.0

    # Keyword matching
    for word, weight in URGENT_WORDS.items():
        if word in title_lower:
            score += weight

    # Uppercase words = emphasis (e.g. BREAKING, URGENT)
    upper_words = re.findall(r'\b[A-Z]{4,}\b', title)
    score += len(upper_words) * 2

    # Exclamation marks
    score += title.count("!") * 2

    # Source trust multiplier
    multiplier = TRUSTED_SOURCES.get(source_name, 0.8)
    score *= multiplier

    return score


def check_articles(conn, source_name: str, limit: int = 10, threshold: int = 5) -> list[dict]:
    """
    Scan recent articles from a source and return those crossing the urgency threshold.
    Returns list of alert dicts with title, url, score, source.
    """
    articles = db.get_articles(conn, source=source_name, limit=limit)
    alerts = []
    for a in articles:
        urgency = score_article(a["title"], source_name)
        if urgency >= threshold:
            alerts.append({
                "title": a["title"],
                "url": a["url"],
                "source": source_name,
                "score": round(urgency, 1),
                "top_image": a.get("top_image", ""),
            })
    return alerts


def format_alert(alert: dict) -> str:
    """Format a breaking news alert for terminal / cron output."""
    urgency = alert["score"]
    emoji = "🚨" if urgency >= 8 else "🔴" if urgency >= 6 else "⚠️"
    tag = "BREAKING" if urgency >= 8 else "URGENT" if urgency >= 6 else "ALERT"
    return f"{emoji} [{tag}] [{alert['source']}] {alert['title']} — {alert['url']}"
