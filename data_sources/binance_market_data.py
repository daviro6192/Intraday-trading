"""Dati di mercato dai futures USDT-M perpetual di Binance (`fapi.binance.com`,
API pubblica, nessuna API key richiesta per i soli dati di mercato).

Fonte per: prezzo di mark (fill/mark-to-market del PaperBroker), funding rate
(accrual periodico simulato) e klines recenti (trigger tecnico di timing
dell'Order Agent). Le commissioni maker/taker NON sono ottenibili da un
endpoint pubblico (sono account-specific): restano assunte in
config/trading.yaml (vedi common.schemas.FeeSchedule)."""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://fapi.binance.com"
_DEFAULT_TIMEOUT_SECONDS = 10
_USER_AGENT = "intraday-trading-bot/0.1"


def get_mark_price(symbol: str) -> float | None:
    """Prezzo di mark corrente per una coppia perpetual (es. BTCUSDT).
    Ritorna None se Binance non è raggiungibile: il chiamante deve degradare
    con un fallback (es. ultimo prezzo noto), mai propagare l'eccezione."""
    try:
        response = requests.get(
            f"{_BASE_URL}/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            headers={"User-Agent": _USER_AGENT},
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        logger.warning("Binance non raggiungibile: impossibile recuperare il mark price per %s", symbol)
        return None

    mark_price = data.get("markPrice")
    return float(mark_price) if mark_price is not None else None


def get_funding_rate(symbol: str) -> float | None:
    """Ultimo funding rate noto per una coppia perpetual (frazione, non
    percentuale: es. 0.0001 = 0.01%). Ritorna None se non disponibile."""
    try:
        response = requests.get(
            f"{_BASE_URL}/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            headers={"User-Agent": _USER_AGENT},
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        logger.warning("Binance non raggiungibile: impossibile recuperare il funding rate per %s", symbol)
        return None

    funding_rate = data.get("lastFundingRate")
    return float(funding_rate) if funding_rate is not None else None


def get_recent_klines(symbol: str, interval: str = "1m", limit: int = 50) -> list[dict] | None:
    """Ultime `limit` candele per una coppia perpetual. Ritorna None se non
    disponibile. Ogni candela: open/high/low/close/volume/close_time."""
    try:
        response = requests.get(
            f"{_BASE_URL}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            headers={"User-Agent": _USER_AGENT},
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        raw_klines = response.json()
    except (requests.RequestException, ValueError):
        logger.warning("Binance non raggiungibile: impossibile recuperare le klines per %s", symbol)
        return None

    return [
        {
            "open_time": kline[0],
            "open": float(kline[1]),
            "high": float(kline[2]),
            "low": float(kline[3]),
            "close": float(kline[4]),
            "volume": float(kline[5]),
            "close_time": kline[6],
        }
        for kline in raw_klines
    ]


def fetch_24h_ticker_stats(binance_perps: list[str]) -> dict[str, dict]:
    """Variazione di prezzo % e volume scambiato (in USDT) nelle ultime 24h
    per un insieme di coppie perpetual: una sola chiamata (l'endpoint senza
    `symbol` restituisce tutte le coppie, filtriamo lato client), usata dallo
    screener di simboli per stimare quali sono i più volatili/liquidi di
    giornata. Ritorna un dict vuoto (mai un'eccezione) se Binance non è
    raggiungibile o i dati sono malformati: il chiamante deve degradare con
    un fallback statico."""
    try:
        response = requests.get(
            f"{_BASE_URL}/fapi/v1/ticker/24hr",
            headers={"User-Agent": _USER_AGENT},
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        logger.warning("Binance non raggiungibile: impossibile recuperare le statistiche 24h")
        return {}

    wanted = set(binance_perps)
    stats: dict[str, dict] = {}
    for item in data:
        symbol = item.get("symbol")
        if symbol not in wanted:
            continue
        try:
            stats[symbol] = {
                "price_change_24h_pct": float(item["priceChangePercent"]),
                "quote_volume_24h_usdt": float(item["quoteVolume"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return stats


def validate_symbols_exist(symbols: list[str]) -> list[str]:
    """Verifica quali delle coppie perpetual configurate esistono realmente su
    Binance futures, interrogando /fapi/v1/exchangeInfo una volta sola.
    Ritorna la lista dei simboli richiesti che NON sono stati trovati (lista
    vuota se sono tutti validi, o se Binance non è raggiungibile — in
    quest'ultimo caso non si può affermare che manchino, quindi si evita un
    falso allarme)."""
    try:
        response = requests.get(
            f"{_BASE_URL}/fapi/v1/exchangeInfo",
            headers={"User-Agent": _USER_AGENT},
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        logger.warning("Binance non raggiungibile: impossibile validare le coppie perpetual %s", symbols)
        return []

    available = {item["symbol"] for item in data.get("symbols", [])}
    return [symbol for symbol in symbols if symbol not in available]
