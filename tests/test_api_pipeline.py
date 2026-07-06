"""Test dei trigger della pipeline via API: Claude e dati di mercato finti,
DB isolato per test, verifica che l'audit trail sia scoped per utente."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from agents.risk_agent import RiskAssessment, RiskAssessmentBatch
from api.deps import get_db, get_session_factory_dep
from api.main import app
from common.schemas import (
    Bias,
    DailyStrategy,
    Market,
    NewsItem,
    OrderProposalBatch,
    RiskAppetite,
    RiskDecisionStatus,
    SentimentReport,
    WatchlistEntry,
)
from storage.db import get_engine, get_session_factory, init_db
from tests.conftest import FakeClaudeClient, make_order_proposal


def _canned_responses() -> dict:
    return {
        "SentimentReport": SentimentReport(
            report_date=datetime.now(timezone.utc),
            macro_summary="Contesto favorevole al rischio.",
            overall_sentiment=Bias.BULLISH,
            overall_sentiment_score=0.3,
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


class _FakeClaudeClientFactory:
    """Sostituisce orchestrator.factory.ClaudeClient: chiamabile come una
    classe (api_key, model) ma restituisce un FakeClaudeClient."""

    def __call__(self, api_key: str, model: str) -> FakeClaudeClient:
        return FakeClaudeClient(_canned_responses())


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    db_path = tmp_path / "test_pipeline_api.db"
    engine = get_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = get_session_factory(engine)

    def override_get_db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session_factory_dep] = lambda: factory

    monkeypatch.setattr(
        "orchestrator.pipeline.fetch_all_feeds",
        lambda feeds: [NewsItem(title="Mercati in rialzo su dati macro positivi", source="Test")],
    )
    monkeypatch.setattr(
        "orchestrator.pipeline.get_market_snapshots",
        lambda symbols: {symbol: {"last_price": 101.0} for symbol, _market in symbols},
    )
    monkeypatch.setattr("orchestrator.factory.ClaudeClient", _FakeClaudeClientFactory())

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _register(client: TestClient, username: str = "trader1", password: str = "password123") -> None:
    response = client.post("/api/auth/register", json={"username": username, "password": password})
    assert response.status_code == 201


def test_run_pre_market_then_intraday(client: TestClient):
    _register(client)

    pre_market_response = client.post("/api/pipeline/run-pre-market")
    assert pre_market_response.status_code == 200
    assert pre_market_response.json()["watchlist"][0]["symbol"] == "AAPL"

    intraday_response = client.post("/api/pipeline/run-intraday")
    assert intraday_response.status_code == 200
    assert len(intraday_response.json()["execution_results"]) == 1

    runs_response = client.get("/api/pipeline/runs")
    assert len(runs_response.json()) == 2

    run_detail = client.get(f"/api/pipeline/runs/{runs_response.json()[-1]['id']}")
    assert run_detail.status_code == 200
    assert run_detail.json()["sentiment_report"] is not None


def test_usage_accumulates_after_pipeline_runs(client: TestClient):
    _register(client)

    client.post("/api/pipeline/run-pre-market")  # 2 chiamate Claude: sentiment + strategy
    client.post("/api/pipeline/run-intraday")  # 2 chiamate Claude: proposte + risk

    usage_response = client.get("/api/usage")
    assert usage_response.status_code == 200
    body = usage_response.json()
    assert body["total_input_tokens"] == 400
    assert body["total_output_tokens"] == 200
    assert body["estimated_cost_usd"] > 0


def test_run_intraday_without_prior_strategy_is_rejected(client: TestClient):
    _register(client)

    response = client.post("/api/pipeline/run-intraday")
    assert response.status_code == 400


def test_pipeline_runs_are_scoped_per_user(client: TestClient):
    _register(client, username="trader1", password="password123")
    client.post("/api/pipeline/run-pre-market")

    client.post("/api/auth/logout")
    _register(client, username="trader2", password="password123")

    runs_response = client.get("/api/pipeline/runs")
    assert runs_response.json() == []


def test_account_state_uses_paper_broker_by_default(client: TestClient):
    _register(client)

    response = client.get("/api/account/state")
    assert response.status_code == 200
    assert response.json()["equity"] == 100_000.0
