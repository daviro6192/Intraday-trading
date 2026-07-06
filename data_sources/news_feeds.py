"""Aggregazione di news macro/finanziarie da feed RSS pubblici e gratuiti."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import feedparser

from common.schemas import NewsItem

logger = logging.getLogger(__name__)


def _parse_published_at(entry: dict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)


def fetch_feed(name: str, url: str, max_items: int = 15, timeout: int = 10) -> list[NewsItem]:
    """Scarica e parsa un singolo feed RSS. Ritorna lista vuota in caso di errore
    (una singola fonte non disponibile non deve bloccare l'intera pipeline)."""
    try:
        parsed_feed = feedparser.parse(url, request_headers={"User-Agent": "intraday-trading-bot/0.1"})
    except Exception:
        logger.exception("Errore nel parsing del feed %s (%s)", name, url)
        return []

    if getattr(parsed_feed, "bozo", False) and not parsed_feed.entries:
        logger.warning("Feed %s (%s) non valido o irraggiungibile", name, url)
        return []

    items: list[NewsItem] = []
    for entry in parsed_feed.entries[:max_items]:
        items.append(
            NewsItem(
                title=entry.get("title", "").strip(),
                summary=entry.get("summary", "").strip(),
                source=name,
                url=entry.get("link", ""),
                published_at=_parse_published_at(entry),
            )
        )
    return items


def fetch_all_feeds(feed_configs: list[dict], max_items_per_feed: int = 15) -> list[NewsItem]:
    """Scarica tutti i feed configurati in trading.yaml e deduplica per titolo."""
    all_items: list[NewsItem] = []
    seen_titles: set[str] = set()

    for feed in feed_configs:
        for item in fetch_feed(feed["name"], feed["url"], max_items=max_items_per_feed):
            key = item.title.lower().strip()
            if key and key not in seen_titles:
                seen_titles.add(key)
                all_items.append(item)

    all_items.sort(key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return all_items
