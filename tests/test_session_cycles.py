"""Verifica i due cicli (lento: fondamentale+strategia; veloce: ordini+rischio)
come funzioni pure, senza sleep reali e senza rete: Claude, CoinGecko e
Binance sono tutti mockati. Le stesse funzioni sono quelle riusate dal loop
asyncio reale in orchestrator/session_manager.py."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.fundamental_agent import FundamentalAgent
from agents.order_agent import OrderAgent
from agents.risk_agent import RiskReviewAgent
from agents.strategy_agent import StrategyAgent
from broker.paper_broker import PaperBroker
from common.schemas import (
    Bias,
    FeeSchedule,
    FundamentalAnalysis,
    FundamentalAnalysisBatch,
    RiskParameters,
    StrategyView,
    StrategyViewBatch,
    TradeDirection,
)
from orchestrator.cycles import SessionState, run_fundamental_strategy_cycle, run_order_risk_tick
from orchestrator.factory import LiveComponents
from storage.db import get_engine, get_session_factory, init_db
from storage.models import ExecutionResultRecord, FundamentalAnalysisRecord, OrderIntentRecord, TradingSession
from tests.conftest import FakeClaudeClient

_STATIC_RISK_LIMITS = {
    "max_leverage": 10.0,
    "max_exposure_per_symbol_pct": 1.0,
    "max_daily_loss_pct": 1.0,
    "min_profit_over_fees_multiple": 0.0,
}


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


def _fundamental_batch(_user_message: str) -> FundamentalAnalysisBatch:
    return FundamentalAnalysisBatch(
        analyses=[
            FundamentalAnalysis(
                symbol="BTCUSDT",
                as_of=datetime.now(timezone.utc),
                structural_bias=Bias.BULLISH,
                score=0.5,
                rationale="test",
            )
        ]
    )


def _strategy_batch(_user_message: str) -> StrategyViewBatch:
    return StrategyViewBatch(
        views=[
            StrategyView(
                symbol="BTCUSDT",
                direction=TradeDirection.LONG,
                conviction=0.8,
                invalidation_condition="test",
                rationale="test",
            )
        ]
    )


def _risk_review(_user_message: str) -> RiskParameters:
    return _risk_params()


@pytest.fixture
def components(tmp_path, monkeypatch) -> LiveComponents:
    monkeypatch.setattr("broker.paper_broker.random.uniform", lambda a, b: 0.0)

    engine = get_engine(f"sqlite:///{tmp_path}/test_session_cycles.db")
    init_db(engine)
    session_factory = get_session_factory(engine)

    client = FakeClaudeClient(
        {
            "FundamentalAnalysisBatch": _fundamental_batch,
            "StrategyViewBatch": _strategy_batch,
            "RiskParameters": _risk_review,
        }
    )
    fee_schedule = _fee_schedule()
    broker = PaperBroker(fee_schedule=fee_schedule, starting_cash=1_000_000.0)
    order_agent = OrderAgent(broker=broker, fee_schedule=fee_schedule, default_leverage=2.0, max_risk_per_trade_pct=0.01)

    return LiveComponents(
        user_id=1,
        fundamental_agent=FundamentalAgent(client),
        strategy_agent=StrategyAgent(client),
        order_agent=order_agent,
        risk_review_agent=RiskReviewAgent(client, _STATIC_RISK_LIMITS),
        broker=broker,
        claude_client=client,
        symbols={"fixed": {"symbol": "BTC", "coingecko_id": "bitcoin", "binance_perp": "BTCUSDT"}},
        static_risk_limits=_STATIC_RISK_LIMITS,
        fee_schedule=fee_schedule,
        cycles_config={"slow_cycle_interval_minutes": 15, "fast_cycle_interval_seconds": 5},
        session_factory=session_factory,
        default_risk_parameters=_risk_params(),
    )


@pytest.fixture
def db_session_id(components: LiveComponents) -> int:
    with components.session_factory() as db:
        trading_session = TradingSession(user_id=1, status="running", starting_equity=1_000_000.0)
        db.add(trading_session)
        db.commit()
        db.refresh(trading_session)
        return trading_session.id


def _rising_klines(n: int = 25, start: float = 90.0, step: float = 0.5) -> list[dict]:
    return [
        {
            "open": start + i * step,
            "high": start + i * step + 0.1,
            "low": start + i * step - 0.1,
            "close": start + i * step,
            "volume": 10.0,
        }
        for i in range(n)
    ]


def test_slow_cycle_updates_state_and_persists_to_db(components, db_session_id, monkeypatch):
    monkeypatch.setattr("orchestrator.cycles.fetch_coin_fundamentals", lambda ids: {"bitcoin": {"market_cap_rank": 1}})

    state = SessionState(db_session_id=db_session_id, risk_parameters=_risk_params())
    run_fundamental_strategy_cycle(components, state)

    assert "BTCUSDT" in state.fundamentals_by_symbol
    assert "BTCUSDT" in state.strategy_views
    assert state.strategy_views["BTCUSDT"].direction is TradeDirection.LONG
    assert state.risk_parameters.rationale == "test"

    with components.session_factory() as db:
        assert db.query(FundamentalAnalysisRecord).count() == 1


def test_fast_tick_opens_position_when_signal_and_view_agree(components, db_session_id, monkeypatch):
    monkeypatch.setattr("orchestrator.cycles.get_mark_price", lambda symbol: 100.0)
    monkeypatch.setattr("orchestrator.cycles.get_recent_klines", lambda symbol, **kwargs: _rising_klines())

    state = SessionState(db_session_id=db_session_id, risk_parameters=_risk_params())
    state.strategy_views["BTCUSDT"] = StrategyView(
        symbol="BTCUSDT",
        direction=TradeDirection.LONG,
        conviction=0.8,
        invalidation_condition="test",
        rationale="test",
    )

    results = run_order_risk_tick(components, state)

    assert len(results) == 1
    assert results[0].status.value == "filled"
    assert components.broker.get_account_state().open_positions[0].symbol == "BTCUSDT"

    with components.session_factory() as db:
        assert db.query(OrderIntentRecord).count() == 1
        assert db.query(ExecutionResultRecord).count() == 1


def test_fast_tick_returns_nothing_without_a_strategy_view(components, db_session_id):
    state = SessionState(db_session_id=db_session_id, risk_parameters=_risk_params())

    results = run_order_risk_tick(components, state)

    assert results == []
    with components.session_factory() as db:
        assert db.query(OrderIntentRecord).count() == 0
