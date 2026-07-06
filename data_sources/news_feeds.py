"""Aggregazione di news macro/finanziarie da feed RSS pubblici e gratuiti."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import feedparser
import requests

from common.schemas import NewsItem

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 6
_USER_AGENT = "intraday-trading-bot/0.1"


def _parse_published_at(entry: dict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)


def fetch_feed(name: str, url: str, max_items: int = 15, timeout: int = _DEFAULT_TIMEOUT_SECONDS) -> list[NewsItem]:
    """Scarica e parsa un singolo feed RSS. Ritorna lista vuota in caso di errore
    o timeout (una singola fonte lenta/irraggiungibile non deve bloccare
    l'intera pipeline).

    Usa `requests` con un timeout esplicito invece di lasciare che
    `feedparser.parse()` scarichi l'URL da solo: quest'ultimo non espone un
    parametro di timeout affidabile in tutte le versioni, e affidarsi al
    timeout globale dei socket non è sicuro quando più feed vengono scaricati
    in parallelo (vedi fetch_all_feeds).
    """
    try:
        response = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException:
        logger.warning("Feed %s (%s) non raggiungibile entro %ss", name, url, timeout)
        return []

    parsed_feed = feedparser.parse(response.content)

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
    """Scarica in parallelo tutti i feed configurati in trading.yaml e deduplica
    per titolo. Il tempo totale è quindi vicino al feed più lento (rispettando
    il suo timeout), non alla somma di tutti i feed."""
    all_items: list[NewsItem] = []
    seen_titles: set[str] = set()

    if not feed_configs:
        return all_items

    with ThreadPoolExecutor(max_workers=len(feed_configs)) as executor:
        futures = [
            executor.submit(fetch_feed, feed["name"], feed["url"], max_items_per_feed) for feed in feed_configs
        ]
        for future in as_completed(futures):
            for item in future.result():
                key = item.title.lower().strip()
                if key and key not in seen_titles:
                    seen_titles.add(key)
                    all_items.append(item)

    all_items.sort(key=lambda i: i.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return all_items
