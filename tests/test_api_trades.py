"""Test dello storico trade aggregato per giorno e del dettaglio di un
singolo giorno."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api.deps import get_db, get_session_factory_dep
from api.main import app
from storage.db import get_engine, get_session_factory, init_db
from storage.models import ExecutionResultRecord, TradingSession, User


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "test_api_trades.db"
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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_trades(factory) -> int:
    """Crea una sessione con 3 fill: 2 oggi (uno BTCUSDT in profitto, uno
    SOLUSDT in perdita) e 1 ieri (BTCUSDT), più un ordine rifiutato (da
    ignorare negli aggregati). Ritorna lo user_id."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    with factory() as db:
        user = db.query(User).first()
        trading_session = TradingSession(user_id=user.id, status="stopped", starting_equity=100_000.0)
        db.add(trading_session)
        db.flush()

        db.add_all(
            [
                ExecutionResultRecord(
                    session_id=trading_session.id,
                    created_at=now,
                    symbol="BTCUSDT",
                    status="filled",
                    payload='{"side": "sell", "filled_quantity": 0.1, "avg_fill_price": 51000.0, "fee": 2.0, "realized_pnl": 100.0}',
                ),
                ExecutionResultRecord(
                    session_id=trading_session.id,
                    created_at=now,
                    symbol="SOLUSDT",
                    status="filled",
                    payload='{"side": "sell", "filled_quantity": 10.0, "avg_fill_price": 95.0, "fee": 1.0, "realized_pnl": -20.0}',
                ),
                ExecutionResultRecord(
                    session_id=trading_session.id,
                    created_at=yesterday,
                    symbol="BTCUSDT",
                    status="filled",
                    payload='{"side": "buy", "filled_quantity": 0.05, "avg_fill_price": 49000.0, "fee": 1.0, "realized_pnl": null}',
                ),
                ExecutionResultRecord(
                    session_id=trading_session.id,
                    created_at=now,
                    symbol="INJUSDT",
                    status="rejected",
                    payload='{"side": "buy", "filled_quantity": 0.0, "avg_fill_price": null, "fee": 0.0, "realized_pnl": null, "error_message": "margine insufficiente"}',
                ),
            ]
        )
        db.commit()
        return user.id


def _register(client: TestClient) -> None:
    response = client.post("/api/auth/register", json={"username": "trader1", "password": "password123"})
    assert response.status_code == 201


def test_trade_days_aggregates_correctly_and_ignores_rejected_orders(client: TestClient):
    _register(client)
    _seed_trades(client.app.dependency_overrides[get_session_factory_dep]())

    response = client.get("/api/trades/days")
    assert response.status_code == 200
    days = {row["date"]: row for row in response.json()}

    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday_key = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    assert days[today_key]["trades_count"] == 2  # non include il rejected
    assert days[today_key]["total_realized_pnl"] == pytest.approx(80.0)  # 100 - 20
    assert days[today_key]["total_fees"] == pytest.approx(3.0)  # 2 + 1

    assert days[yesterday_key]["trades_count"] == 1


def test_trade_day_detail_returns_individual_trades(client: TestClient):
    _register(client)
    _seed_trades(client.app.dependency_overrides[get_session_factory_dep]())

    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    response = client.get(f"/api/trades/days/{today_key}")

    assert response.status_code == 200
    symbols = {trade["symbol"] for trade in response.json()}
    assert symbols == {"BTCUSDT", "SOLUSDT"}
    assert all(trade["status"] == "filled" for trade in response.json())


def test_trade_days_is_scoped_per_user(client: TestClient):
    _register(client)
    _seed_trades(client.app.dependency_overrides[get_session_factory_dep]())

    client.post("/api/auth/logout")
    client.post("/api/auth/register", json={"username": "trader2", "password": "password123"})

    response = client.get("/api/trades/days")
    assert response.json() == []
