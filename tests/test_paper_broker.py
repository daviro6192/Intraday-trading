"""Verifica il comportamento "realistico" del PaperBroker: fill al prezzo di
mercato corrente (con fallback offline), prezzo medio ponderato quando si
aggiunge a una posizione, P&L realizzato alla chiusura, e mark-to-market
delle posizioni aperte in get_account_state()."""

from __future__ import annotations

import itertools

from broker.paper_broker import PaperBroker
from common.schemas import RiskDecision, RiskDecisionStatus
from tests.conftest import make_order_proposal


def _decision(**kwargs) -> RiskDecision:
    proposal = make_order_proposal(**kwargs)
    return RiskDecision(
        proposal=proposal,
        status=RiskDecisionStatus.APPROVED,
        reasoning="test",
    )


def test_fill_uses_proposal_price_when_market_data_unavailable(monkeypatch):
    monkeypatch.setattr("broker.paper_broker.get_market_snapshot", lambda symbol, market: None)
    monkeypatch.setattr("broker.paper_broker.random.uniform", lambda a, b: 0.0)

    broker = PaperBroker(starting_cash=100_000)
    result = broker.place_bracket_order(_decision(entry_price=100.0, proposed_quantity=10))

    assert result.avg_fill_price == 100.0


def test_fill_uses_live_market_price_when_available(monkeypatch):
    monkeypatch.setattr("broker.paper_broker.get_market_snapshot", lambda symbol, market: {"last_price": 105.0})
    monkeypatch.setattr("broker.paper_broker.random.uniform", lambda a, b: 0.0)

    broker = PaperBroker(starting_cash=100_000)
    # Il proposal propone 100, ma il "mercato" è a 105: il fill deve riflettere il mercato.
    result = broker.place_bracket_order(_decision(entry_price=100.0, proposed_quantity=10))

    assert result.avg_fill_price == 105.0


def test_average_cost_when_adding_to_existing_position(monkeypatch):
    monkeypatch.setattr("broker.paper_broker.random.uniform", lambda a, b: 0.0)
    # I primi due valori sono i prezzi di fill dei due ordini; eventuali
    # chiamate successive (es. il mark-to-market di get_account_state)
    # continuano a ricevere l'ultimo prezzo, per non esaurire l'iteratore.
    prices = itertools.chain([100.0, 200.0], itertools.repeat(200.0))
    monkeypatch.setattr("broker.paper_broker.get_market_snapshot", lambda symbol, market: {"last_price": next(prices)})

    broker = PaperBroker(starting_cash=1_000_000)
    broker.place_bracket_order(_decision(symbol="BTC/USD", entry_price=100.0, proposed_quantity=10))
    broker.place_bracket_order(_decision(symbol="BTC/USD", entry_price=100.0, proposed_quantity=10))

    position = broker.get_account_state().open_positions[0]
    assert position.quantity == 20
    assert position.avg_price == 150.0  # media pesata tra 100 (x10) e 200 (x10)


def test_realized_pnl_on_closing_a_long_position(monkeypatch):
    monkeypatch.setattr("broker.paper_broker.random.uniform", lambda a, b: 0.0)
    prices = iter([100.0, 130.0])
    monkeypatch.setattr("broker.paper_broker.get_market_snapshot", lambda symbol, market: {"last_price": next(prices)})

    broker = PaperBroker(starting_cash=100_000)
    broker.place_bracket_order(_decision(symbol="BTC/USD", side="buy", entry_price=100.0, proposed_quantity=10))
    broker.place_bracket_order(_decision(symbol="BTC/USD", side="sell", entry_price=100.0, proposed_quantity=10))

    state = broker.get_account_state()
    assert state.realized_pnl_today == 300.0  # 10 unità x (130 - 100)
    assert state.open_positions == []


def test_mark_to_market_updates_unrealized_pnl_between_calls(monkeypatch):
    monkeypatch.setattr("broker.paper_broker.random.uniform", lambda a, b: 0.0)
    current_price = {"value": 100.0}
    monkeypatch.setattr(
        "broker.paper_broker.get_market_snapshot", lambda symbol, market: {"last_price": current_price["value"]}
    )

    broker = PaperBroker(starting_cash=100_000)
    broker.place_bracket_order(_decision(symbol="BTC/USD", entry_price=100.0, proposed_quantity=10))

    assert broker.get_account_state().unrealized_pnl_today == 0.0

    current_price["value"] = 120.0  # il "mercato" si muove tra un ciclo e l'altro
    state = broker.get_account_state()
    assert state.unrealized_pnl_today == 200.0  # 10 unità x (120 - 100)
