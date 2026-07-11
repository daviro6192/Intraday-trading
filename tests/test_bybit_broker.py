"""BybitBroker: mai una vera chiamata di rete qui, tutte le richieste
mockate a livello di _signed_request/_public_get (stesso approccio usato
per BinanceFuturesTestnetBroker/CryptoComBroker).

Nota: il file di documentazione caricato dall'utente per questa
integrazione copriva solo l'autenticazione (non gli endpoint operativi
create-order/position/closed-pnl, né una tabella di codici di errore), e
il suo esempio di firma non include la secret key usata per generare la
firma di riferimento — quindi qui si verifica solo che la stringa da
firmare sia costruita secondo la regola documentata
(timestamp+api_key+recv_window+queryString/jsonBody), non un digest HMAC
di riferimento byte-per-byte come fatto per Crypto.com."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from broker.bybit_broker import BybitBroker, _sign
from common.schemas import AccountState, ExecutionStatus, FeeSchedule, OrderSide, Position


def _fee_schedule() -> FeeSchedule:
    return FeeSchedule(
        maker_fee_pct=0.0002, taker_fee_pct=0.0004, funding_interval_hours=8, default_funding_rate_fallback_pct=0.0001
    )


@pytest.fixture
def broker() -> BybitBroker:
    return BybitBroker(api_key="test-key", api_secret="test-secret", fee_schedule=_fee_schedule(), default_leverage=3.0)


def test_timestamp_ms_applies_server_offset_when_local_clock_is_ahead(broker, monkeypatch):
    """Bybit rifiuta un timestamp anche solo ~1s avanti rispetto al proprio
    orologio server: se l'orologio locale è avanti, il timestamp usato per
    firmare deve essere corretto dell'offset misurato contro il server."""
    monkeypatch.setattr("broker.bybit_broker.time.time", lambda: 1000.0)  # orologio locale: 1000.0s = 1_000_000ms
    monkeypatch.setattr(broker, "_public_get", lambda path, params: {"retCode": 0, "time": 998300})  # server: 1.7s indietro

    timestamp = broker._timestamp_ms()

    assert timestamp == "998300"


def test_timestamp_ms_reuses_cached_offset_within_ttl(broker, monkeypatch):
    call_count = 0

    def fake_public_get(path, params):
        nonlocal call_count
        call_count += 1
        return {"retCode": 0, "time": 1_000_000}

    monkeypatch.setattr("broker.bybit_broker.time.time", lambda: 1000.0)
    monkeypatch.setattr(broker, "_public_get", fake_public_get)

    broker._timestamp_ms()
    broker._timestamp_ms()

    assert call_count == 1


def test_timestamp_ms_degrades_to_local_clock_when_sync_fails(broker, monkeypatch):
    monkeypatch.setattr("broker.bybit_broker.time.time", lambda: 1000.0)
    monkeypatch.setattr(broker, "_public_get", lambda path, params: None)

    assert broker._timestamp_ms() == "1000000"


def test_sign_matches_documented_string_construction():
    """La regola documentata è timestamp+api_key+recv_window+queryString
    (GET) — verifichiamo solo la costruzione della stringa (l'esempio del
    doc non fornisce la secret key usata per il digest di riferimento)."""
    sign_string = "1658384314791XXXXXXXXXX5000category=option&symbol=BTC-29JUL22-25000-C"
    expected = hmac.new(b"SECRET_KEY", sign_string.encode("utf-8"), hashlib.sha256).hexdigest()
    assert _sign(sign_string, "SECRET_KEY") == expected


_INSTRUMENTS = {
    "retCode": 0,
    "result": {"list": [{"symbol": "BTCUSDT", "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"}}]},
}


def test_round_quantity_rounds_down_to_step_size(broker, monkeypatch):
    monkeypatch.setattr(broker, "_public_get", lambda path, params: _INSTRUMENTS)
    assert broker._round_quantity("BTCUSDT", 0.01234) == pytest.approx(0.012)


