"""CryptoComBroker: mai una vera chiamata di rete qui, tutte le richieste
mockate a livello di _signed_call/_public_get (stesso approccio usato per
BinanceFuturesTestnetBroker)."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from broker.crypto_com_broker import CryptoComBroker, _sign
from common.schemas import AccountState, ExecutionStatus, FeeSchedule, OrderSide, Position


def _fee_schedule() -> FeeSchedule:
    return FeeSchedule(
        maker_fee_pct=0.0002, taker_fee_pct=0.0004, funding_interval_hours=8, default_funding_rate_fallback_pct=0.0001
    )


@pytest.fixture
def broker() -> CryptoComBroker:
    return CryptoComBroker(api_key="test-key", api_secret="test-secret", fee_schedule=_fee_schedule(), default_leverage=3.0)


_INSTRUMENTS = {
    "result": {
        "data": [
            {"symbol": "BTCUSD-PERP", "qty_tick_size": "0.0001", "quantity_decimals": 4},
        ]
    }
}


def test_sign_matches_reference_algorithm_from_the_official_docs():
    """Ricostruisce esattamente l'esempio annidato del doc ufficiale
    (create-order-list con array di oggetti) e verifica che il nostro
    calcolo dia lo stesso digest HMAC-SHA256."""
    params = {
        "contingency_type": "LIST",
        "order_list": [
            {"instrument_name": "ONE_USDT", "side": "BUY", "type": "LIMIT", "price": "0.24", "quantity": "1.0"},
            {
                "instrument_name": "ONE_USDT",
                "side": "BUY",
                "type": "STOP_LIMIT",
                "price": "0.27",
                "quantity": "1.0",
                "ref_price": "0.26",
            },
        ],
    }

    def reference_params_to_str(obj, level, max_level=3):
        if level >= max_level:
            return str(obj)
        result = ""
        for key in sorted(obj):
            result += key
            if obj[key] is None:
                result += "null"
            elif isinstance(obj[key], list):
                for sub in obj[key]:
                    result += reference_params_to_str(sub, level + 1)
            else:
                result += str(obj[key])
        return result

    payload_str = "private/create-order-list" + str(14) + "API_KEY" + reference_params_to_str(params, 0) + str(1700000000000)
    expected = hmac.new(b"SECRET_KEY", payload_str.encode("utf-8"), hashlib.sha256).hexdigest()

    assert _sign("private/create-order-list", 14, "API_KEY", params, 1700000000000, "SECRET_KEY") == expected


def test_round_quantity_rounds_down_to_tick_size(broker, monkeypatch):
    monkeypatch.setattr(broker, "_public_get", lambda method, params: _INSTRUMENTS)
    assert broker._round_quantity("BTCUSD-PERP", 0.12345) == pytest.approx(0.1234)


def test_round_quantity_rejects_non_positive(broker, monkeypatch):
    monkeypatch.setattr(broker, "_public_get", lambda method, params: _INSTRUMENTS)
    assert broker._round_quantity("BTCUSD-PERP", 0.00001) is None


def _order_detail_response(order_id="1", status="FILLED", avg_price="60000.0", qty="0.01", fee="0.024", isolation_id=None):
    result = {
        "order_id": order_id,
        "status": status,
        "avg_price": avg_price,
        "cumulative_quantity": qty,
        "cumulative_fee": fee,
    }
    if isolation_id is not None:
        result["isolation_id"] = isolation_id
    return {"id": 1, "method": "private/get-order-detail", "code": 0, "result": result}


def test_open_successful_fill_caches_isolation_id(broker, monkeypatch):
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty: qty)

    def fake_signed_call(method, params):
        if method == "private/create-order":
            assert "isolation_id" not in params  # primo ordine sul simbolo: nessun isolation_id noto
            return {"id": 1, "method": method, "code": 0, "result": {"order_id": "999"}}
        if method == "private/get-order-detail":
            return _order_detail_response(order_id="999", isolation_id="ISO-1")
        raise AssertionError(f"unexpected call to {method}")

    monkeypatch.setattr(broker, "_signed_call", fake_signed_call)

    result = broker.place_order("BTCUSD-PERP", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.FILLED
    assert result.broker_order_id == "999"
    assert result.filled_quantity == pytest.approx(0.01)
    assert result.avg_fill_price == pytest.approx(60000.0)
    assert result.fee == pytest.approx(0.024)  # reale da cumulative_fee, non stimata
    assert result.realized_pnl is None  # apertura: nessun P&L realizzato
    assert broker._isolation_id_by_symbol["BTCUSD-PERP"] == "ISO-1"


def test_open_reuses_cached_isolation_id(broker, monkeypatch):
    broker._isolation_id_by_symbol["BTCUSD-PERP"] = "ISO-1"
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty: qty)

    def fake_signed_call(method, params):
        if method == "private/create-order":
            assert params["isolation_id"] == "ISO-1"
            return {"id": 1, "method": method, "code": 0, "result": {"order_id": "999"}}
        if method == "private/get-order-detail":
            return _order_detail_response(isolation_id="ISO-1")
        raise AssertionError(f"unexpected call to {method}")

    monkeypatch.setattr(broker, "_signed_call", fake_signed_call)

    broker.place_order("BTCUSD-PERP", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)


def test_open_rejects_when_quantity_rounds_to_none(broker, monkeypatch):
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty: None)

    def fail_if_called(method, params):
        raise AssertionError("non deve arrivare a chiamare create-order")

    monkeypatch.setattr(broker, "_signed_call", fail_if_called)

    result = broker.place_order("BTCUSD-PERP", OrderSide.BUY, 0.00001, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.REJECTED


def test_open_known_rejection_code(broker, monkeypatch):
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty: qty)
    monkeypatch.setattr(
        broker, "_signed_call", lambda method, params: {"id": 1, "method": method, "code": 213, "message": "INVALID_QUANTITY"}
    )

    result = broker.place_order("BTCUSD-PERP", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.REJECTED


def test_open_unknown_error_code_is_error_not_rejected(broker, monkeypatch):
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty: qty)
    monkeypatch.setattr(
        broker, "_signed_call", lambda method, params: {"id": 1, "method": method, "code": 40101, "message": "UNAUTHORIZED"}
    )

    result = broker.place_order("BTCUSD-PERP", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.ERROR


def test_open_network_failure_is_error(broker, monkeypatch):
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty: qty)
    monkeypatch.setattr(broker, "_signed_call", lambda method, params: None)

    result = broker.place_order("BTCUSD-PERP", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.ERROR


def test_open_poll_timeout_returns_submitted_not_error(broker, monkeypatch):
    """Se l'ordine non raggiunge uno stato definitivo entro il numero
    massimo di tentativi di polling, va trattato come pending (SUBMITTED),
    non come un errore: è comunque stato accettato dall'exchange."""
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty: qty)
    monkeypatch.setattr("broker.crypto_com_broker.time.sleep", lambda seconds: None)

    def fake_signed_call(method, params):
        if method == "private/create-order":
            return {"id": 1, "method": method, "code": 0, "result": {"order_id": "999"}}
        if method == "private/get-order-detail":
            return _order_detail_response(status="ACTIVE")
        raise AssertionError(f"unexpected call to {method}")

    monkeypatch.setattr(broker, "_signed_call", fake_signed_call)

    result = broker.place_order("BTCUSD-PERP", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.SUBMITTED


def test_close_calculates_realized_pnl_for_a_long_position(broker, monkeypatch):
    """Crypto.com non espone un P&L realizzato per singolo trade (a
    differenza di Binance): va calcolato da prezzo di entrata/uscita reali.
    Long: guadagna se il prezzo di uscita è sopra quello di entrata."""
    broker._isolation_id_by_symbol["BTCUSD-PERP"] = "ISO-1"
    position = Position(symbol="BTCUSD-PERP", quantity=0.01, avg_price=60000.0, leverage=3.0)
    monkeypatch.setattr(
        broker, "get_account_state", lambda: AccountState(equity=100000.0, cash=90000.0, open_positions=[position])
    )

    def fake_signed_call(method, params):
        if method == "private/close-position":
            assert params["isolation_id"] == "ISO-1"
            assert "quantity" not in params  # chiusura totale nativa, mai una quantità
            return {"id": 1, "method": method, "code": 0, "result": {"order_id": "1000"}}
        if method == "private/get-order-detail":
            return _order_detail_response(order_id="1000", avg_price="61000.0", qty="0.01", fee="0.03")
        raise AssertionError(f"unexpected call to {method}")

    monkeypatch.setattr(broker, "_signed_call", fake_signed_call)

    result = broker.place_order(
        "BTCUSD-PERP", OrderSide.SELL, 0.01, 3.0, reference_price=61000.0, reduce_only=True
    )

    assert result.realized_pnl == pytest.approx(0.01 * (61000.0 - 60000.0))
    assert result.fee == pytest.approx(0.03)  # reale da cumulative_fee
    assert "BTCUSD-PERP" not in broker._isolation_id_by_symbol  # chiusura completa: isolation_id rimosso


def test_close_calculates_realized_pnl_for_a_short_position(broker, monkeypatch):
    """Short: guadagna se il prezzo di uscita è sotto quello di entrata."""
    position = Position(symbol="BTCUSD-PERP", quantity=-0.01, avg_price=61000.0, leverage=3.0)
    monkeypatch.setattr(
        broker, "get_account_state", lambda: AccountState(equity=100000.0, cash=90000.0, open_positions=[position])
    )

    def fake_signed_call(method, params):
        if method == "private/close-position":
            return {"id": 1, "method": method, "code": 0, "result": {"order_id": "1000"}}
        if method == "private/get-order-detail":
            return _order_detail_response(order_id="1000", avg_price="60000.0", qty="0.01", fee="0.03")
        raise AssertionError(f"unexpected call to {method}")

    monkeypatch.setattr(broker, "_signed_call", fake_signed_call)

    result = broker.place_order(
        "BTCUSD-PERP", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0, reduce_only=True
    )

    assert result.realized_pnl == pytest.approx(0.01 * (60000.0 - 61000.0) * -1)  # = +10.0, profitto per lo short


_BALANCE_RESPONSE = {
    "id": 1,
    "method": "private/user-balance",
    "code": 0,
    "result": {
        "data": [
            {
                "total_margin_balance": "100000.0",
                "total_available_balance": "90000.0",
                "total_session_realized_pnl": "10.5",
                "total_session_unrealized_pnl": "50.0",
                "isolated_positions": [{"isolation_id": "ISO-1", "leverage": "3"}],
            }
        ]
    },
}
_POSITIONS_RESPONSE = {
    "id": 1,
    "method": "private/get-positions",
    "code": 0,
    "result": {
        "data": [
            {
                "instrument_name": "BTCUSD-PERP",
                "quantity": "0.01",
                "cost": "600.0",
                "open_position_pnl": "50.0",
                "isolation_id": "ISO-1",
            },
            {"instrument_name": "ETHUSD-PERP", "quantity": "0", "cost": "0", "open_position_pnl": "0"},
        ]
    },
}
_TRADES_RESPONSE = {
    "id": 1,
    "method": "private/get-trades",
    "code": 0,
    "result": {"data": [{"fees": "-2.3"}, {"fees": "-1.1"}]},
}


def test_get_account_state_maps_fields(broker, monkeypatch):
    def fake_signed_call(method, params):
        return {
            "private/user-balance": _BALANCE_RESPONSE,
            "private/get-positions": _POSITIONS_RESPONSE,
            "private/get-trades": _TRADES_RESPONSE,
        }[method]

    monkeypatch.setattr(broker, "_signed_call", fake_signed_call)

    state = broker.get_account_state()

    assert state.equity == pytest.approx(100000.0)
    assert state.cash == pytest.approx(90000.0)
    assert state.realized_pnl_today == pytest.approx(10.5)
    assert state.unrealized_pnl_today == pytest.approx(50.0)
    assert state.fees_paid_today == pytest.approx(3.4)
    assert len(state.open_positions) == 1  # ETHUSD-PERP a quantità 0 escluso
    position = state.open_positions[0]
    assert position.symbol == "BTCUSD-PERP"
    assert position.avg_price == pytest.approx(60000.0)  # 600 / 0.01
    assert position.leverage == pytest.approx(3.0)
    assert position.liquidation_price is None


def test_get_account_state_uses_cache_within_ttl(broker, monkeypatch):
    call_count = 0

    def fake_signed_call(method, params):
        nonlocal call_count
        if method == "private/user-balance":
            call_count += 1
        return {
            "private/user-balance": _BALANCE_RESPONSE,
            "private/get-positions": _POSITIONS_RESPONSE,
            "private/get-trades": _TRADES_RESPONSE,
        }[method]

    monkeypatch.setattr(broker, "_signed_call", fake_signed_call)

    broker.get_account_state()
    broker.get_account_state()

    assert call_count == 1


def test_get_account_state_raises_on_first_failure(broker, monkeypatch):
    monkeypatch.setattr(broker, "_signed_call", lambda method, params: None)

    with pytest.raises(RuntimeError):
        broker.get_account_state()


def test_get_account_state_returns_stale_state_after_prior_success(broker, monkeypatch):
    def fake_signed_call(method, params):
        return {
            "private/user-balance": _BALANCE_RESPONSE,
            "private/get-positions": _POSITIONS_RESPONSE,
            "private/get-trades": _TRADES_RESPONSE,
        }[method]

    monkeypatch.setattr(broker, "_signed_call", fake_signed_call)
    first_state = broker.get_account_state()

    broker._account_state_cache = (0.0, first_state)  # forza la scadenza della cache
    monkeypatch.setattr(broker, "_signed_call", lambda method, params: None)

    stale_state = broker.get_account_state()

    assert stale_state == first_state


def test_close_position_no_open_position_returns_none(broker, monkeypatch):
    monkeypatch.setattr(broker, "get_account_state", lambda: AccountState(equity=100000.0, cash=100000.0, open_positions=[]))
    assert broker.close_position("BTCUSD-PERP") is None


def test_close_position_delegates_to_place_order(broker, monkeypatch):
    position = Position(symbol="BTCUSD-PERP", quantity=0.01, avg_price=60000.0, leverage=3.0)
    monkeypatch.setattr(
        broker, "get_account_state", lambda: AccountState(equity=100000.0, cash=90000.0, open_positions=[position])
    )

    captured = {}

    def fake_place_order(symbol, side, quantity, leverage, reference_price, reduce_only=False):
        captured.update(symbol=symbol, side=side, quantity=quantity, leverage=leverage, reduce_only=reduce_only)
        return "result"

    monkeypatch.setattr(broker, "place_order", fake_place_order)

    result = broker.close_position("BTCUSD-PERP")

    assert result == "result"
    assert captured == {
        "symbol": "BTCUSD-PERP",
        "side": OrderSide.SELL,  # posizione long -> chiude vendendo
        "quantity": 0.01,
        "leverage": 3.0,
        "reduce_only": True,
    }
