"""BinanceFuturesTestnetBroker: mai una vera chiamata di rete qui, tutte le
richieste HTTP mockate a livello di _signed_request/_public_request (stesso
approccio con cui gli altri test del progetto evitano rete reale)."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from broker.binance_futures_testnet_broker import BinanceFuturesTestnetBroker, _sign
from common.schemas import ExecutionStatus, FeeSchedule, OrderSide


def _fee_schedule() -> FeeSchedule:
    return FeeSchedule(
        maker_fee_pct=0.0002, taker_fee_pct=0.0004, funding_interval_hours=8, default_funding_rate_fallback_pct=0.0001
    )


@pytest.fixture
def broker() -> BinanceFuturesTestnetBroker:
    return BinanceFuturesTestnetBroker(
        api_key="test-key", api_secret="test-secret", fee_schedule=_fee_schedule(), default_leverage=3.0
    )


_EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "quantityPrecision": 3,
            "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"}],
        }
    ]
}


def test_sign_matches_independently_computed_hmac():
    query = "symbol=BTCUSDT&side=BUY&timestamp=123"
    expected = hmac.new(b"test-secret", query.encode("utf-8"), hashlib.sha256).hexdigest()
    assert _sign(query, "test-secret") == expected


def test_round_quantity_rounds_down_to_step_size(broker, monkeypatch):
    monkeypatch.setattr(broker, "_public_request", lambda path, params: _EXCHANGE_INFO)
    assert broker._round_quantity("BTCUSDT", 1.23456) == pytest.approx(1.234)


def test_round_quantity_rejects_below_min_qty(broker, monkeypatch):
    monkeypatch.setattr(broker, "_public_request", lambda path, params: _EXCHANGE_INFO)
    assert broker._round_quantity("BTCUSDT", 0.0001) is None


def test_round_quantity_unknown_symbol_rejected(broker, monkeypatch):
    monkeypatch.setattr(broker, "_public_request", lambda path, params: _EXCHANGE_INFO)
    assert broker._round_quantity("DOGEUSDT", 100.0) is None


def test_round_quantity_rounds_up_when_closing(broker, monkeypatch):
    """Una chiusura (reduce_only) arrotonda per ECCESSO: la posizione reale
    raramente è un multiplo esatto dello step size (l'apertura arrotonda
    per difetto), quindi chiudere arrotondando anch'essa per difetto
    lascerebbe sempre una piccola quantità "polvere" aperta per sempre —
    Binance limita comunque un reduce_only alla size reale, non la supera."""
    monkeypatch.setattr(broker, "_public_request", lambda path, params: _EXCHANGE_INFO)
    assert broker._round_quantity("BTCUSDT", 1.2341, round_up=True) == pytest.approx(1.235)


def test_place_order_rounds_up_quantity_for_reduce_only_close(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_symbol_configured", lambda symbol, leverage: None)

    captured = {}

    def fake_round_quantity(symbol, quantity, round_up=False):
        captured["round_up"] = round_up
        return quantity

    monkeypatch.setattr(broker, "_round_quantity", fake_round_quantity)
    monkeypatch.setattr(
        broker,
        "_signed_request",
        lambda method, path, params: {"orderId": 1, "status": "FILLED", "executedQty": "0.007", "avgPrice": "60000.0"},
    )

    broker.place_order("BTCUSDT", OrderSide.SELL, 0.0081, 3.0, reference_price=60000.0, reduce_only=True)

    assert captured["round_up"] is True


def test_ensure_symbol_configured_treats_already_set_margin_type_as_success(broker, monkeypatch):
    calls = []

    def fake_signed_request(method, path, params):
        calls.append(path)
        if path == "/fapi/v1/marginType":
            return {"code": -4046, "msg": "No need to change margin type."}
        return {"symbol": "BTCUSDT"}  # leverage call succeeds

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    error = broker._ensure_symbol_configured("BTCUSDT", 3.0)

    assert error is None
    assert "BTCUSDT" in broker._margin_type_set
    assert "/fapi/v1/marginType" in calls
    assert "/fapi/v1/leverage" in calls


def test_ensure_symbol_configured_rejects_on_other_margin_error(broker, monkeypatch):
    monkeypatch.setattr(
        broker, "_signed_request", lambda method, path, params: {"code": -1000, "msg": "boom"}
    )

    error = broker._ensure_symbol_configured("BTCUSDT", 3.0)

    assert error is not None
    assert "BTCUSDT" not in broker._margin_type_set


def test_place_order_successful_fill(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_symbol_configured", lambda symbol, leverage: None)
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)

    def fake_signed_request(method, path, params):
        if path == "/fapi/v1/order":
            return {"orderId": 999, "status": "FILLED", "executedQty": "0.01", "avgPrice": "60000.5"}
        raise AssertionError(f"unexpected call to {path}")

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    result = broker.place_order("BTCUSDT", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.FILLED
    assert result.broker_order_id == "999"
    assert result.filled_quantity == pytest.approx(0.01)
    assert result.avg_fill_price == pytest.approx(60000.5)
    assert result.realized_pnl is None  # apertura: nessun P&L realizzato
    assert result.fee > 0  # stimata dalla FeeSchedule


def test_place_order_known_rejection_code(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_symbol_configured", lambda symbol, leverage: None)
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)
    monkeypatch.setattr(
        broker, "_signed_request", lambda method, path, params: {"code": -2019, "msg": "Margin is insufficient."}
    )

    result = broker.place_order("BTCUSDT", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.REJECTED
    assert "insufficient" in result.error_message.lower()


def test_place_order_unknown_error_code_is_error_not_rejected(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_symbol_configured", lambda symbol, leverage: None)
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)
    monkeypatch.setattr(
        broker, "_signed_request", lambda method, path, params: {"code": -1021, "msg": "Timestamp out of window."}
    )

    result = broker.place_order("BTCUSDT", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.ERROR


def test_place_order_network_failure_is_error(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_symbol_configured", lambda symbol, leverage: None)
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)
    monkeypatch.setattr(broker, "_signed_request", lambda method, path, params: None)

    result = broker.place_order("BTCUSDT", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.ERROR


def test_place_order_setup_failure_rejects_without_calling_order_endpoint(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_symbol_configured", lambda symbol, leverage: "leva non impostabile")

    def fail_if_called(method, path, params):
        raise AssertionError("non deve arrivare a chiamare l'endpoint ordine")

    monkeypatch.setattr(broker, "_signed_request", fail_if_called)

    result = broker.place_order("BTCUSDT", OrderSide.BUY, 0.01, 3.0, reference_price=60000.0)

    assert result.status == ExecutionStatus.REJECTED
    assert result.error_message == "leva non impostabile"


def test_closing_order_fetches_realized_pnl_from_user_trades(broker, monkeypatch):
    """Rispecchia il bug appena corretto su PaperBroker: una chiusura deve
    riportare il P&L realizzato REALE del trade, sommato sui fill multipli
    restituiti da userTrades, non lasciato a None."""
    monkeypatch.setattr(broker, "_ensure_symbol_configured", lambda symbol, leverage: None)
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)

    def fake_signed_request(method, path, params):
        if path == "/fapi/v1/order":
            return {"orderId": 111, "status": "FILLED", "executedQty": "0.02", "avgPrice": "61000.0"}
        if path == "/fapi/v1/userTrades":
            assert params["orderId"] == 111
            return [
                {"commission": "1.2", "realizedPnl": "5.5"},
                {"commission": "0.8", "realizedPnl": "-1.0"},
            ]
        raise AssertionError(f"unexpected call to {path}")

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    result = broker.place_order("BTCUSDT", OrderSide.SELL, 0.02, 3.0, reference_price=61000.0, reduce_only=True)

    assert result.realized_pnl == pytest.approx(4.5)
    assert result.fee == pytest.approx(2.0)


def test_closing_order_falls_back_to_estimate_when_user_trades_unavailable(broker, monkeypatch):
    monkeypatch.setattr(broker, "_ensure_symbol_configured", lambda symbol, leverage: None)
    monkeypatch.setattr(broker, "_round_quantity", lambda symbol, qty, round_up=False: qty)

    def fake_signed_request(method, path, params):
        if path == "/fapi/v1/order":
            return {"orderId": 111, "status": "FILLED", "executedQty": "0.02", "avgPrice": "61000.0"}
        if path == "/fapi/v1/userTrades":
            return None  # rete non raggiungibile per questa chiamata secondaria
        raise AssertionError(f"unexpected call to {path}")

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    result = broker.place_order("BTCUSDT", OrderSide.SELL, 0.02, 3.0, reference_price=61000.0, reduce_only=True)

    # L'ordine è comunque riuscito: non deve fallire solo perché l'arricchimento fallisce.
    assert result.status == ExecutionStatus.FILLED
    assert result.realized_pnl is None
    assert result.fee > 0


_ACCOUNT_PAYLOAD = {
    "totalMarginBalance": "100000.0",
    "totalUnrealizedProfit": "50.0",
    "availableBalance": "90000.0",
    "positions": [
        {
            "symbol": "BTCUSDT",
            "positionAmt": "0.5",
            "entryPrice": "60000.0",
            "leverage": "3",
            "isolatedWallet": "10000.0",
            "liquidationPrice": "55000.0",
            "markPrice": "60100.0",
            "unrealizedProfit": "50.0",
        },
        {"symbol": "ETHUSDT", "positionAmt": "0", "entryPrice": "0", "leverage": "3"},
    ],
}
_INCOME_PAYLOAD = [
    {"incomeType": "REALIZED_PNL", "income": "10.5"},
    {"incomeType": "COMMISSION", "income": "-2.3"},
    {"incomeType": "FUNDING_FEE", "income": "-0.4"},
]


def test_get_account_state_maps_fields(broker, monkeypatch):
    monkeypatch.setattr(
        broker,
        "_signed_request",
        lambda method, path, params: _ACCOUNT_PAYLOAD if path == "/fapi/v2/account" else _INCOME_PAYLOAD,
    )

    state = broker.get_account_state()

    assert state.equity == pytest.approx(100000.0)
    assert state.cash == pytest.approx(90000.0)
    assert state.unrealized_pnl_today == pytest.approx(50.0)
    assert state.realized_pnl_today == pytest.approx(10.5)
    assert state.fees_paid_today == pytest.approx(2.3)
    assert state.funding_paid_today == pytest.approx(0.4)
    assert len(state.open_positions) == 1  # ETHUSDT a quantità 0 escluso
    position = state.open_positions[0]
    assert position.symbol == "BTCUSDT"
    assert position.quantity == pytest.approx(0.5)
    assert position.unrealized_pnl == pytest.approx(50.0)


def test_get_account_state_uses_cache_within_ttl(broker, monkeypatch):
    call_count = 0

    def fake_signed_request(method, path, params):
        nonlocal call_count
        if path == "/fapi/v2/account":
            call_count += 1
        return _ACCOUNT_PAYLOAD if path == "/fapi/v2/account" else _INCOME_PAYLOAD

    monkeypatch.setattr(broker, "_signed_request", fake_signed_request)

    broker.get_account_state()
    broker.get_account_state()

    assert call_count == 1  # la seconda chiamata è servita dalla cache


def test_get_account_state_raises_on_first_failure(broker, monkeypatch):
    monkeypatch.setattr(broker, "_signed_request", lambda method, path, params: None)

    with pytest.raises(RuntimeError):
        broker.get_account_state()


def test_get_account_state_returns_stale_state_after_prior_success(broker, monkeypatch):
    responses = iter(
        [
            _ACCOUNT_PAYLOAD,
            _INCOME_PAYLOAD,
        ]
    )

    def first_succeeds(method, path, params):
        return next(responses, None)

    monkeypatch.setattr(broker, "_signed_request", first_succeeds)
    first_state = broker.get_account_state()

    # Forza la scadenza della cache e simula un fallimento di rete transitorio.
    broker._account_state_cache = (0.0, first_state)
    monkeypatch.setattr(broker, "_signed_request", lambda method, path, params: None)

    stale_state = broker.get_account_state()

    assert stale_state == first_state  # non solleva: il fast loop non muore per un blip


def test_close_position_no_open_position_returns_none(broker, monkeypatch):
    monkeypatch.setattr(broker, "get_account_state", lambda: type("S", (), {"open_positions": []})())
    assert broker.close_position("BTCUSDT") is None


def test_close_position_delegates_to_place_order(broker, monkeypatch):
    from common.schemas import AccountState, Position

    position = Position(symbol="BTCUSDT", quantity=0.5, avg_price=60000.0, leverage=3.0)
    state = AccountState(equity=100000.0, cash=90000.0, open_positions=[position])
    monkeypatch.setattr(broker, "get_account_state", lambda: state)

    captured = {}

    def fake_place_order(symbol, side, quantity, leverage, reference_price, reduce_only=False):
        captured.update(
            symbol=symbol, side=side, quantity=quantity, leverage=leverage, reduce_only=reduce_only
        )
        return "result"

    monkeypatch.setattr(broker, "place_order", fake_place_order)

    result = broker.close_position("BTCUSDT")

    assert result == "result"
    assert captured == {
        "symbol": "BTCUSDT",
        "side": OrderSide.SELL,  # posizione long -> chiude vendendo
        "quantity": 0.5,
        "leverage": 3.0,
        "reduce_only": True,
    }
