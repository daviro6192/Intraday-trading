"""Test dell'autenticazione dell'API web: registrazione, login, sessione,
accesso non autenticato rifiutato."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api.deps import get_db
from api.main import app
from storage.db import get_engine, get_session_factory, init_db


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "test_auth.db"
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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_unauthenticated_request_is_rejected(client: TestClient):
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_register_login_logout_flow(client: TestClient):
    register_response = client.post("/api/auth/register", json={"username": "trader1", "password": "password123"})
    assert register_response.status_code == 201
    assert register_response.json()["username"] == "trader1"

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "trader1"

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401

    login_response = client.post("/api/auth/login", json={"username": "trader1", "password": "password123"})
    assert login_response.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_login_with_wrong_password_is_rejected(client: TestClient):
    client.post("/api/auth/register", json={"username": "trader1", "password": "password123"})
    client.post("/api/auth/logout")

    response = client.post("/api/auth/login", json={"username": "trader1", "password": "wrong-password"})
    assert response.status_code == 401


def test_duplicate_username_is_rejected(client: TestClient):
    client.post("/api/auth/register", json={"username": "trader1", "password": "password123"})
    response = client.post("/api/auth/register", json={"username": "trader1", "password": "another-password"})
    assert response.status_code == 409


def test_new_user_gets_settings_seeded_from_trading_yaml(client: TestClient):
    client.post("/api/auth/register", json={"username": "trader1", "password": "password123"})

    settings_response = client.get("/api/settings")
    assert settings_response.status_code == 200
    assert settings_response.json()["has_api_key"] is False
    assert settings_response.json()["trading_mode"] == "paper"

    symbols_response = client.get("/api/symbols")
    assert symbols_response.json()["fixed"]["symbol"] == "BTC"

    risk_limits_response = client.get("/api/risk-limits")
    assert "max_risk_per_trade_pct" in risk_limits_response.json()
