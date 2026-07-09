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


def _low_volatility_klines(n: int = 25, start: float = 62700.0) -> list[dict]:
    """Serie a prezzo alto e oscillazioni minuscole in proporzione (come
    Bitcoin rispetto a un altcoin): l'ATR risultante è una frazione di
    prezzo molto piccola, il caso che fa scattare il pavimento fee-aware."""
    return [
        {"open": start, "high": start + 0.2, "low": start - 0.2, "close": start + (i % 2) * 0.1, "volume": 10.0}
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


def test_opens_long_position_on_strategy_view(agent, monkeypatch):
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


def test_opens_position_even_when_immediate_momentum_is_contrary(agent, monkeypatch):
    """Il momentum di brevissimo termine (EMA su poche candele) resta spesso
    contrario alla strategia per minuti di fila: richiedere che coincida
    blocca quasi tutti gli ingressi. L'agente entra comunque sulla direzione
    della strategia, usando le klines solo per calcolare stop/take-profit."""
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 100.0)

    tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.LONG),
        position=None,
        mark_price=100.0,
        klines=_falling_klines(),  # momentum immediato ribassista, strategia è LONG
        account=agent._broker.get_account_state(),
        risk_params=_risk_params(),
        current_stop_loss=None,
        current_take_profit=None,
    )

    assert tick.execution_result is not None
    assert tick.execution_result.status.value == "filled"


def test_uses_fallback_stop_take_profit_when_not_enough_klines_for_atr(agent, monkeypatch):
    """Con dati insufficienti per calcolare l'ATR, l'agente entra comunque
    sulla direzione della strategia usando percentuali fisse di fallback."""
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 100.0)

    tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.LONG),
        position=None,
        mark_price=100.0,
        klines=_rising_klines(n=5),  # troppo poche candele per l'ATR
        account=agent._broker.get_account_state(),
        risk_params=_risk_params(),
        current_stop_loss=None,
        current_take_profit=None,
    )

    assert tick.execution_result is not None


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


def test_opens_low_volatility_symbol_despite_tiny_atr_relative_to_price(agent, monkeypatch):
    """Un simbolo a bassa volatilità percentuale (es. Bitcoin) genera un
    take-profit ATR-based troppo vicino al prezzo per superare mai il gate
    fee-aware, a QUALSIASI size: il rapporto profitto/fee dipende solo dalla
    distanza percentuale del target, non dalla quantità. Senza un pavimento
    minimo sul take-profit, questo blocca quel simbolo per sempre."""
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 62700.0)

    tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.LONG),
        position=None,
        mark_price=62700.0,
        klines=_low_volatility_klines(),
        account=agent._broker.get_account_state(),
        risk_params=_risk_params(min_profit_over_fees_multiple=1.5),
        current_stop_loss=None,
        current_take_profit=None,
    )

    assert tick.execution_result is not None
    assert tick.execution_result.status.value == "filled"


def test_position_size_scales_with_strategy_conviction(agent, monkeypatch):
    """La quota di capitale investita è a discrezione della Strategy Agent:
    con stop ATR stretti la size "grezza" richiesta dal rischio supera quasi
    sempre il tetto di esposizione, quindi è la conviction (0.3-1.0) a
    decidere quanto di quel tetto usare — un segnale forte investe di più di
    uno debole, appena sopra la soglia minima per tradare."""
    monkeypatch.setattr("broker.paper_broker.get_mark_price", lambda symbol: 100.0)
    risk_params = _risk_params(max_position_notional_pct=0.20)

    weak_tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.LONG, conviction=0.3),
        position=None,
        mark_price=100.0,
        klines=_rising_klines(),
        account=agent._broker.get_account_state(),
        risk_params=risk_params,
        current_stop_loss=None,
        current_take_profit=None,
    )
    assert weak_tick.execution_result is not None
    agent._broker.close_position("BTCUSDT")

    strong_tick = agent.run_tick(
        "BTCUSDT",
        _view(TradeDirection.LONG, conviction=1.0),
        position=None,
        mark_price=100.0,
        klines=_rising_klines(),
        account=agent._broker.get_account_state(),
        risk_params=risk_params,
        current_stop_loss=None,
        current_take_profit=None,
    )
    assert strong_tick.execution_result is not None

    weak_qty = weak_tick.execution_result.filled_quantity
    strong_qty = strong_tick.execution_result.filled_quantity
    assert strong_qty > weak_qty
    assert strong_qty == pytest.approx(weak_qty * (1.0 / 0.3), rel=0.01)
