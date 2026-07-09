"""Metriche fondamentali crypto (market cap, supply, dominance, performance di
lungo periodo) via l'API pubblica gratuita di CoinGecko. Nessuna API key
richiesta. Nessun dato tecnico/di prezzo a breve termine né sentiment/news:
solo fondamentali, per alimentare l'Agente 1 (Fundamental Agent)."""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.coingecko.com/api/v3"
_DEFAULT_TIMEOUT_SECONDS = 10
_USER_AGENT = "intraday-trading-bot/0.1"


def fetch_coin_fundamentals(coingecko_ids: list[str]) -> dict[str, dict]:
    """Ritorna un dict keyed by coingecko_id con le metriche fondamentali di
    ciascuna moneta (market cap/rank, supply, ATH/ATL, variazioni di prezzo su
    24h/7d/30d/1y). Una singola chiamata per tutte le monete richieste.

    Ritorna un dict vuoto (mai solleva) se CoinGecko non è raggiungibile: un
    ciclo di analisi non deve bloccarsi per un problema di rete transitorio."""
    if not coingecko_ids:
        return {}

    params = {
        "vs_currency": "usd",
        "ids": ",".join(coingecko_ids),
        "price_change_percentage": "24h,7d,30d,1y",
    }
    try:
        response = requests.get(
            f"{_BASE_URL}/coins/markets",
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        coins = response.json()
    except requests.RequestException:
        logger.warning("CoinGecko non raggiungibile: impossibile recuperare i fondamentali per %s", coingecko_ids)
        return {}
    except ValueError:
        logger.warning("Risposta CoinGecko non valida (JSON malformato) per %s", coingecko_ids)
        return {}

    fundamentals: dict[str, dict] = {}
    for coin in coins:
        coin_id = coin.get("id")
        if not coin_id:
            continue
        fundamentals[coin_id] = {
            "symbol": coin.get("symbol"),
            "current_price": coin.get("current_price"),
            "market_cap": coin.get("market_cap"),
            "market_cap_rank": coin.get("market_cap_rank"),
            "total_volume": coin.get("total_volume"),
            "circulating_supply": coin.get("circulating_supply"),
            "total_supply": coin.get("total_supply"),
            "max_supply": coin.get("max_supply"),
            "ath": coin.get("ath"),
            "ath_change_percentage": coin.get("ath_change_percentage"),
            "atl": coin.get("atl"),
            "atl_change_percentage": coin.get("atl_change_percentage"),
            "price_change_percentage_24h": coin.get("price_change_percentage_24h_in_currency"),
            "price_change_percentage_7d": coin.get("price_change_percentage_7d_in_currency"),
            "price_change_percentage_30d": coin.get("price_change_percentage_30d_in_currency"),
            "price_change_percentage_1y": coin.get("price_change_percentage_1y_in_currency"),
        }
    return fundamentals


def fetch_global_market_data() -> dict | None:
    """Ritorna dati di mercato crypto globali (dominance BTC, market cap
    totale). Ritorna None se non raggiungibile."""
    try:
        response = requests.get(
            f"{_BASE_URL}/global",
            headers={"User-Agent": _USER_AGENT},
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json().get("data", {})
    except requests.RequestException:
        logger.warning("CoinGecko non raggiungibile: impossibile recuperare i dati di mercato globali")
        return None
    except ValueError:
        logger.warning("Risposta CoinGecko non valida (JSON malformato) per /global")
        return None

    return {
        "total_market_cap_usd": data.get("total_market_cap", {}).get("usd"),
        "btc_dominance_pct": data.get("market_cap_percentage", {}).get("btc"),
    }
