"""Dati di mercato quantitativi (prezzi, volumi, indicatori tecnici) via yfinance.

Copre azioni USA/EU, forex e crypto con un'unica interfaccia; i simboli sono
quelli usati in trading.yaml (formato "leggibile", es. EURUSD, BTC/USD) e
vengono tradotti nel formato richiesto da yfinance.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import ta
import yfinance as yf

from common.schemas import Market

logger = logging.getLogger(__name__)


def to_yfinance_symbol(symbol: str, market: Market) -> str:
    if market is Market.FOREX:
        return f"{symbol}=X"
    if market is Market.CRYPTO:
        return symbol.replace("/", "-")
    return symbol


def fetch_ohlcv(symbol: str, market: Market, period: str = "5d", interval: str = "15m") -> pd.DataFrame | None:
    """Scarica OHLCV per un simbolo. Ritorna None se il dato non è disponibile
    (yfinance non deve poter bloccare l'intera pipeline in caso di simbolo o
    orario non coperto)."""
    yf_symbol = to_yfinance_symbol(symbol, market)
    try:
        df = yf.Ticker(yf_symbol).history(period=period, interval=interval)
    except Exception:
        logger.exception("Errore nello scaricare dati per %s (%s)", symbol, yf_symbol)
        return None

    if df is None or df.empty:
        logger.warning("Nessun dato OHLCV disponibile per %s (%s)", symbol, yf_symbol)
        return None
    return df


def compute_technical_snapshot(df: pd.DataFrame) -> dict:
    """Calcola un piccolo set di indicatori tecnici standard sull'ultima candela disponibile."""
    close = df["Close"]

    snapshot: dict = {
        "last_price": float(close.iloc[-1]),
        "volume": float(df["Volume"].iloc[-1]) if "Volume" in df else None,
    }

    if len(close) >= 20:
        snapshot["sma_20"] = float(ta.trend.SMAIndicator(close, window=20).sma_indicator().iloc[-1])
    if len(close) >= 50:
        snapshot["sma_50"] = float(ta.trend.SMAIndicator(close, window=50).sma_indicator().iloc[-1])
    if len(close) >= 14:
        snapshot["rsi_14"] = float(ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1])
    if len(close) >= 26:
        macd = ta.trend.MACD(close)
        snapshot["macd"] = float(macd.macd().iloc[-1])
        snapshot["macd_signal"] = float(macd.macd_signal().iloc[-1])
    if len(df) >= 14 and {"High", "Low", "Close"}.issubset(df.columns):
        atr = ta.volatility.AverageTrueRange(df["High"], df["Low"], df["Close"], window=14)
        snapshot["atr_14"] = float(atr.average_true_range().iloc[-1])

    return snapshot


def get_market_snapshot(symbol: str, market: Market, period: str = "5d", interval: str = "15m") -> dict | None:
    """Ritorna prezzo corrente + indicatori tecnici per un simbolo, o None se non disponibile."""
    df = fetch_ohlcv(symbol, market, period=period, interval=interval)
    if df is None:
        return None
    snapshot = compute_technical_snapshot(df)
    snapshot["symbol"] = symbol
    snapshot["market"] = market.value
    return snapshot


def get_market_snapshots(symbols: list[tuple[str, Market]]) -> dict[str, dict]:
    """Ritorna gli snapshot per una lista di (simbolo, mercato) in parallelo,
    saltando quelli falliti."""
    if not symbols:
        return {}

    snapshots: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(symbols)) as executor:
        results = executor.map(lambda sm: (sm[0], get_market_snapshot(sm[0], sm[1])), symbols)
        for symbol, snapshot in results:
            if snapshot is not None:
                snapshots[symbol] = snapshot
    return snapshots
