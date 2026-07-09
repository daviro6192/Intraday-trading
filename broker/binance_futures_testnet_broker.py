"""Broker reale contro Binance Futures Testnet (fondi finti, stessa identica
API/comportamento del mainnet: https://fapi.binance.com -> qui invece
https://testnet.binancefuture.com, stessi path relativi). Primo passo prima
di considerare mainnet reale — vedi orchestrator/factory.py per la
selezione in base a UserSettings.trading_mode.

Stile difensivo identico a data_sources/binance_market_data.py (mai
propagare un errore di rete: log + degradazione), con l'eccezione descritta
in get_account_state() per non spegnere il loop veloce della sessione su un
semplice hiccup transitorio (vedi orchestrator/session_manager.py:
_fast_loop cattura QUALSIASI eccezione e termina la sessione per sempre)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import time
from urllib.parse import urlencode

import requests

from common.schemas import AccountState, ExecutionResult, ExecutionStatus, FeeSchedule, OrderSide, Position

logger = logging.getLogger(__name__)

_BASE_URL = "https://testnet.binancefuture.com"
_DEFAULT_TIMEOUT_SECONDS = 10
_USER_AGENT = "intraday-trading-bot/0.1"
_RECV_WINDOW_MS = 5000
_ACCOUNT_STATE_CACHE_TTL_SECONDS = 3.0

# Codici di rifiuto "normali" di trading (margine insufficiente, quantità
# sotto il minimo, precisione/step size, notional minimo, rate limit) ->
# ExecutionStatus.REJECTED. Qualunque altro codice negativo (es. -1021
# timestamp/orologio di sistema, -2015 API key invalida) è un problema di
# configurazione -> ExecutionStatus.ERROR. Punto di partenza, non esaustivo:
# va esteso se un test reale incontra altri codici.
_KNOWN_REJECTION_CODES = {-1111, -2019, -2010, -4003, -1013}
_MARGIN_TYPE_ALREADY_SET = -4046


def _sign(query_string: str, api_secret: str) -> str:
    return hmac.new(api_secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()


class BinanceFuturesTestnetBroker:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        fee_schedule: FeeSchedule,
        default_leverage: float,
        margin_type: str = "ISOLATED",
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._fee_schedule = fee_schedule
        self._default_leverage = default_leverage
        self._margin_type = margin_type
        self._session = requests.Session()

        self._exchange_info: dict[str, dict] | None = None
        self._margin_type_set: set[str] = set()
        self._leverage_by_symbol: dict[str, float] = {}
        self._account_state_cache: tuple[float, AccountState] | None = None
        self._connected = False

    def connect(self) -> None:
        # Nessuna chiamata di rete qui: le credenziali vengono validate per
        # davvero al primo get_account_state() (chiamato subito dopo, in
        # session_manager.start_session) — connect() è anche invocato ad ogni
        # poll idle da api/routers/account.py quando non c'è una sessione
        # attiva, e non ha senso raddoppiare lì una chiamata autenticata.
        self._connected = True
        logger.info("BinanceFuturesTestnetBroker: pronto (%s)", _BASE_URL)

    def disconnect(self) -> None:
        self._connected = False

    # ------------------------------------------------------------------
    # Richieste HTTP: firmate (account/ordini) e pubbliche (exchangeInfo)
    # ------------------------------------------------------------------

    def _signed_request(self, method: str, path: str, params: dict) -> dict | None:
        """None = fallimento di rete (mai un'eccezione qui). Altrimenti
        sempre un dict: o il payload di successo, o il body di errore
        Binance {"code": <negativo>, "msg": ...} — il chiamante distingue
        controllando esplicitamente la presenza di un "code" negativo."""
        payload = {**params, "timestamp": int(time.time() * 1000), "recvWindow": _RECV_WINDOW_MS}
        query_string = urlencode(payload, doseq=True)
        signature = _sign(query_string, self._api_secret)
        url = f"{_BASE_URL}{path}?{query_string}&signature={signature}"
        headers = {"X-MBX-APIKEY": self._api_key, "User-Agent": _USER_AGENT}

        try:
            response = self._session.request(method, url, headers=headers, timeout=_DEFAULT_TIMEOUT_SECONDS)
            return response.json()
        except (requests.RequestException, ValueError):
            logger.warning("Binance Testnet non raggiungibile: %s %s", method, path)
            return None

    def _public_request(self, path: str, params: dict) -> dict | list | None:
        try:
            response = requests.get(
                f"{_BASE_URL}{path}",
                params=params,
                headers={"User-Agent": _USER_AGENT},
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            logger.warning("Binance Testnet non raggiungibile: GET %s", path)
            return None

    @staticmethod
    def _error_code(response: dict | None) -> int | None:
        if response is None:
            return None
        code = response.get("code")
        return int(code) if isinstance(code, int | float) and code < 0 else None

    # ------------------------------------------------------------------
    # Stato del conto
    # ------------------------------------------------------------------

    def get_account_state(self) -> AccountState:
        if self._account_state_cache is not None:
            cached_at, cached_state = self._account_state_cache
            if time.monotonic() - cached_at < _ACCOUNT_STATE_CACHE_TTL_SECONDS:
                return cached_state

        account = self._signed_request("GET", "/fapi/v2/account", {})
        if account is None or self._error_code(account) is not None:
            if self._account_state_cache is not None:
                # Un blip transitorio non deve terminare il loop veloce
                # della sessione (session_manager._fast_loop cattura
                # QUALSIASI eccezione e chiude per sempre): degradiamo
                # restituendo l'ultimo stato noto invece di sollevare.
                logger.warning("BinanceFuturesTestnetBroker: get_account_state fallita, uso l'ultimo stato noto")
                return self._account_state_cache[1]
            # Prima chiamata in assoluto (subito dopo l'avvio sessione,
            # prima dei loop asyncio): qui è giusto sollevare, così una API
            # key sbagliata/revocata viene intercettata subito all'avvio
            # con un errore chiaro (api/routers/session.py la traduce in
            # HTTP 400) invece di restare silenziosa.
            msg = account.get("msg") if account else "Binance Testnet non raggiungibile"
            raise RuntimeError(f"Impossibile leggere lo stato del conto Binance Testnet: {msg}")

        positions = []
        for entry in account.get("positions", []):
            position_amt = float(entry.get("positionAmt", 0.0))
            if position_amt == 0:
                continue
            mark_price = float(entry.get("markPrice", 0.0)) or float(entry.get("entryPrice", 0.0))
            liquidation_price = float(entry.get("liquidationPrice", 0.0))
            positions.append(
                Position(
                    symbol=entry["symbol"],
                    quantity=position_amt,
                    avg_price=float(entry.get("entryPrice", 0.0)),
                    leverage=float(entry.get("leverage", self._default_leverage)),
                    initial_margin=float(entry.get("isolatedWallet", entry.get("initialMargin", 0.0))),
                    liquidation_price=liquidation_price if liquidation_price > 0 else None,
                    market_value=position_amt * mark_price,
                    unrealized_pnl=float(entry.get("unrealizedProfit", 0.0)),
                )
            )

        realized_pnl_today, fees_paid_today, funding_paid_today = self._income_since_midnight()

        state = AccountState(
            equity=float(account.get("totalMarginBalance", 0.0)),
            cash=float(account.get("availableBalance", 0.0)),
            open_positions=positions,
            realized_pnl_today=realized_pnl_today,
            unrealized_pnl_today=float(account.get("totalUnrealizedProfit", 0.0)),
            fees_paid_today=fees_paid_today,
            funding_paid_today=funding_paid_today,
        )
        self._account_state_cache = (time.monotonic(), state)
        return state

    def _income_since_midnight(self) -> tuple[float, float, float]:
        """realized_pnl/fee/funding "di oggi" da /fapi/v1/income (UTC).
        Nessuna paginazione in questa prima versione (limit=1000) — limite
        noto, accettabile per un test, non per volumi alti. Un fallimento
        qui non deve bloccare lo stato del conto (chiamata secondaria):
        degrada a 0.0 con un warning."""
        midnight_utc_ms = int(time.time() // 86400 * 86400 * 1000)
        income = self._signed_request(
            "GET", "/fapi/v1/income", {"startTime": midnight_utc_ms, "limit": 1000}
        )
        if income is None or not isinstance(income, list):
            logger.warning("BinanceFuturesTestnetBroker: impossibile leggere /fapi/v1/income, uso 0.0 per oggi")
            return 0.0, 0.0, 0.0

        realized = fees = funding = 0.0
        for entry in income:
            income_type = entry.get("incomeType")
            amount = float(entry.get("income", 0.0))
            if income_type == "REALIZED_PNL":
                realized += amount
            elif income_type == "COMMISSION":
                fees += -amount  # Binance riporta la commissione come importo negativo
            elif income_type == "FUNDING_FEE":
                funding += -amount
        return realized, fees, funding

    # ------------------------------------------------------------------
    # Impostazione simbolo: margin type isolato + leva, quantity rounding
    # ------------------------------------------------------------------

    def _ensure_symbol_configured(self, symbol: str, leverage: float) -> str | None:
        """Ritorna un messaggio di errore (l'ordine va rifiutato senza
        nemmeno arrivare a Binance) o None se tutto ok."""
        if symbol not in self._margin_type_set:
            response = self._signed_request(
                "POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": self._margin_type}
            )
            code = self._error_code(response)
            if code is not None and code != _MARGIN_TYPE_ALREADY_SET:
                return f"Impossibile impostare margin type {self._margin_type} per {symbol}: {response.get('msg')}"
            if response is None:
                return f"Binance Testnet non raggiungibile (impostazione margin type per {symbol})"
            self._margin_type_set.add(symbol)

        if self._leverage_by_symbol.get(symbol) != leverage:
            response = self._signed_request(
                "POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(round(leverage))}
            )
            if response is None or self._error_code(response) is not None:
                msg = response.get("msg") if response else "Binance Testnet non raggiungibile"
                return f"Impossibile impostare leva {leverage}x per {symbol}: {msg}"
            self._leverage_by_symbol[symbol] = leverage

        return None

    def _get_symbol_filters(self, symbol: str) -> dict | None:
        if self._exchange_info is None:
            info = self._public_request("/fapi/v1/exchangeInfo", {})
            self._exchange_info = {}
            if info is not None:
                for entry in info.get("symbols", []):
                    lot_size = next(
                        (f for f in entry.get("filters", []) if f.get("filterType") == "LOT_SIZE"), None
                    )
                    if lot_size is None:
                        continue
                    self._exchange_info[entry["symbol"]] = {
                        "step_size": float(lot_size["stepSize"]),
                        "min_qty": float(lot_size["minQty"]),
                        "quantity_precision": int(entry.get("quantityPrecision", 8)),
                    }
        return self._exchange_info.get(symbol)

    def _round_quantity(self, symbol: str, quantity: float, round_up: bool = False) -> float | None:
        """round_up=True per le chiusure (reduce_only): una posizione reale
        raramente è un multiplo esatto dello step size (l'apertura stessa
        arrotonda per difetto), quindi chiudere arrotondando anch'essa per
        difetto lascerebbe sempre una piccola quantità "polvere" ancora
        aperta — e ai tentativi successivi quella polvere può finire sotto
        il minimo e restare bloccata per sempre. Arrotondare per eccesso è
        sicuro qui: un ordine reduce_only che richiede più della posizione
        aperta viene comunque limitato da Binance alla size reale, non la
        supera mai."""
        filters = self._get_symbol_filters(symbol)
        if filters is None:
            # Simbolo non trovato in exchangeInfo (o exchangeInfo
            # irraggiungibile): meglio rifiutare che inviare una quantità
            # potenzialmente non valida.
            return None

        step_size = filters["step_size"]
        if step_size > 0:
            steps = math.ceil(quantity / step_size) if round_up else math.floor(quantity / step_size)
            rounded = steps * step_size
        else:
            rounded = quantity
        rounded = round(rounded, filters["quantity_precision"])
        if rounded <= 0 or rounded < filters["min_qty"]:
            return None
        return rounded

    # ------------------------------------------------------------------
    # Esecuzione ordini
    # ------------------------------------------------------------------

    def _reject(self, symbol: str, side: OrderSide, reason: str) -> ExecutionResult:
        logger.warning("BinanceFuturesTestnetBroker: ordine %s %s rifiutato (%s)", side.value, symbol, reason)
        return ExecutionResult(
            broker_order_id=f"BINANCE-REJECT-{int(time.time() * 1000)}",
            symbol=symbol,
            side=side,
            status=ExecutionStatus.REJECTED,
            error_message=reason,
        )

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        leverage: float,
        reference_price: float,
        reduce_only: bool = False,
    ) -> ExecutionResult:
        setup_error = self._ensure_symbol_configured(symbol, leverage)
        if setup_error is not None:
            return self._reject(symbol, side, setup_error)

        rounded_quantity = self._round_quantity(symbol, quantity, round_up=reduce_only)
        if rounded_quantity is None:
            return self._reject(symbol, side, "quantità troppo piccola dopo arrotondamento allo step size di Binance")

        response = self._signed_request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": "BUY" if side is OrderSide.BUY else "SELL",
                "type": "MARKET",
                "quantity": rounded_quantity,
                "reduceOnly": str(reduce_only).lower(),
                "newOrderRespType": "RESULT",
            },
        )

        if response is None:
            return ExecutionResult(
                broker_order_id=f"BINANCE-ERROR-{int(time.time() * 1000)}",
                symbol=symbol,
                side=side,
                status=ExecutionStatus.ERROR,
                error_message="Binance Testnet non raggiungibile",
            )

        code = self._error_code(response)
        if code is not None:
            status = ExecutionStatus.REJECTED if code in _KNOWN_REJECTION_CODES else ExecutionStatus.ERROR
            return ExecutionResult(
                broker_order_id=f"BINANCE-{status.value.upper()}-{int(time.time() * 1000)}",
                symbol=symbol,
                side=side,
                status=status,
                error_message=response.get("msg", f"Errore Binance {code}"),
            )

        order_id = response.get("orderId")
        binance_status = response.get("status", "")
        status_map = {
            "NEW": ExecutionStatus.SUBMITTED,
            "PARTIALLY_FILLED": ExecutionStatus.PARTIALLY_FILLED,
            "FILLED": ExecutionStatus.FILLED,
            "EXPIRED": ExecutionStatus.CANCELLED,
            "REJECTED": ExecutionStatus.REJECTED,
        }
        exec_status = status_map.get(binance_status)
        if exec_status is None:
            logger.warning("BinanceFuturesTestnetBroker: status ordine inatteso %r, trattato come ERROR", binance_status)
            exec_status = ExecutionStatus.ERROR

        filled_quantity = float(response.get("executedQty", 0.0))
        avg_fill_price = float(response.get("avgPrice", 0.0)) or reference_price

        fee, realized_pnl = self._fee_and_realized_pnl(symbol, order_id, reduce_only, filled_quantity, avg_fill_price)

        return ExecutionResult(
            broker_order_id=str(order_id),
            symbol=symbol,
            side=side,
            status=exec_status,
            filled_quantity=filled_quantity,
            avg_fill_price=avg_fill_price,
            fee=fee,
            realized_pnl=realized_pnl,
        )

    def _fee_and_realized_pnl(
        self, symbol: str, order_id: int | None, reduce_only: bool, filled_quantity: float, avg_fill_price: float
    ) -> tuple[float, float | None]:
        """Fee e P&L realizzato reali, letti da Binance (/fapi/v1/userTrades,
        sommati sui fill effettivi dell'ordine — un MARKET order può
        produrne più di uno) sia per le aperture che per le chiusure: mai
        stimati dalla FeeSchedule configurata a meno che questa chiamata
        fallisca per un problema di rete, unico caso in cui si ripiega su
        una stima come ultima risorsa (l'ordine è comunque già eseguito sul
        vero exchange, non deve mai fallire solo per questo)."""
        trades = self._signed_request("GET", "/fapi/v1/userTrades", {"symbol": symbol, "orderId": order_id})
        if trades is None or not isinstance(trades, list) or not trades:
            estimated_fee = filled_quantity * avg_fill_price * self._fee_schedule.taker_fee_pct
            logger.warning(
                "BinanceFuturesTestnetBroker: impossibile leggere userTrades per l'ordine %s, fee stimata dalla FeeSchedule configurata",
                order_id,
            )
            return estimated_fee, None

        total_fee = sum(float(t.get("commission", 0.0)) for t in trades)
        # Un'apertura non realizza mai P&L: Binance lo riporterebbe comunque
        # a 0 per quei fill, ma restare a None qui è più esplicito che "0.0
        # calcolato" per una posizione che non si è ancora chiusa.
        total_realized_pnl = sum(float(t.get("realizedPnl", 0.0)) for t in trades) if reduce_only else None
        return total_fee, total_realized_pnl

    def close_position(self, symbol: str) -> ExecutionResult | None:
        account = self.get_account_state()
        position = next((p for p in account.open_positions if p.symbol == symbol), None)
        if position is None or position.quantity == 0:
            return None

        closing_side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
        return self.place_order(
            symbol=symbol,
            side=closing_side,
            quantity=abs(position.quantity),
            leverage=position.leverage,
            reference_price=position.avg_price,
            reduce_only=True,
        )
