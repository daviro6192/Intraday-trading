"""Test dedicato per GET /api/account/state: non richiede API key Anthropic
né una sessione attiva, solo la costruzione del broker dalle impostazioni
dell'utente. Copre la regressione in cui build_broker_for_user veniva
chiamato senza il FeeSchedule richiesto dalla sua firma."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api.deps import get_db, get_session_factory_dep
from api.main import app
from storage.db import get_engine, get_session_factory, init_db


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "test_api_account.db"
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


def test_account_state_uses_paper_broker_starting_cash_by_default(client: TestClient):
    client.post("/api/auth/register", json={"username": "trader1", "password": "password123"})

    response = client.get("/api/account/state")

    assert response.status_code == 200
    body = response.json()
    assert body["equity"] == body["cash"]
    assert body["open_positions"] == []


def test_account_state_requires_authentication(client: TestClient):
    response = client.get("/api/account/state")
    assert response.status_code == 401