def test_round_quantity_rounds_up_for_closes(broker, monkeypatch):
    monkeypatch.setattr(broker, "_public_get", lambda path, params: _INSTRUMENTS)
    assert broker._round_quantity("BTCUSDT", 0.0121, round_up=True) == pytest.approx(0.013)


def test_round_quantity_rejects_non_positive(broker, monkeypatch):
    monkeypatch.setattr(broker, "_public_get", lambda path, params: _INSTRUMENTS)
    assert broker._round_quantity("BTCUSDT", 0.0001) is None


def _order_history_response(order_id="999", status="Filled", avg_price="60000.0", qty="0.01"):
    return {
        "retCode": 0,
        "result": {"list": [{"orderId": order_id, "orderStatus": status, "avgPrice": avg_price, "cumExecQty": qty}]},
    }


def _execution_list_response(fee="0.024"):
    return {"retCode": 0, "result": {"list": [{"execFee": fee}]}}


def test_open_successful_fill(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_isolated_and_leverage", lambda symbol, leverage: None)
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)

    def fake_signed_request(method, path, params):
        if path == "/v5/order/create":
            assert params["reduceOnly"] is False
            return {"retCode": 0, "result": {"orderId": "999"}}
        if path == "/v5/order/history":
            return _order_history_response()
        if path == "/v5/execution/list":
            return _execution_list_response()
        raise AssertionError(f"unexpected call to {path}")

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    result = broker.place_order("BTCUSDT", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.FILLED
    assert result.broker_order_id == "999"
    assert result.filled_quantity == pytest.approx(0.01)
    assert result.avg_fill_price == pytest.approx(60000.0)
    assert result.fee == pytest.approx(0.024)  # reale da execFee, non stimata
    assert result.realized_pnl is None  # apertura: nessun P&L realizzato


def test_open_rejects_when_setup_fails(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_isolated_and_leverage", lambda symbol, leverage: "errore margine isolato")

    def fail_if_called(method, path, params):
        raise AssertionError("non deve arrivare a chiamare order/create")

    monkeypatch.setattr(broker, "_signed_request", fail_if_called)

    result = broker.place_order("BTCUSDT", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.REJECTED


def test_open_rejects_when_quantity_rounds_to_none(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_isolated_and_leverage", lambda symbol, leverage: None)
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: None)

    def fail_if_called(method, path, params):
        raise AssertionError("non deve arrivare a chiamare order/create")

    monkeypatch.setattr(broker, "_signed_request", fail_if_called)

    result = broker.place_order("BTCUSDT", OrderSide.BUY, 0.00001, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.REJECTED


def test_open_known_rejection_code(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_isolated_and_leverage", lambda symbol, leverage: None)
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)
    monkeypatch.setattr(
        broker, "_signed_request", lambda method, path, params: {"retCode": 110007, "retMsg": "insufficient balance"}
    )

    result = broker.place_order("BTCUSDT", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.REJECTED


def test_open_unknown_error_code_is_error_not_rejected(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_isolated_and_leverage", lambda symbol, leverage: None)
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)
    monkeypatch.setattr(
        broker, "_signed_request", lambda method, path, params: {"retCode": 10003, "retMsg": "invalid api key"}
    )

    result = broker.place_order("BTCUSDT", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.ERROR


def test_open_network_failure_is_error(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_isolated_and_leverage", lambda symbol, leverage: None)
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)
    monkeypatch.setattr(broker, "_signed_request", lambda method, path, params: None)

    result = broker.place_order("BTCUSDT", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.ERROR


def test_open_poll_timeout_returns_submitted_not_error(broker, monkeypatch):
    """Se l'ordine non raggiunge uno stato definitivo entro il numero
    massimo di tentativi di polling, va trattato come pending (SUBMITTED),
    non come un errore: è comunque stato accettato dall'exchange."""
    monkeypatch.setattr(broker, "_ensure_isolated_and_leverage", lambda symbol, leverage: None)
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)
    monkeypatch.setattr("broker.bybit_broker.time.sleep", lambda seconds: None)

    def fake_signed_request(method, path, params):
        if path == "/v5/order/create":
            return {"retCode": 0, "result": {"orderId": "999"}}
        if path == "/v5/order/history":
            return _order_history_response(status="New", qty="0")
        if path == "/v5/execution/list":
            return _execution_list_response(fee="0")
        raise AssertionError(f"unexpected call to {path}")

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    result = broker.place_order("BTCUSDT", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.SUBMITTED


def test_close_uses_real_closed_pnl_from_bybit(broker, monkeypatch):
    """A differenza di Crypto.com, Bybit espone il P&L realizzato per
    singolo ordine chiuso (closed-pnl): va usato direttamente, non
    ricalcolato, quando disponibile."""
    position = Position(symbol="BTCUSDT", quantity=0.01, avg_price=60000.0, leverage=3.0)
    monkeypatch.setattr(
        broker, "get_account_state", lambda: AccountState(equity=100000.0, cash=90000.0, open_positions=[position])
    )
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)

    def fake_signed_request(method, path, params):
        if path == "/v5/order/create":
            assert params["reduceOnly"] is True
            return {"retCode": 0, "result": {"orderId": "1000"}}
        if path == "/v5/order/history":
            return _order_history_response(order_id="1000", avg_price="61000.0", qty="0.01")
        if path == "/v5/execution/list":
            return _execution_list_response(fee="0.03")
        if path == "/v5/position/closed-pnl":
            return {"retCode": 0, "result": {"list": [{"orderId": "1000", "closedPnl": "10.5"}]}}
        raise AssertionError(f"unexpected call to {path}")

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    result = broker.place_order("BTCUSDT", OrderSide.SELL, 0.01, 3.0, reference_price=61000.0, reduce_only=True)

    assert result.realized_pnl == pytest.approx(10.5)  # letto da closed-pnl, non ricalcolato
    assert result.fee == pytest.approx(0.03)


def test_close_falls_back_to_calculated_pnl_when_closed_pnl_missing(broker, monkeypatch):
    """Se closed-pnl non ha ancora il record (o la chiamata fallisce), si
    ripiega sul calcolo da prezzo di entrata/uscita reali, stesso approccio
    già usato per Crypto.com."""
    position = Position(symbol="BTCUSDT", quantity=0.01, avg_price=60000.0, leverage=3.0)
    monkeypatch.setattr(
        broker, "get_account_state", lambda: AccountState(equity=100000.0, cash=90000.0, open_positions=[position])
    )
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)

    def fake_signed_request(method, path, params):
        if path == "/v5/order/create":
            return {"retCode": 0, "result": {"orderId": "1000"}}
        if path == "/v5/order/history":
            return _order_history_response(order_id="1000", avg_price="61000.0", qty="0.01")
        if path == "/v5/execution/list":
            return _execution_list_response(fee="0.03")
        if path == "/v5/position/closed-pnl":
            return {"retCode": 0, "result": {"list": []}}  # record non ancora presente
        raise AssertionError(f"unexpected call to {path}")

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    result = broker.place_order("BTCUSDT", OrderSide.SELL, 0.01, 3.0, reference_price=61000.0, reduce_only=True)

    assert result.realized_pnl == pytest.approx(0.01 * (61000.0 - 60000.0))


_WALLET_RESPONSE = {
    "retCode": 0,
    "result": {"list": [{"totalEquity": "100000.0", "totalAvailableBalance": "90000.0"}]},
}
_POSITIONS_RESPONSE = {
    "retCode": 0,
    "result": {
        "list": [
            {
                "symbol": "BTCUSDT",
                "side": "Buy",
                "size": "0.01",
                "avgPrice": "60000.0",
                "leverage": "3",
                "liqPrice": "",
                "positionValue": "600.0",
                "unrealisedPnl": "50.0",
                "positionIM": "200.0",
            },
            {"symbol": "ETHUSDT", "side": "None", "size": "0", "avgPrice": "0"},
        ]
    },
}
_CLOSED_PNL_RESPONSE = {"retCode": 0, "result": {"list": [{"closedPnl": "10.5"}]}}
_EXECUTIONS_RESPONSE = {"retCode": 0, "result": {"list": [{"execFee": "2.3", "execType": "Trade"}, {"execFee": "0.4", "execType": "Funding"}]}}


def _fake_account_signed_request(method, path, params):
    return {
        "/v5/account/wallet-balance": _WALLET_RESPONSE,
        "/v5/position/list": _POSITIONS_RESPONSE,
        "/v5/position/closed-pnl": _CLOSED_PNL_RESPONSE,
        "/v5/execution/list": _EXECUTIONS_RESPONSE,
    }[path]


def test_get_account_state_maps_fields(broker, monkeypatch):
    monkeypatch.setattr(broker, "_signed_request", _fake_account_signed_request)

    state = broker.get_account_state()

    assert state.equity == pytest.approx(100000.0)
    assert state.cash == pytest.approx(90000.0)
    assert state.realized_pnl_today == pytest.approx(10.5)
    assert state.fees_paid_today == pytest.approx(2.3)
    assert state.funding_paid_today == pytest.approx(0.4)
    assert len(state.open_positions) == 1  # ETHUSDT a size 0 escluso
    position = state.open_positions[0]
    assert position.symbol == "BTCUSDT"
    assert position.quantity == pytest.approx(0.01)
    assert position.avg_price == pytest.approx(60000.0)
    assert position.liquidation_price is None  # liqPrice vuota


def test_get_account_state_short_position_has_negative_quantity(broker, monkeypatch):
    def fake_signed_request(method, path, params):
        if path == "/v5/position/list":
            return {
                "retCode": 0,
                "result": {"list": [{"symbol": "BTCUSDT", "side": "Sell", "size": "0.01", "avgPrice": "60000.0", "leverage": "3", "liqPrice": "65000.0"}]},
            }
        return {
            "/v5/account/wallet-balance": _WALLET_RESPONSE,
            "/v5/position/closed-pnl": _CLOSED_PNL_RESPONSE,
            "/v5/execution/list": _EXECUTIONS_RESPONSE,
        }[path]

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    state = broker.get_account_state()

    position = state.open_positions[0]
    assert position.quantity == pytest.approx(-0.01)
    assert position.liquidation_price == pytest.approx(65000.0)


def test_get_account_state_uses_cache_within_ttl(broker, monkeypatch):
    call_count = 0

    def fake_signed_request(method, path, params):
        nonlocal call_count
        if path == "/v5/account/wallet-balance":
            call_count += 1
        return _fake_account_signed_request(method, path, params)

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    broker.get_account_state()
    broker.get_account_state()

    assert call_count == 1


def test_get_account_state_raises_on_first_failure(broker, monkeypatch):
    monkeypatch.setattr(broker, "_signed_request", lambda method, path, params: None)

    with pytest.raises(RuntimeError):
        broker.get_account_state()


def test_get_account_state_returns_stale_state_after_prior_success(broker, monkeypatch):
    monkeypatch.setattr(broker, "_signed_request", _fake_account_signed_request)
    first_state = broker.get_account_state()

    broker._account_state_cache = (0.0, first_state)  # forza la scadenza della cache
    monkeypatch.setattr(broker, "_signed_request", lambda method, path, params: None)

    stale_state = broker.get_account_state()

    assert stale_state == first_state


def test_close_position_no_open_position_returns_none(broker, monkeypatch):
    monkeypatch.setattr(broker, "get_account_state", lambda: AccountState(equity=100000.0, cash=100000.0, open_positions=[]))
    assert broker.close_position("BTCUSDT") is None


def test_close_position_delegates_to_place_order(broker, monkeypatch):
    position = Position(symbol="BTCUSDT", quantity=0.01, avg_price=60000.0, leverage=3.0)
    monkeypatch.setattr(
        broker, "get_account_state", lambda: AccountState(equity=100000.0, cash=90000.0, open_positions=[position])
    )

    captured = {}

    def fake_place_order(symbol, side, quantity, leverage, reference_price, reduce_only=False):
        captured.update(symbol=symbol, side=side, quantity=quantity, leverage=leverage, reduce_only=reduce_only)
        return "result"

    monkeypatch.setattr(broker, "place_order", fake_place_order)

    result = broker.close_position("BTCUSDT")

    assert result == "result"
    assert captured == {
        "symbol": "BTCUSDT",
        "side": OrderSide.SELL,  # posizione long -> chiude vendendo
        "quantity": 0.01,
        "leverage": 3.0,
        "reduce_only": True,
    }


def test_ensure_isolated_and_leverage_sets_up_once(broker, monkeypatch):
    """Il margine isolato è a livello di intero conto (account/set-margin-mode
    su una UTA — position/switch-isolated risponde "unified account is
    forbidden"): va chiamato una sola volta per l'intero processo, non per
    simbolo. La leva resta invece per-simbolo."""
    calls = []

    def fake_signed_request(method, path, params):
        calls.append(path)
        return {"retCode": 0, "result": {}}

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    assert broker._ensure_isolated_and_leverage("BTCUSDT", 3.0) is None
    assert broker._ensure_isolated_and_leverage("BTCUSDT", 3.0) is None  # stessa leva: nessuna nuova chiamata

    assert calls == ["/v5/account/set-margin-mode", "/v5/position/set-leverage"]


def test_ensure_isolated_and_leverage_does_not_repeat_account_setup_for_another_symbol(broker, monkeypatch):
    calls = []

    def fake_signed_request(method, path, params):
        calls.append(path)
        return {"retCode": 0, "result": {}}

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    broker._ensure_isolated_and_leverage("BTCUSDT", 3.0)
    broker._ensure_isolated_and_leverage("ETHUSDT", 3.0)

    assert calls == ["/v5/account/set-margin-mode", "/v5/position/set-leverage", "/v5/position/set-leverage"]


def test_ensure_isolated_and_leverage_updates_leverage_on_change(broker, monkeypatch):
    calls = []

    def fake_signed_request(method, path, params):
        calls.append(path)
        return {"retCode": 0, "result": {}}

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    broker._ensure_isolated_and_leverage("BTCUSDT", 3.0)
    broker._ensure_isolated_and_leverage("BTCUSDT", 5.0)

    assert calls == ["/v5/account/set-margin-mode", "/v5/position/set-leverage", "/v5/position/set-leverage"]


def test_ensure_isolated_and_leverage_treats_not_modified_message_as_success(broker, monkeypatch):
    """Codice di errore sconosciuto ma messaggio che indica idempotenza
    ("not modified"): va trattato come già impostato, non come fallimento —
    codici esatti non verificati contro i doc per questo endpoint."""
    monkeypatch.setattr(
        broker,
        "_signed_request",
        lambda method, path, params: {"retCode": 99999, "retMsg": "Margin mode is not modified"},
    )

    assert broker._ensure_isolated_and_leverage("BTCUSDT", 3.0) is None


def test_ensure_isolated_and_leverage_returns_error_on_failure(broker, monkeypatch):
    monkeypatch.setattr(
        broker, "_signed_request", lambda method, path, params: {"retCode": 10001, "retMsg": "boom"}
    )

    error = broker._ensure_isolated_and_leverage("BTCUSDT", 3.0)

    assert error is not None
