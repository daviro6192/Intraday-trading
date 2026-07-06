"""Test end-to-end della pipeline con Claude e dati di mercato/news finti e
un PaperBroker reale (nessuna chiamata di rete, nessun ordine reale)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agents.execution_agent import ExecutionAgent
from agents.order_agent import OrderAgent
from agents.risk_agent import RiskAgent, RiskAssessment, RiskAssessmentBatch
from agents.sentiment_agent import SentimentAgent
from agents.strategy_agent import StrategyAgent
from broker.paper_broker import PaperBroker
from common.schemas import (
    Bias,
    DailyStrategy,
    ExecutionStatus,
    Market,
    NewsItem,
    OrderProposalBatch,
    RiskAppetite,
    RiskDecisionStatus,
    SentimentReport,
    WatchlistEntry,
)
from orchestrator.pipeline import Pipeline
from storage.db import get_session_factory, init_db
from storage.models import ExecutionResultRecord, OrderProposalRecord, PipelineRun, RiskDecisionRecord
from tests.conftest import FakeClaudeClient, make_order_proposal

RISK_LIMITS = {
    "max_risk_per_trade_pct": 0.01,
    "max_daily_loss_pct": 0.03,
    "max_concurrent_positions": 5,
    "max_exposure_per_symbol_pct": 0.20,
    "min_reward_risk_ratio": 1.5,
}

TRADING_CONFIG = {
    "news_feeds": [],
    "watchlists": {"us_equities": {"market": "US_EQUITY", "symbols": ["AAPL"]}},
    "risk_limits": RISK_LIMITS,
}


def _in_memory_session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    return get_session_factory(engine)


def _build_pipeline(monkeypatch) -> tuple[Pipeline, FakeClaudeClient]:
    monkeypatch.setattr(
        "orchestrator.pipeline.fetch_all_feeds",
        lambda feeds: [NewsItem(title="Mercati in rialzo su dati macro positivi", source="Reuters")],
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.get_market_snapshots",
        lambda symbols: {symbol: {"last_price": 101.0} for symbol, _market in symbols},
    )

    responses = {
        "SentimentReport": SentimentReport(
            report_date=datetime.now(timezone.utc),
            macro_summary="Contesto favorevole al rischio.",
            overall_sentiment=Bias.BULLISH,
            overall_sentiment_score=0.4,
        ),
        "DailyStrategy": DailyStrategy(
            strategy_date=datetime.now(timezone.utc),
            risk_appetite=RiskAppetite.MEDIUM,
            watchlist=[
                WatchlistEntry(symbol="AAPL", market=Market.US_EQUITY, bias=Bias.BULLISH, rationale="momentum")
            ],
        ),
        "OrderProposalBatch": OrderProposalBatch(
            proposals=[make_order_proposal(symbol="AAPL", entry_price=101, stop_loss=99, take_profit=107)]
        ),
        "RiskAssessmentBatch": RiskAssessmentBatch(
            assessments=[
                RiskAssessment(symbol="AAPL", status=RiskDecisionStatus.APPROVED, suggested_quantity=10, reasoning="ok")
            ]
        ),
    }
    claude_client = FakeClaudeClient(responses)

    broker = PaperBroker(starting_cash=100_000)
    broker.connect()

    pipeline = Pipeline(
        sentiment_agent=SentimentAgent(claude_client),
        strategy_agent=StrategyAgent(claude_client),
        order_agent=OrderAgent(claude_client),
        risk_agent=RiskAgent(claude_client, RISK_LIMITS),
        execution_agent=ExecutionAgent(broker),
        broker=broker,
        session_factory=_in_memory_session_factory(),
        trading_config=TRADING_CONFIG,
    )
    return pipeline, claude_client


def test_full_pipeline_places_a_paper_order_and_persists_audit_trail(monkeypatch):
    pipeline, claude_client = _build_pipeline(monkeypatch)

    results = pipeline.run_once_full()

    assert len(results) == 1
    assert results[0].symbol == "AAPL"
    assert results[0].status is ExecutionStatus.FILLED
    assert {call[0] for call in claude_client.calls} == {
        "SentimentReport",
        "DailyStrategy",
        "OrderProposalBatch",
        "RiskAssessmentBatch",
    }

    with pipeline.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PipelineRun)) == 2  # pre-market + intraday
        assert session.scalar(select(func.count()).select_from(OrderProposalRecord)) == 1
        assert session.scalar(select(func.count()).select_from(RiskDecisionRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ExecutionResultRecord)) == 1


def test_intraday_cycle_without_prior_strategy_is_skipped(monkeypatch):
    pipeline, claude_client = _build_pipeline(monkeypatch)

    results = pipeline.run_intraday_cycle()

    assert results == []
    assert claude_client.calls == []
