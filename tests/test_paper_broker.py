"""Verifica il comportamento del PaperBroker come simulatore di futures USDT-M
perpetual: fill al prezzo di mark corrente (con fallback offline), prezzo
medio ponderato e P&L realizzato su chiusura/riduzione, mark-to-market delle
posizioni aperte, margine/leva, fee, funding periodico e liquidazione."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from broker.paper_broker import PaperBroker
from common.schemas import FeeSchedule, OrderSide


def _fee_schedule(
    maker_fee_pct: float = 0.0002,
    taker_fee_pct: float = 0.0004,
    funding_interval_hours: int = 8,
    default_funding_rate_fallback_pct: float = 0.0001,
) -> FeeSchedule:
    return FeeSchedule(
        maker_fee_pct=maker_fee_pct,
        taker_fee_pct=taker_fee_pct,
        funding_interval_hours=funding_interval_hours,
        default_funding_rate_fallback_pct=default_funding_rate_fallback_pct,
    )


@pytest.fixture
def broker(monkeypatch) -> PaperBroker:
    monkeypatch.setattr("broker.paper_broker.random.uniform", lambda a, b: 0.0)
    return PaperBroker(fee_schedule=_fee_schedule(), starting_cash=100_000.0, maintenance_margin_rate_pct=0.005)


def test_fill_uses_reference_price_when_market_data_unavailable(broker, monkeypatch):
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: None)

    result = broker.place_order("BTCUSDT", OrderSide.BUY, quantity=1.0, leverage=1, reference_price=100.0)

    assert result.avg_fill_price == 100.0


def test_fill_uses_live_mark_price_when_available(broker, monkeypatch):
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 105.0)

    # Il chiamante si aspetta 100, ma il mercato è a 105: il fill deve riflettere il mercato.
    result = broker.place_order("BTCUSDT", OrderSide.BUY, quantity=1.0, leverage=1, reference_price=100.0)

    assert result.avg_fill_price == 105.0


def test_average_cost_when_adding_to_existing_position(broker, monkeypatch):
    prices = iter([100.0, 200.0])
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: next(prices, 200.0))

    broker.place_order("BTCUSDT", OrderSide.BUY, quantity=10, leverage=2, reference_price=100.0)
    broker.place_order("BTCUSDT", OrderSide.BUY, quantity=10, leverage=2, reference_price=100.0)

    position = broker.get_account_state().open_positions[0]
    assert position.quantity == 20
    assert position.avg_price == 150.0  # media pesata tra 100 (x10) e 200 (x10)
    assert position.initial_margin == pytest.approx(20 * 150.0 / 2)  # notional/leva


def test_realized_pnl_and_fee_on_closing_a_long_position(broker, monkeypatch):
    prices = iter([100.0, 130.0])
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: next(prices, 130.0))

    broker.place_order("BTCUSDT", OrderSide.BUY, quantity=10, leverage=1, reference_price=100.0)
    broker.close_position("BTCUSDT")

    state = broker.get_account_state()
    assert state.realized_pnl_today == pytest.approx(300.0)  # 10 unità x (130 - 100)
    assert state.open_positions == []
    assert state.fees_paid_today > 0
    # L'equity deve riflettere il P&L realizzato al netto delle fee, non solo le fee.
    assert state.equity == pytest.approx(100_000.0 + 300.0 - state.fees_paid_today)


def test_short_position_profits_when_price_falls(broker, monkeypatch):
    prices = iter([100.0, 80.0])
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: next(prices, 80.0))

    broker.place_order("BTCUSDT", OrderSide.SELL, quantity=10, leverage=1, reference_price=100.0)
    broker.close_position("BTCUSDT")

    state = broker.get_account_state()
    assert state.realized_pnl_today == pytest.approx(200.0)  # short: guadagna se il prezzo scende


def test_mark_to_market_updates_unrealized_pnl_between_calls(broker, monkeypatch):
    current_price = {"value": 100.0}
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: current_price["value"])

    broker.place_order("BTCUSDT", OrderSide.BUY, quantity=10, leverage=1, reference_price=100.0)
    assert broker.get_account_state().unrealized_pnl_today == 0.0

    current_price["value"] = 120.0  # il "mercato" si muove tra un ciclo e l'altro
    state = broker.get_account_state()
    assert state.unrealized_pnl_today == pytest.approx(200.0)  # 10 unità x (120 - 100)


def test_reduce_only_caps_quantity_instead_of_flipping(broker, monkeypatch):
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 100.0)

    broker.place_order("BTCUSDT", OrderSide.BUY, quantity=5, leverage=1, reference_price=100.0)
    # Un reduce_only che chiede più di quanto è aperto non deve flippare la posizione a short.
    broker.place_order("BTCUSDT", OrderSide.SELL, quantity=100, leverage=1, reference_price=100.0, reduce_only=True)

    assert broker.get_account_state().open_positions == []


def test_reduce_only_without_position_is_rejected(broker, monkeypatch):
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 100.0)

    result = broker.place_order("BTCUSDT", OrderSide.SELL, quantity=1, leverage=1, reference_price=100.0, reduce_only=True)

    assert result.status.value == "rejected"


def test_close_position_returns_none_when_no_position(broker):
    assert broker.close_position("BTCUSDT") is None


def test_insufficient_margin_rejects_order(monkeypatch):
    monkeypatch.setattr("broker.paper_broker.random.uniform", lambda a, b: 0.0)
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 50_000.0)
    broker = PaperBroker(fee_schedule=_fee_schedule(), starting_cash=1_000.0)

    # Notional 50_000 a leva 2x richiede 25_000 di margine, molto più dei 1_000 disponibili.
    result = broker.place_order("BTCUSDT", OrderSide.BUY, quantity=1.0, leverage=2, reference_price=50_000.0)

    assert result.status.value == "rejected"
    assert broker.get_account_state().open_positions == []


def test_liquidation_closes_position_and_realizes_loss(monkeypatch):
    monkeypatch.setattr("broker.paper_broker.random.uniform", lambda a, b: 0.0)
    broker = PaperBroker(fee_schedule=_fee_schedule(), starting_cash=10_000.0, maintenance_margin_rate_pct=0.005)

    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 50_000.0)
    broker.place_order("BTCUSDT", OrderSide.BUY, quantity=0.5, leverage=10, reference_price=50_000.0)
    position = broker.get_account_state().open_positions[0]
    assert position.liquidation_price == pytest.approx(50_000.0 * (1 - 1 / 10 + 0.005))

    # Il prezzo crolla sotto il livello di liquidazione.
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 44_000.0)
    state = broker.get_account_state()

    assert state.open_positions == []
    assert state.realized_pnl_today < 0
    # La perdita realizzata deve essere circa il margine iniziale (leva 10x, isolated margin semplificata).
    assert state.realized_pnl_today == pytest.approx(-2_375.0)


def test_funding_accrual_charges_long_position_when_rate_positive(monkeypatch):
    monkeypatch.setattr("broker.paper_broker.random.uniform", lambda a, b: 0.0)
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 100.0)
    monkeypatch.setattr("broker.paper_broker.get_funding_rate", lambda symbol: 0.001)

    broker = PaperBroker(fee_schedule=_fee_schedule(funding_interval_hours=8), starting_cash=100_000.0)
    broker.place_order("BTCUSDT", OrderSide.BUY, quantity=10, leverage=1, reference_price=100.0)

    equity_before = broker.get_account_state().equity

    # Simula il passaggio di un intero intervallo di funding.
    broker._last_funding_at["BTCUSDT"] = datetime.now(timezone.utc) - timedelta(hours=9)
    state = broker.get_account_state()

    # Long con funding rate positivo paga: notional 1000 x 0.001 = 1.0.
    assert state.funding_paid_today == pytest.approx(1.0)
    assert state.equity == pytest.approx(equity_before - 1.0)
