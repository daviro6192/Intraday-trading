"""Impostazioni utente: le credenziali Binance Testnet non vengono mai
rilette in chiaro dall'API (solo un booleano "presente/assente"),
trading_mode non valido viene rifiutato, e una stringa vuota cancella una
credenziale già salvata."""

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
    db_path = tmp_path / "test_api_settings.db"
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


def _register(client: TestClient) -> None:
    response = client.post("/api/auth/register", json={"username": "trader1", "password": "password123"})
    assert response.status_code == 201


def test_no_binance_credentials_by_default(client: TestClient):
    _register(client)
    response = client.get("/api/settings")
    assert response.json()["has_binance_testnet_credentials"] is False
    assert response.json()["has_crypto_com_testnet_credentials"] is False


def test_saving_crypto_com_credentials_never_echoes_the_raw_secret(client: TestClient):
    _register(client)

    update = client.put(
        "/api/settings",
        json={"crypto_com_api_key": "abc123", "crypto_com_api_secret": "super-secret"},
    )
    assert update.status_code == 200
    body = update.json()
    assert body["has_crypto_com_testnet_credentials"] is True
    assert "abc123" not in str(body)
    assert "super-secret" not in str(body)

    read_back = client.get("/api/settings").json()
    assert read_back["has_crypto_com_testnet_credentials"] is True
    assert "abc123" not in str(read_back)
    assert "super-secret" not in str(read_back)


def test_crypto_com_testnet_is_a_valid_trading_mode(client: TestClient):
    _register(client)
    response = client.put("/api/settings", json={"trading_mode": "crypto_com_testnet"})
    assert response.status_code == 200
    assert response.json()["trading_mode"] == "crypto_com_testnet"


def test_saving_binance_credentials_never_echoes_the_raw_secret(client: TestClient):
    _register(client)

    update = client.put(
        "/api/settings",
        json={"binance_testnet_api_key": "abc123", "binance_testnet_api_secret": "super-secret"},
    )
    assert update.status_code == 200
    body = update.json()
    assert body["has_binance_testnet_credentials"] is True
    assert "abc123" not in str(body)
    assert "super-secret" not in str(body)

    read_back = client.get("/api/settings").json()
    assert read_back["has_binance_testnet_credentials"] is True
    assert "abc123" not in str(read_back)
    assert "super-secret" not in str(read_back)


def test_invalid_trading_mode_is_rejected(client: TestClient):
    _register(client)
    response = client.put("/api/settings", json={"trading_mode": "mainnet_yolo"})
    assert response.status_code == 400


def test_empty_string_clears_saved_credentials(client: TestClient):
    _register(client)
    client.put(
        "/api/settings",
        json={"binance_testnet_api_key": "abc123", "binance_testnet_api_secret": "super-secret"},
    )

    cleared = client.put(
        "/api/settings",
        json={"binance_testnet_api_key": "", "binance_testnet_api_secret": ""},
    )
    assert cleared.json()["has_binance_testnet_credentials"] is False
