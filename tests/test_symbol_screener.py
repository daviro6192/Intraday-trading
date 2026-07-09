"""Lo screener sceglie i 3 simboli su cui tradare per la sessione, tra un
universo di candidati più ampio di un set fisso, in base a volatilità/
liquidità delle ultime 24h — mai reti reali qui, sempre mockato."""

from __future__ import annotations

from agents.symbol_screener_agent import SymbolScreenerAgent
from common.schemas import SymbolCandidate, SymbolSelection
from config.settings import trading_config
from orchestrator.factory import select_symbols_for_session
from tests.conftest import FakeClaudeClient


def _candidate(symbol: str, binance_perp: str, price_change_24h_pct: float, quote_volume: float = 1_000_000.0):
    return SymbolCandidate(
        symbol=symbol,
        binance_perp=binance_perp,
        coingecko_id=symbol.lower(),
        price_change_24h_pct=price_change_24h_pct,
        quote_volume_24h_usdt=quote_volume,
    )


def test_screener_agent_returns_claudes_selection():
    client = FakeClaudeClient(
        {"SymbolSelection": SymbolSelection(selected_binance_perps=["DOGEUSDT", "WIFUSDT", "TIAUSDT"], rationale="volatili oggi")}
    )
    candidates = [_candidate("BTC", "BTCUSDT", 0.5), _candidate("DOGE", "DOGEUSDT", 12.0)]

    selection = SymbolScreenerAgent(client).run(candidates, 3)

    assert selection.selected_binance_perps == ["DOGEUSDT", "WIFUSDT", "TIAUSDT"]
    assert selection.rationale == "volatili oggi"


def test_select_symbols_for_session_uses_screener_choice(monkeypatch):
    monkeypatch.setitem(trading_config, "symbols_per_session", 3)
    monkeypatch.setitem(
        trading_config,
        "symbol_universe",
        [
            {"symbol": "BTC", "coingecko_id": "bitcoin", "binance_perp": "BTCUSDT"},
            {"symbol": "DOGE", "coingecko_id": "dogecoin", "binance_perp": "DOGEUSDT"},
            {"symbol": "WIF", "coingecko_id": "dogwifcoin", "binance_perp": "WIFUSDT"},
            {"symbol": "TIA", "coingecko_id": "celestia", "binance_perp": "TIAUSDT"},
        ],
    )
    monkeypatch.setattr(
        "orchestrator.factory.fetch_24h_ticker_stats",
        lambda binance_perps: {p: {"price_change_24h_pct": 5.0, "quote_volume_24h_usdt": 1_000_000.0} for p in binance_perps},
    )
    client = FakeClaudeClient(
        {"SymbolSelection": SymbolSelection(selected_binance_perps=["DOGEUSDT", "WIFUSDT", "TIAUSDT"], rationale="non per forza BTC")}
    )

    symbols, rationale = select_symbols_for_session(client)

    assert set(symbols.keys()) == {"DOGEUSDT", "WIFUSDT", "TIAUSDT"}
    assert "BTCUSDT" not in symbols
    assert rationale == "non per forza BTC"


def test_select_symbols_for_session_falls_back_when_binance_unreachable(monkeypatch):
    monkeypatch.setattr("orchestrator.factory.fetch_24h_ticker_stats", lambda binance_perps: {})
    client = FakeClaudeClient({})  # non dovrebbe nemmeno essere interrogato

    symbols, rationale = select_symbols_for_session(client)

    assert symbols == trading_config["symbols_fallback"]
    assert "fallback" in rationale.lower()


def test_select_symbols_for_session_backfills_invalid_claude_picks(monkeypatch):
    monkeypatch.setitem(trading_config, "symbols_per_session", 3)
    monkeypatch.setitem(
        trading_config,
        "symbol_universe",
        [
            {"symbol": "BTC", "coingecko_id": "bitcoin", "binance_perp": "BTCUSDT"},
            {"symbol": "DOGE", "coingecko_id": "dogecoin", "binance_perp": "DOGEUSDT"},
            {"symbol": "WIF", "coingecko_id": "dogwifcoin", "binance_perp": "WIFUSDT"},
        ],
    )
    monkeypatch.setattr(
        "orchestrator.factory.fetch_24h_ticker_stats",
        lambda binance_perps: {
            "BTCUSDT": {"price_change_24h_pct": 1.0, "quote_volume_24h_usdt": 1_000_000.0},
            "DOGEUSDT": {"price_change_24h_pct": 9.0, "quote_volume_24h_usdt": 1_000_000.0},
            "WIFUSDT": {"price_change_24h_pct": 5.0, "quote_volume_24h_usdt": 1_000_000.0},
        },
    )
    # Claude propone un solo simbolo valido ("NOTREALUSDT" non è tra i candidati).
    client = FakeClaudeClient(
        {"SymbolSelection": SymbolSelection(selected_binance_perps=["BTCUSDT", "NOTREALUSDT", "ALSOFAKEUSDT"], rationale="parziale")}
    )

    symbols, rationale = select_symbols_for_session(client)

    assert len(symbols) == 3
    assert "BTCUSDT" in symbols
    # Completato deterministicamente con i più volatili rimasti (DOGE prima di WIF).
    assert set(symbols.keys()) == {"BTCUSDT", "DOGEUSDT", "WIFUSDT"}
