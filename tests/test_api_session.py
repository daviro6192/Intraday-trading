"""Smoke test end-to-end (via API, senza frontend) del meccanismo di
sessione continua: avvio, qualche tick reale in background con Claude e
Binance/CoinGecko mockati, lettura degli aggregati, stop pulito."""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api.deps import get_db, get_session_factory_dep
from api.main import app
from common.schemas import (
    Bias,
    FundamentalAnalysis,
    FundamentalAnalysisBatch,
    RiskParameters,
    StrategyView,
    StrategyViewBatch,
    TradeDirection,
)
from config.settings import trading_config
from storage.db import get_engine, get_session_factory, init_db
from storage.models import ExecutionResultRecord
from tests.conftest import FakeClaudeClient


def _canned_responses() -> dict:
    return {
        "FundamentalAnalysisBatch": FundamentalAnalysisBatch(
            analyses=[
                FundamentalAnalysis(
                    symbol="BTCUSDT",
                    as_of=datetime.now(timezone.utc),
                    structural_bias=Bias.BULLISH,
                    score=0.5,
                    rationale="test",
                )
            ]
        ),
        "StrategyViewBatch": StrategyViewBatch(
            views=[
                StrategyView(
                    symbol="BTCUSDT",
                    direction=TradeDirection.LONG,
                    conviction=0.9,
                    invalidation_condition="test",
                    rationale="test",
                )
            ]
        ),
        "RiskParameters": RiskParameters(
            max_leverage=10.0,
            max_position_notional_pct=1.0,
            max_daily_loss_pct=1.0,
            min_profit_over_fees_multiple=0.0,
            paused_symbols=[],
            rationale="test",
        ),
    }


class _FakeClaudeClientFactory:
    def __call__(self, api_key: str, model: str) -> FakeClaudeClient:
        return FakeClaudeClient(_canned_responses())


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


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    db_path = tmp_path / "test_api_session.db"
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

    # Cicli molto rapidi per il test: qualche tick reale in pochi decimi di secondo.
    monkeypatch.setitem(trading_config["cycles"], "fast_cycle_interval_seconds", 0.05)
    monkeypatch.setitem(trading_config["cycles"], "slow_cycle_interval_minutes", 0.002)

    monkeypatch.setattr("orchestrator.factory.ClaudeClient", _FakeClaudeClientFactory())
    monkeypatch.setattr("broker.paper_broker.random.uniform", lambda a, b: 0.0)
    monkeypatch.setattr("orchestrator.cycles.fetch_coin_fundamentals", lambda ids: {"bitcoin": {"market_cap_rank": 1}})
    monkeypatch.setattr("orchestrator.cycles.get_mark_price", lambda symbol: 100.0)
    monkeypatch.setattr("orchestrator.cycles.get_recent_klines", lambda symbol, **kwargs: _rising_klines())
    monkeypatch.setattr("data_sources.binance_market_data.get_funding_rate", lambda symbol: 0.0001)

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _register(client: TestClient, username: str = "trader1", password: str = "password123") -> None:
    response = client.post("/api/auth/register", json={"username": username, "password": password})
    assert response.status_code == 201


def test_session_start_runs_ticks_and_stop_is_clean(client: TestClient):
    _register(client)

    not_started = client.get("/api/session/status")
    assert not_started.json()["status"] == "not_started"

    start_response = client.post("/api/session/start")
    assert start_response.status_code == 201
    assert start_response.json()["status"] == "running"
    session_id = start_response.json()["session_id"]

    # Lascia girare qualche tick reale in background (i loop asyncio vivono
    # sul thread dell'ASGI server di TestClient, indipendente da questo sleep).
    time.sleep(0.6)

    status_response = client.get("/api/session/status")
    body = status_response.json()
    assert body["status"] == "running"
    assert body["trades_executed"] >= 1
    assert body["current_equity"] != body["starting_equity"]  # fee/P&L hanno mosso l'equity

    stop_response = client.post("/api/session/stop")
    assert stop_response.status_code == 200
    assert stop_response.json()["status"] == "stopped"

    final_status = client.get("/api/session/status")
    assert final_status.json()["status"] == "stopped"
    assert final_status.json()["session_id"] == session_id

    with client.app.dependency_overrides[get_session_factory_dep]()() as db:
        db_count = db.query(ExecutionResultRecord).filter_by(session_id=session_id, status="filled").count()
    assert db_count == final_status.json()["trades_executed"]


def test_stop_closes_all_open_positions_immediately(client: TestClient):
    _register(client)
    client.post("/api/session/start")

    time.sleep(0.6)
    account_while_running = client.get("/api/account/state").json()
    assert len(account_while_running["open_positions"]) >= 1  # il tick veloce ha aperto una posizione LONG su BTCUSDT

    client.post("/api/session/stop")

    account_after_stop = client.get("/api/account/state").json()
    assert account_after_stop["open_positions"] == []


def test_starting_a_second_session_while_one_is_running_is_rejected(client: TestClient):
    _register(client)
    client.post("/api/session/start")

    second_start = client.post("/api/session/start")
    assert second_start.status_code == 409

    client.post("/api/session/stop")
