"""L'Order Agent (Agente 3) è meccanico: nessun FakeClaudeClient qui, solo
prezzi/klines scriptati e un PaperBroker reale (con Binance mockato) per
verificare che entrate/uscite long/short avvengano quando previsto."""

from __future__ import annotations

import pytest

from agents.order_agent import OrderAgent
from broker.paper_broker import PaperBroker
from common.schemas import FeeSchedule, RiskParameters, StrategyView, TradeDirection


def _fee_schedule() -> FeeSchedule:
    return FeeSchedule(
        maker_fee_pct=0.0002, taker_fee_pct=0.0004, funding_interval_hours=8, default_funding_rate_fallback_pct=0.0001
    )


def _risk_params(**overrides) -> RiskParameters:
    defaults = dict(
        max_leverage=10.0,
        max_position_notional_pct=1.0,
        max_daily_loss_pct=1.0,
        min_profit_over_fees_multiple=0.0,
        paused_symbols=[],
        rationale="test",
    )
    defaults.update(overrides)
    return RiskParameters(**defaults)


def _rising_klines(n: int = 25, start: float = 90.0, step: float = 0.5) -> list[dict]:
    """Serie di prezzi crescente: EMA veloce sopra la lenta -> segnale LONG."""
    return [
        {"open": start + i * step, "high": start + i * step + 0.1, "low": start + i * step - 0.1, "close": start + i * step, "volume": 10.0}
        for i in range(n)
    ]


def _falling_klines(n: int = 25, start: float = 100.0, step: float = 0.5) -> list[dict]:
    """Serie di prezzi decrescente: EMA veloce sotto la lenta -> segnale SHORT."""
    return [
        {"open": start - i * step, "high": start - i * step + 0.1, "low": start - i * step - 0.1, "close": start - i * step, "volume": 10.0}
        for i in range(n)
    ]


def _view(direction: TradeDirection, conviction: float = 0.7) -> StrategyView:
    return StrategyView(
        symbol="BTCUSDT",
        direction=direction,
        conviction=conviction,
        invalidation_condition="test",
        rationale="test",
    )


@pytest.fixture
def agent(monkeypatch) -> OrderAgent:
    monkeypatch.setattr("broker.paper_broker.random.uniform", lambda a, b: 0.0)
    broker = PaperBroker(fee_schedule=_fee_schedule(), starting_cash=1_000_000.0)
    return OrderAgent(broker=broker, fee_schedule=_fee_schedule(), default_leverage=2.0, max_risk_per_trade_pct=0.01)


def test_opens_long_position_when_view_and_technical_signal_agree(agent, monkeypatch):
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 100.0)

    tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.LONG),
        position=None,
        mark_price=100.0,
        klines=_rising_klines(),
        account=agent._broker.get_account_state(),
        risk_params=_risk_params(),
        current_stop_loss=None,
        current_take_profit=None,
    )

    assert tick.execution_result is not None
    assert tick.execution_result.status.value == "filled"
    assert tick.new_stop_loss is not None
    assert tick.new_stop_loss < 100.0  # stop sotto il prezzo di entrata per un long
    assert tick.new_take_profit > 100.0


def test_waits_when_technical_signal_disagrees_with_strategy_view(agent, monkeypatch):
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 100.0)

    # La strategia dice LONG ma il momentum tecnico recente è ribassista: aspetta.
    tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.LONG),
        position=None,
        mark_price=100.0,
        klines=_falling_klines(),
        account=agent._broker.get_account_state(),
        risk_params=_risk_params(),
        current_stop_loss=None,
        current_take_profit=None,
    )

    assert tick.execution_result is None
    assert tick.intent is None


def test_waits_when_not_enough_klines_for_technical_signal(agent, monkeypatch):
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 100.0)

    tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.LONG),
        position=None,
        mark_price=100.0,
        klines=_rising_klines(n=5),  # troppo poche candele
        account=agent._broker.get_account_state(),
        risk_params=_risk_params(),
        current_stop_loss=None,
        current_take_profit=None,
    )

    assert tick.execution_result is None


def test_closes_position_when_view_flips_to_flat(agent, monkeypatch):
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 100.0)
    open_tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.LONG),
        position=None,
        mark_price=100.0,
        klines=_rising_klines(),
        account=agent._broker.get_account_state(),
        risk_params=_risk_params(),
        current_stop_loss=None,
        current_take_profit=None,
    )
    assert open_tick.execution_result is not None

    position = agent._broker.get_account_state().open_positions[0]
    close_tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.FLAT),
        position=position,
        mark_price=100.0,
        klines=_rising_klines(),
        account=agent._broker.get_account_state(),
        risk_params=_risk_params(),
        current_stop_loss=open_tick.new_stop_loss,
        current_take_profit=open_tick.new_take_profit,
    )

    assert close_tick.execution_result is not None
    assert agent._broker.get_account_state().open_positions == []
    assert close_tick.new_stop_loss is None


def test_closes_position_when_stop_loss_breached(agent, monkeypatch):
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 100.0)
    open_tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.LONG),
        position=None,
        mark_price=100.0,
        klines=_rising_klines(),
        account=agent._broker.get_account_state(),
        risk_params=_risk_params(),
        current_stop_loss=None,
        current_take_profit=None,
    )
    stop_loss = open_tick.new_stop_loss
    position = agent._broker.get_account_state().open_positions[0]

    # Il prezzo scende sotto lo stop-loss interno.
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: stop_loss - 1.0)
    tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.LONG),
        position=position,
        mark_price=stop_loss - 1.0,
        klines=_rising_klines(),
        account=agent._broker.get_account_state(),
        risk_params=_risk_params(),
        current_stop_loss=stop_loss,
        current_take_profit=open_tick.new_take_profit,
    )

    assert tick.execution_result is not None
    assert agent._broker.get_account_state().open_positions == []


def test_risk_gate_blocks_entry_on_paused_symbol(agent, monkeypatch):
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 100.0)

    tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.LONG),
        position=None,
        mark_price=100.0,
        klines=_rising_klines(),
        account=agent._broker.get_account_state(),
        risk_params=_risk_params(paused_symbols=["BTCUSDT"]),
        current_stop_loss=None,
        current_take_profit=None,
    )

    assert tick.execution_result is None
    assert tick.risk_decision.status.value == "rejected"
    assert agent._broker.get_account_state().open_positions == []
