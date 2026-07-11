"""Broker reale contro Bybit V5 (Unified Trading Account, categoria "linear"
= perpetui USDT-margined) — Testnet (fondi finti, api-testnet.bybit.com) o
mainnet di produzione (fondi veri, use_production=True, api.bybit.com),
stessa identica API. Ulteriore alternativa a broker/binance_futures_testnet_broker.py
e broker/crypto_com_broker.py — vedi orchestrator/factory.py per la
selezione in base a UserSettings.trading_mode.

Costruito dalla documentazione pubblica nota dell'API V5 di Bybit: solo la
pagina di autenticazione è stata verificata byte-per-byte contro un file
caricato dall'utente (header X-BAPI-*, stringa da firmare
timestamp+api_key+recv_window+queryString/jsonBody, HMAC-SHA256 esadecimale
minuscolo). Endpoint operativi (create-order, position/list,
closed-pnl, ecc.) e i codici di errore/rifiuto NON sono stati verificati
contro la documentazione ufficiale in questa sessione (non raggiungibile
dalla sandbox di sviluppo) — vanno confermati dall'utente sul proprio
account Testnet prima di un uso con denaro reale. _KNOWN_REJECTION_CODES
è un punto di partenza minimo, non una tabella completa.

Architettura, rispetto a Binance/Crypto.com:
- stile query-string firmata come Binance (non JSON-RPC come Crypto.com):
  header X-BAPI-SIGN = HMAC-SHA256(timestamp+api_key+recv_window+query_o_body);
- creazione ordine ASINCRONA come Crypto.com: /v5/order/create risponde solo
  con orderId, va interrogato /v5/order/history per fill/prezzo/quantità;
- margine isolato per simbolo tramite un semplice switch per simbolo
  (/v5/position/switch-isolated + /v5/position/set-leverage), niente
  meccanismo isolation_id da tracciare come Crypto.com;
- P&L realizzato per-trade ESPOSTO direttamente da Bybit
  (/v5/position/closed-pnl, campo closedPnl per orderId) — a differenza di
  Crypto.com non va calcolato da noi; si ripiega sul calcolo da
  entrata/uscita reali solo se quella chiamata fallisce o non trova il
  record (caso limite, non il percorso normale).
- modalità "one-way" (positionIdx=0) assunta per l'account: se l'utente ha
  l'hedge mode abilitato su Bybit, gli ordini falliranno con un errore
  dedicato da Bybit (da mappare in _KNOWN_REJECTION_CODES una volta osservato)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import time

import requests

from common.schemas import AccountState, ExecutionResult, ExecutionStatus, FeeSchedule, OrderSide, Position

logger = logging.getLogger(__name__)

_TESTNET_BASE_URL = "https://api-testnet.bybit.com"
_PRODUCTION_BASE_URL = "https://api.bybit.com"
_CATEGORY = "linear"
_DEFAULT_TIMEOUT_SECONDS = 10
_USER_AGENT = "intraday-trading-bot/0.1"
_RECV_WINDOW_MS = "5000"
_ACCOUNT_STATE_CACHE_TTL_SECONDS = 3.0
_ORDER_POLL_INTERVAL_SECONDS = 0.3
_ORDER_POLL_MAX_ATTEMPTS = 10
_SERVER_TIME_OFFSET_REFRESH_SECONDS = 300.0
_TERMINAL_ORDER_STATUSES = {"Filled", "Rejected", "Cancelled", "PartiallyFilledCanceled"}

# Codici retCode "già impostato così" da trattare come successo (idempotenza
# su switch-isolated/set-leverage), non verificati dai doc caricati in
# questa sessione — valori pubblicamente noti, da confermare sul testnet.
_ALREADY_SET_CODES = {110026, 110043}

# Punto di partenza minimo per i rifiuti "normali" di trading (saldo/margine
# insufficiente): NON è una tabella completa, i doc caricati in questa
# sessione non includevano gli endpoint/errori operativi. Va esteso non
# appena si osservano altri codici sul testnet dell'utente.
_KNOWN_REJECTION_CODES = {110007, 110012, 110017}


def _sign(sign_string: str, api_secret: str) -> str:
    return hmac.new(api_secret.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha256).hexdigest()


class BybitBroker:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        fee_schedule: FeeSchedule,
        default_leverage: float,
        use_production: bool = False,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._fee_schedule = fee_schedule
        self._default_leverage = default_leverage
        self._base_url = _PRODUCTION_BASE_URL if use_production else _TESTNET_BASE_URL
        self._session = requests.Session()

        self._instrument_info: dict[str, dict] | None = None
        self._isolated_configured: set[str] = set()
        self._leverage_by_symbol: dict[str, float] = {}
        self._account_state_cache: tuple[float, AccountState] | None = None
        self._server_time_offset_ms = 0.0
        self._server_time_offset_checked_at: float | None = None

    def connect(self) -> None:
        # Nessuna chiamata di rete qui, stesso motivo degli altri broker
        # reali: le credenziali vengono validate per davvero al primo
        # get_account_state() (chiamato subito dopo, in
        # session_manager.start_session).
        logger.info("BybitBroker: pronto (%s)", self._base_url)

    def disconnect(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Richieste HTTP: firmate (private) e pubbliche (market/instruments-info)
    # ------------------------------------------------------------------

    def _refresh_server_time_offset(self) -> None:
        """Bybit rifiuta una richiesta il cui timestamp è anche solo ~1s
        avanti rispetto al proprio orologio server (a differenza della
        tolleranza simmetrica di recv_window all'indietro) — un server con
        l'orologio leggermente disallineato (comune su VM/hosting) basta a
        far fallire ogni richiesta con "invalid request...check your server
        timestamp". Ci si allinea periodicamente all'orario del server
        Bybit stesso (endpoint pubblico) invece di fidarsi solo
        dell'orologio locale. Un fallimento qui degrada mantenendo l'ultimo
        offset noto (0.0 se non ancora calcolato), non solleva mai."""
        if (
            self._server_time_offset_checked_at is not None
            and time.monotonic() - self._server_time_offset_checked_at < _SERVER_TIME_OFFSET_REFRESH_SECONDS
        ):
            return
        response = self._public_get("/v5/market/time", {})
        if response is not None and "time" in response:
            self._server_time_offset_ms = float(response["time"]) - int(time.time() * 1000)
        else:
            logger.warning("BybitBroker: impossibile sincronizzare l'orario col server Bybit, uso l'ultimo offset noto")
        self._server_time_offset_checked_at = time.monotonic()

    def _timestamp_ms(self) -> str:
        self._refresh_server_time_offset()
        return str(int(time.time() * 1000 + self._server_time_offset_ms))

    def _signed_request(self, method: str, path: str, params: dict) -> dict | None:
        """None = fallimento di rete (mai un'eccezione qui). Altrimenti
        sempre il dict di risposta, incluso il caso di errore
        (retCode != 0 — il successo è retCode == 0)."""
        timestamp = self._timestamp_ms()
        if method == "GET":
            query_string = "&".join(f"{k}={v}" for k, v in params.items())
            body_to_sign = query_string
            url = f"{self._base_url}{path}"
            request_kwargs = {"params": params}
        else:
            json_body_str = json.dumps(params, separators=(",", ":"))
            body_to_sign = json_body_str
            url = f"{self._base_url}{path}"
            request_kwargs = {"data": json_body_str.encode("utf-8")}

        sign_string = f"{timestamp}{self._api_key}{_RECV_WINDOW_MS}{body_to_sign}"
        signature = _sign(sign_string, self._api_secret)
        headers = {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": _RECV_WINDOW_MS,
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }
        try:
            response = self._session.request(
                method, url, headers=headers, timeout=_DEFAULT_TIMEOUT_SECONDS, **request_kwargs
            )
            return response.json()
        except (requests.RequestException, ValueError):
            logger.warning("Bybit non raggiungibile: %s %s", method, path)
            return None

    def _public_get(self, path: str, params: dict) -> dict | None:
        try:
            response = requests.get(
                f"{self._base_url}{path}",
                params=params,
                headers={"User-Agent": _USER_AGENT},
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            logger.warning("Bybit non raggiungibile: GET %s", path)
            return None

    @staticmethod
    def _ret_code(response: dict | None) -> int | None:
        if response is None:
            return None
        code = response.get("retCode")
        return int(code) if isinstance(code, int | float) and code != 0 else None

    # ------------------------------------------------------------------
    # Stato del conto
    # ------------------------------------------------------------------

    def get_account_state(self) -> AccountState:
        if self._account_state_cache is not None:
            cached_at, cached_state = self._account_state_cache
            if time.monotonic() - cached_at < _ACCOUNT_STATE_CACHE_TTL_SECONDS:
                return cached_state

        wallet_response = self._signed_request(
            "GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"}
        )
        if wallet_response is None or self._ret_code(wallet_response) is not None:
            if self._account_state_cache is not None:
                # Un blip transitorio non deve terminare il loop veloce
                # della sessione (session_manager._fast_loop cattura
                # QUALSIASI eccezione e chiude per sempre): degradiamo
                # restituendo l'ultimo stato noto invece di sollevare.
                logger.warning("BybitBroker: get_account_state fallita, uso l'ultimo stato noto")
                return self._account_state_cache[1]
            msg = wallet_response.get("retMsg") if wallet_response else "Bybit non raggiungibile"
            raise RuntimeError(f"Impossibile leggere lo stato del conto Bybit: {msg}")

        account_info = wallet_response["result"]["list"][0]

        positions_response = self._signed_request(
            "GET", "/v5/position/list", {"category": _CATEGORY, "settleCoin": "USDT"}
        )
        positions: list[Position] = []
        if positions_response is not None and self._ret_code(positions_response) is None:
            for entry in positions_response["result"]["list"]:
                size = float(entry.get("size", 0.0))
                if size == 0:
                    continue
                quantity = size if entry.get("side") == "Buy" else -size
                liq_price_raw = entry.get("liqPrice")
                liquidation_price = float(liq_price_raw) if liq_price_raw not in (None, "") else None
                positions.append(
                    Position(
                        symbol=entry["symbol"],
                        quantity=quantity,
                        avg_price=float(entry.get("avgPrice", 0.0)),
                        leverage=float(entry.get("leverage", self._default_leverage)),
                        initial_margin=float(entry.get("positionIM", 0.0) or 0.0),
                        liquidation_price=liquidation_price,
                        market_value=float(entry.get("positionValue", quantity * float(entry.get("avgPrice", 0.0)))),
                        unrealized_pnl=float(entry.get("unrealisedPnl", 0.0)),
                    )
                )
        else:
            logger.warning("BybitBroker: impossibile leggere le posizioni, nessuna posizione aperta riportata")

        realized_pnl_today = self._realized_pnl_since_midnight()
        fees_paid_today, funding_paid_today = self._fees_and_funding_since_midnight()

        state = AccountState(
            equity=float(account_info.get("totalEquity", 0.0)),
            cash=float(account_info.get("totalAvailableBalance", 0.0)),
            open_positions=positions,
            realized_pnl_today=realized_pnl_today,
            unrealized_pnl_today=sum(p.unrealized_pnl for p in positions),
            fees_paid_today=fees_paid_today,
            funding_paid_today=funding_paid_today,
        )
        self._account_state_cache = (time.monotonic(), state)
        return state

    def _realized_pnl_since_midnight(self) -> float:
        midnight_utc_ms = int(time.time() // 86400 * 86400 * 1000)
        response = self._signed_request(
            "GET", "/v5/position/closed-pnl", {"category": _CATEGORY, "startTime": midnight_utc_ms, "limit": 200}
        )
        if response is None or self._ret_code(response) is not None:
            logger.warning("BybitBroker: impossibile leggere il P&L realizzato di oggi, uso 0.0")
            return 0.0
        return sum(float(entry.get("closedPnl", 0.0)) for entry in response["result"]["list"])

    def _fees_and_funding_since_midnight(self) -> tuple[float, float]:
        """Fee di trading e funding "di oggi" da /v5/execution/list, separate
        per execType — il campo "Funding" per isolare il funding non è
        verificato contro i doc ufficiali in questa sessione (non
        raggiungibili), va confermato sul testnet dell'utente."""
        midnight_utc_ms = int(time.time() // 86400 * 86400 * 1000)
        response = self._signed_request(
            "GET", "/v5/execution/list", {"category": _CATEGORY, "startTime": midnight_utc_ms, "limit": 200}
        )
        if response is None or self._ret_code(response) is not None:
            logger.warning("BybitBroker: impossibile leggere fee/funding di oggi, uso 0.0")
            return 0.0, 0.0

        fees = funding = 0.0
        for entry in response["result"]["list"]:
            fee = float(entry.get("execFee", 0.0))
            if entry.get("execType") == "Funding":
                funding += fee
            else:
                fees += fee
        return fees, funding

    # ------------------------------------------------------------------
    # Strumenti: precisione quantità, margine isolato + leva
    # ------------------------------------------------------------------

    def _get_instrument_filters(self, symbol: str) -> dict | None:
        if self._instrument_info is None:
            info = self._public_get("/v5/market/instruments-info", {"category": _CATEGORY, "symbol": symbol})
            self._instrument_info = {}
            if info is not None and self._ret_code(info) is None:
                for entry in info["result"]["list"]:
                    lot_size = entry["lotSizeFilter"]
                    qty_step = lot_size["qtyStep"]
                    decimals = len(qty_step.split(".")[1]) if "." in qty_step else 0
                    self._instrument_info[entry["symbol"]] = {
                        "qty_step": float(qty_step),
                        "min_qty": float(lot_size["minOrderQty"]),
                        "quantity_decimals": decimals,
                    }
        return self._instrument_info.get(symbol)

    def _round_quantity(self, symbol: str, quantity: float, round_up: bool = False) -> float | None:
        """round_up=True per le chiusure (reduce_only): stessa motivazione
        già documentata in BinanceFuturesTestnetBroker._round_quantity —
        evita di lasciare polvere residua dopo una chiusura, e un ordine
        reduce_only che richiede più della posizione reale viene comunque
        limitato da Bybit alla size effettiva."""
        filters = self._get_instrument_filters(symbol)
        if filters is None:
            return None

        step = filters["qty_step"]
        if step > 0:
            steps = math.ceil(quantity / step) if round_up else math.floor(quantity / step)
            rounded = steps * step
        else:
            rounded = quantity
        rounded = round(rounded, filters["quantity_decimals"])
        if rounded <= 0 or rounded < filters["min_qty"]:
            return None
        return rounded

    def _ensure_isolated_and_leverage(self, symbol: str, leverage: float) -> str | None:
        """Ritorna un messaggio di errore (l'ordine va rifiutato senza
        nemmeno arrivare a Bybit) o None se tutto ok."""
        if symbol not in self._isolated_configured:
            response = self._signed_request(
                "POST",
                "/v5/position/switch-isolated",
                {
                    "category": _CATEGORY,
                    "symbol": symbol,
                    "tradeMode": 1,
                    "buyLeverage": str(leverage),
                    "sellLeverage": str(leverage),
                },
            )
            code = self._ret_code(response)
            if code is not None and code not in _ALREADY_SET_CODES:
                return f"Impossibile impostare margine isolato per {symbol}: {response.get('retMsg')}"
            if response is None:
                return f"Bybit non raggiungibile (impostazione margine isolato per {symbol})"
            self._isolated_configured.add(symbol)
            self._leverage_by_symbol[symbol] = leverage

        if self._leverage_by_symbol.get(symbol) != leverage:
            response = self._signed_request(
                "POST",
                "/v5/position/set-leverage",
                {"category": _CATEGORY, "symbol": symbol, "buyLeverage": str(leverage), "sellLeverage": str(leverage)},
            )
            code = self._ret_code(response)
            if code is not None and code not in _ALREADY_SET_CODES:
                return f"Impossibile impostare leva {leverage}x per {symbol}: {response.get('retMsg')}"
            if response is None:
                return f"Bybit non raggiungibile (impostazione leva per {symbol})"
            self._leverage_by_symbol[symbol] = leverage

        return None

    # ------------------------------------------------------------------
    # Esecuzione ordini
    # ------------------------------------------------------------------

    def _reject(self, symbol: str, side: OrderSide, reason: str) -> ExecutionResult:
        logger.warning("BybitBroker: ordine %s %s rifiutato (%s)", side.value, symbol, reason)
        return ExecutionResult(
            broker_order_id=f"BYBIT-REJECT-{int(time.time() * 1000)}",
            symbol=symbol,
            side=side,
            status=ExecutionStatus.REJECTED,
            error_message=reason,
        )

    def _poll_order_until_terminal(self, symbol: str, order_id: str) -> dict | None:
        """None = fallimento di rete/risposta di errore durante il polling.
        Altrimenti l'entry ordine (terminale, o ancora aperta dopo il
        timeout: SUBMITTED/PARTIALLY_FILLED, non un errore)."""
        entry: dict | None = None
        for _ in range(_ORDER_POLL_MAX_ATTEMPTS):
            response = self._signed_request(
                "GET", "/v5/order/history", {"category": _CATEGORY, "symbol": symbol, "orderId": order_id}
            )
            if response is None or self._ret_code(response) is not None:
                return None
            entries = response["result"]["list"]
            if entries:
                entry = entries[0]
                if entry.get("orderStatus") in _TERMINAL_ORDER_STATUSES:
                    return entry
            time.sleep(_ORDER_POLL_INTERVAL_SECONDS)
        if entry is None:
            logger.warning("BybitBroker: ordine %s non trovato in order/history dopo il timeout di polling", order_id)
            entry = {"orderId": order_id, "orderStatus": "New", "cumExecQty": "0", "avgPrice": ""}
        return entry

    @staticmethod
    def _map_status(entry: dict) -> ExecutionStatus:
        bybit_status = entry.get("orderStatus")
        if bybit_status == "Filled":
            return ExecutionStatus.FILLED
        if bybit_status == "Rejected":
            return ExecutionStatus.REJECTED
        if bybit_status in ("Cancelled", "PartiallyFilledCanceled"):
            return ExecutionStatus.CANCELLED if float(entry.get("cumExecQty", 0.0)) == 0 else ExecutionStatus.PARTIALLY_FILLED
        if bybit_status == "PartiallyFilled":
            return ExecutionStatus.PARTIALLY_FILLED
        return ExecutionStatus.SUBMITTED

    def _fee_for_order(self, symbol: str, order_id: str) -> float:
        response = self._signed_request(
            "GET", "/v5/execution/list", {"category": _CATEGORY, "symbol": symbol, "orderId": order_id}
        )
        if response is None or self._ret_code(response) is not None:
            logger.warning("BybitBroker: impossibile leggere le fee reali dell'ordine %s, uso 0.0", order_id)
            return 0.0
        return sum(float(e.get("execFee", 0.0)) for e in response["result"]["list"])

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        leverage: float,
        reference_price: float,
        reduce_only: bool = False,
    ) -> ExecutionResult:
        if not reduce_only:
            setup_error = self._ensure_isolated_and_leverage(symbol, leverage)
            if setup_error is not None:
                return self._reject(symbol, side, setup_error)

        rounded_quantity = self._round_quantity(symbol, quantity, round_up=reduce_only)
        if rounded_quantity is None:
            return self._reject(symbol, side, "quantità troppo piccola dopo arrotondamento allo step size di Bybit")

        entry_avg_price = None
        direction = 1
        if reduce_only:
            account = self.get_account_state()
            position = next((p for p in account.open_positions if p.symbol == symbol), None)
            if position is not None:
                entry_avg_price = position.avg_price
                direction = 1 if position.quantity > 0 else -1

        response = self._signed_request(
            "POST",
            "/v5/order/create",
            {
                "category": _CATEGORY,
                "symbol": symbol,
                "side": "Buy" if side is OrderSide.BUY else "Sell",
                "orderType": "Market",
                "qty": str(rounded_quantity),
                "reduceOnly": reduce_only,
                "positionIdx": 0,
            },
        )

        if response is None:
            return ExecutionResult(
                broker_order_id=f"BYBIT-ERROR-{int(time.time() * 1000)}",
                symbol=symbol,
                side=side,
                status=ExecutionStatus.ERROR,
                error_message="Bybit non raggiungibile",
            )

        code = self._ret_code(response)
        if code is not None:
            status = ExecutionStatus.REJECTED if code in _KNOWN_REJECTION_CODES else ExecutionStatus.ERROR
            return ExecutionResult(
                broker_order_id=f"BYBIT-{status.value.upper()}-{int(time.time() * 1000)}",
                symbol=symbol,
                side=side,
                status=status,
                error_message=response.get("retMsg", f"Errore Bybit {code}"),
            )

        order_id = response["result"]["orderId"]
        entry = self._poll_order_until_terminal(symbol, order_id)
        if entry is None:
            return ExecutionResult(
                broker_order_id=str(order_id),
                symbol=symbol,
                side=side,
                status=ExecutionStatus.ERROR,
                error_message="Bybit non raggiungibile durante la verifica dell'ordine",
            )

        fee = self._fee_for_order(symbol, order_id)
        realized_pnl = None
        if reduce_only:
            realized_pnl = self._realized_pnl_for_order(symbol, order_id, entry, entry_avg_price, direction)

        avg_price_raw = entry.get("avgPrice")
        return ExecutionResult(
            broker_order_id=str(order_id),
            symbol=symbol,
            side=side,
            status=self._map_status(entry),
            filled_quantity=float(entry.get("cumExecQty", 0.0)),
            avg_fill_price=float(avg_price_raw) if avg_price_raw not in (None, "") else None,
            fee=fee,
            realized_pnl=realized_pnl,
        )

    def _realized_pnl_for_order(
        self, symbol: str, order_id: str, entry: dict, entry_avg_price: float | None, direction: int
    ) -> float | None:
        """P&L realizzato REALE da Bybit (closed-pnl espone il valore per
        singolo orderId, a differenza di Crypto.com) — si ripiega sul
        calcolo da prezzo di entrata/uscita reali solo se questa chiamata
        fallisce o non trova ancora il record (caso limite, non il
        percorso normale)."""
        response = self._signed_request(
            "GET", "/v5/position/closed-pnl", {"category": _CATEGORY, "symbol": symbol, "limit": 50}
        )
        if response is not None and self._ret_code(response) is None:
            match = next((e for e in response["result"]["list"] if e.get("orderId") == order_id), None)
            if match is not None:
                return float(match["closedPnl"])

        logger.warning(
            "BybitBroker: P&L realizzato non trovato in closed-pnl per l'ordine %s, calcolato da prezzo entrata/uscita",
            order_id,
        )
        if entry_avg_price is None:
            return None
        exit_avg_price_raw = entry.get("avgPrice")
        exit_avg_price = float(exit_avg_price_raw) if exit_avg_price_raw not in (None, "") else entry_avg_price
        closed_quantity = float(entry.get("cumExecQty", 0.0))
        return closed_quantity * (exit_avg_price - entry_avg_price) * direction

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
