"""Broker reale contro Crypto.com Exchange — UAT Sandbox (fondi finti) o
mainnet di produzione (fondi veri, use_production=True), stessa identica
API. Alternativa a broker/binance_futures_testnet_broker.py per un utente
per cui Binance non è utilizzabile — vedi orchestrator/factory.py per la
selezione in base a UserSettings.trading_mode.

 ATTENZIONE: in modalità produzione (trading_mode="crypto_com_live")
questo broker piazza ordini REALI con denaro REALE, in automatico e senza
conferma per singolo trade — è il comportamento normale della piattaforma
(sessione continua), qui però con capitale vero in gioco. Richiede API
key/secret dell'account di produzione, diverse da quelle sandbox.

Architettura DIVERSA da Binance su punti che cambiano il design:
- stile JSON-RPC su POST (corpo JSON id/method/params/nonce/sig), non
  query-string firmata;
- firma HMAC-SHA256 su method+id+api_key+parametri_ordinati+nonce (tutti i
  valori numerici nei parametri devono essere stringhe);
- creazione ordine ASINCRONA: create-order/close-position rispondono solo
  con un order_id, va interrogato get-order-detail per sapere l'esito;
- margine isolato per simbolo via un meccanismo isolation_id (assegnato dal
  primo ordine su un simbolo, va poi passato ai successivi);
- P&L realizzato per-trade non esposto da Crypto.com (solo a livello di
  posizione/conto): calcolato qui da prezzo di entrata/uscita reali, non
  stimato — la fee invece è reale e diretta (cumulative_fee)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import time

import requests

from common.schemas import AccountState, ExecutionResult, ExecutionStatus, FeeSchedule, OrderSide, Position

logger = logging.getLogger(__name__)

_SANDBOX_BASE_URL = "https://uat-api.3ona.co/exchange/v1"
_PRODUCTION_BASE_URL = "https://api.crypto.com/exchange/v1"
_DEFAULT_TIMEOUT_SECONDS = 10
_USER_AGENT = "intraday-trading-bot/0.1"
_ACCOUNT_STATE_CACHE_TTL_SECONDS = 3.0
_ORDER_POLL_INTERVAL_SECONDS = 0.3
_ORDER_POLL_MAX_ATTEMPTS = 10
_TERMINAL_ORDER_STATUSES = {"FILLED", "REJECTED", "CANCELED", "EXPIRED"}
_SIGN_PARAM_MAX_LEVEL = 3

# Codici di rifiuto "normali" di trading (margine insufficiente, quantità non
# valida, ecc.) -> ExecutionStatus.REJECTED. Qualunque altro codice
# (autenticazione, rate limit, validazione richiesta) è un problema di
# configurazione -> ExecutionStatus.ERROR. Punto di partenza dalla tabella
# errori del doc, non esaustivo: va esteso se un test reale ne incontra altri.
_KNOWN_REJECTION_CODES = {212, 213, 219, 10004, 30003, 30006, 30024, 30025}


def _params_to_str(obj: object, level: int) -> str:
    """Ricostruisce esattamente l'algoritmo del doc ufficiale per la
    costruzione della stringa di parametri da firmare: chiavi ordinate
    alfabeticamente, ricorsivo fino a _SIGN_PARAM_MAX_LEVEL, array iterati
    elemento per elemento."""
    if level >= _SIGN_PARAM_MAX_LEVEL or not isinstance(obj, dict):
        return str(obj)

    result = ""
    for key in sorted(obj):
        result += key
        value = obj[key]
        if value is None:
            result += "null"
        elif isinstance(value, list):
            for sub_obj in value:
                result += _params_to_str(sub_obj, level + 1)
        else:
            result += str(value)
    return result


def _sign(method: str, request_id: int, api_key: str, params: dict, nonce: int, api_secret: str) -> str:
    param_str = _params_to_str(params, 0) if params else ""
    payload_str = f"{method}{request_id}{api_key}{param_str}{nonce}"
    return hmac.new(api_secret.encode("utf-8"), payload_str.encode("utf-8"), hashlib.sha256).hexdigest()


class CryptoComBroker:
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
        self._base_url = _PRODUCTION_BASE_URL if use_production else _SANDBOX_BASE_URL
        self._session = requests.Session()
        self._request_id = 0

        self._instrument_info: dict[str, dict] | None = None
        self._isolation_id_by_symbol: dict[str, str] = {}
        self._account_state_cache: tuple[float, AccountState] | None = None

    def connect(self) -> None:
        # Nessuna chiamata di rete qui, stesso motivo del broker Binance: le
        # credenziali vengono validate per davvero al primo
        # get_account_state() (chiamato subito dopo, in
        # session_manager.start_session).
        logger.info("CryptoComBroker: pronto (%s)", self._base_url)

    def disconnect(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Richieste HTTP: JSON-RPC firmate (private/*) e pubbliche (public/*)
    # ------------------------------------------------------------------

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _signed_call(self, method: str, params: dict) -> dict | None:
        """None = fallimento di rete (mai un'eccezione qui). Altrimenti
        sempre il dict di risposta, incluso il caso di errore (code != 0 —
        a differenza di Binance dove il successo è code < 0, qui il
        successo è code == 0)."""
        request_id = self._next_request_id()
        nonce = int(time.time() * 1000)
        sig = _sign(method, request_id, self._api_key, params, nonce, self._api_secret)
        body = {
            "id": request_id,
            "method": method,
            "api_key": self._api_key,
            "params": params,
            "nonce": nonce,
            "sig": sig,
        }
        try:
            response = self._session.post(
                f"{self._base_url}/{method}",
                json=body,
                headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
            return response.json()
        except (requests.RequestException, ValueError):
            logger.warning("Crypto.com Exchange non raggiungibile: %s", method)
            return None

    def _public_get(self, method: str, params: dict) -> dict | None:
        try:
            response = requests.get(
                f"{self._base_url}/{method}",
                params=params,
                headers={"User-Agent": _USER_AGENT},
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            logger.warning("Crypto.com Exchange non raggiungibile: %s", method)
            return None

    @staticmethod
    def _error_code(response: dict | None) -> int | None:
        if response is None:
            return None
        code = response.get("code")
        return int(code) if isinstance(code, int | float) and code != 0 else None

    # ------------------------------------------------------------------
    # Stato del conto
    # ------------------------------------------------------------------

    def get_account_state(self) -> AccountState:
        if self._account_state_cache is not None:
            cached_at, cached_state = self._account_state_cache
            if time.monotonic() - cached_at < _ACCOUNT_STATE_CACHE_TTL_SECONDS:
                return cached_state

        balance_response = self._signed_call("private/user-balance", {})
        if balance_response is None or self._error_code(balance_response) is not None:
            if self._account_state_cache is not None:
                # Un blip transitorio non deve terminare il loop veloce
                # della sessione (session_manager._fast_loop cattura
                # QUALSIASI eccezione e chiude per sempre): degradiamo
                # restituendo l'ultimo stato noto invece di sollevare.
                logger.warning("CryptoComBroker: get_account_state fallita, uso l'ultimo stato noto")
                return self._account_state_cache[1]
            msg = balance_response.get("message") if balance_response else "Crypto.com Exchange non raggiungibile"
            raise RuntimeError(f"Impossibile leggere lo stato del conto Crypto.com: {msg}")

        balance = balance_response["result"]["data"][0]
        isolated_leverage_by_id = {
            entry["isolation_id"]: float(entry["leverage"]) for entry in balance.get("isolated_positions", [])
        }

        positions_response = self._signed_call("private/get-positions", {})
        positions: list[Position] = []
        if positions_response is not None and self._error_code(positions_response) is None:
            for entry in positions_response["result"]["data"]:
                quantity = float(entry["quantity"])
                if quantity == 0:
                    continue
                cost = float(entry["cost"])
                open_position_pnl = float(entry["open_position_pnl"])
                isolation_id = entry.get("isolation_id")
                positions.append(
                    Position(
                        symbol=entry["instrument_name"],
                        quantity=quantity,
                        avg_price=abs(cost) / abs(quantity),
                        leverage=isolated_leverage_by_id.get(isolation_id, self._default_leverage),
                        initial_margin=0.0,
                        liquidation_price=None,  # non disponibile via REST get-positions
                        # Approssimazione: costo di apertura + P&L accumulato,
                        # senza una chiamata prezzo aggiuntiva.
                        market_value=cost + open_position_pnl,
                        unrealized_pnl=open_position_pnl,
                    )
                )
        else:
            logger.warning("CryptoComBroker: impossibile leggere le posizioni, nessuna posizione aperta riportata")

        fees_paid_today = self._fees_since_midnight()

        state = AccountState(
            equity=float(balance["total_margin_balance"]),
            cash=float(balance["total_available_balance"]),
            open_positions=positions,
            realized_pnl_today=float(balance["total_session_realized_pnl"]),
            unrealized_pnl_today=float(balance["total_session_unrealized_pnl"]),
            fees_paid_today=fees_paid_today,
            # Nessun campo isolato individuato per la sola componente
            # funding (a parte le fee di trading): limite noto, da
            # approfondire. Resta 0.0 in questa prima versione.
            funding_paid_today=0.0,
        )
        self._account_state_cache = (time.monotonic(), state)
        return state

    def _fees_since_midnight(self) -> float:
        midnight_utc_ms = int(time.time() // 86400 * 86400 * 1000)
        response = self._signed_call("private/get-trades", {"start_time": str(midnight_utc_ms)})
        if response is None or self._error_code(response) is not None:
            logger.warning("CryptoComBroker: impossibile leggere le fee di oggi, uso 0.0")
            return 0.0
        # Le fee sono riportate negative (una deduzione dal saldo).
        return -sum(float(trade.get("fees", 0.0)) for trade in response["result"]["data"])

    # ------------------------------------------------------------------
    # Strumenti: precisione quantità
    # ------------------------------------------------------------------

    def _get_instrument_filters(self, symbol: str) -> dict | None:
        if self._instrument_info is None:
            info = self._public_get("public/get-instruments", {})
            self._instrument_info = {}
            if info is not None and self._error_code(info) is None:
                for entry in info["result"]["data"]:
                    self._instrument_info[entry["symbol"]] = {
                        "qty_tick_size": float(entry["qty_tick_size"]),
                        "quantity_decimals": int(entry["quantity_decimals"]),
                    }
        return self._instrument_info.get(symbol)

    def _round_quantity(self, symbol: str, quantity: float) -> float | None:
        # Solo per le aperture: le chiusure non specificano mai una
        # quantità (chiusura totale nativa via private/close-position),
        # quindi qui non c'è la classe di bug "polvere residua" vista con
        # l'arrotondamento delle chiusure su Binance.
        filters = self._get_instrument_filters(symbol)
        if filters is None:
            return None

        step_size = filters["qty_tick_size"]
        rounded = math.floor(quantity / step_size) * step_size if step_size > 0 else quantity
        rounded = round(rounded, filters["quantity_decimals"])
        return rounded if rounded > 0 else None

    # ------------------------------------------------------------------
    # Esecuzione ordini
    # ------------------------------------------------------------------

    def _reject(self, symbol: str, side: OrderSide, reason: str) -> ExecutionResult:
        logger.warning("CryptoComBroker: ordine %s %s rifiutato (%s)", side.value, symbol, reason)
        return ExecutionResult(
            broker_order_id=f"CRYPTOCOM-REJECT-{int(time.time() * 1000)}",
            symbol=symbol,
            side=side,
            status=ExecutionStatus.REJECTED,
            error_message=reason,
        )

    def _poll_order_until_terminal(self, order_id: str) -> dict | None:
        for _ in range(_ORDER_POLL_MAX_ATTEMPTS):
            response = self._signed_call("private/get-order-detail", {"order_id": order_id})
            if response is None or self._error_code(response) is not None:
                return response
            result = response["result"]
            if result.get("status") in _TERMINAL_ORDER_STATUSES:
                return response
            time.sleep(_ORDER_POLL_INTERVAL_SECONDS)
        return response  # ancora non definitivo dopo il timeout: SUBMITTED (pending), non un errore

    @staticmethod
    def _map_status(crypto_com_status: str | None) -> ExecutionStatus:
        return {
            "FILLED": ExecutionStatus.FILLED,
            "REJECTED": ExecutionStatus.REJECTED,
            "CANCELED": ExecutionStatus.CANCELLED,
            "EXPIRED": ExecutionStatus.CANCELLED,
        }.get(crypto_com_status, ExecutionStatus.SUBMITTED)

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        leverage: float,
        reference_price: float,
        reduce_only: bool = False,
    ) -> ExecutionResult:
        if reduce_only:
            return self._close(symbol, side, reference_price)
        return self._open(symbol, side, quantity, leverage, reference_price)

    def _open(self, symbol: str, side: OrderSide, quantity: float, leverage: float, reference_price: float) -> ExecutionResult:
        rounded_quantity = self._round_quantity(symbol, quantity)
        if rounded_quantity is None:
            return self._reject(symbol, side, "quantità troppo piccola dopo arrotondamento al tick size dello strumento")

        params = {
            "instrument_name": symbol,
            "side": "BUY" if side is OrderSide.BUY else "SELL",
            "type": "MARKET",
            "quantity": str(rounded_quantity),
            "exec_inst": ["ISOLATED_MARGIN"],
            "leverage": str(int(round(leverage))),
        }
        isolation_id = self._isolation_id_by_symbol.get(symbol)
        if isolation_id is not None:
            params["isolation_id"] = isolation_id

        response = self._signed_call("private/create-order", params)
        result = self._submit_and_poll(symbol, side, response)
        if isinstance(result, ExecutionResult):
            return result

        order_detail = result
        new_isolation_id = order_detail.get("isolation_id")
        if new_isolation_id:
            self._isolation_id_by_symbol[symbol] = new_isolation_id

        return self._execution_result_from_order_detail(symbol, side, order_detail, realized_pnl=None)

    def _close(self, symbol: str, side: OrderSide, reference_price: float) -> ExecutionResult:
        account = self.get_account_state()
        position = next((p for p in account.open_positions if p.symbol == symbol), None)
        entry_avg_price = position.avg_price if position is not None else reference_price
        direction = 1 if (position is not None and position.quantity > 0) else -1

        params = {"instrument_name": symbol, "type": "MARKET"}
        isolation_id = self._isolation_id_by_symbol.get(symbol)
        if isolation_id is not None:
            params["isolation_id"] = isolation_id

        response = self._signed_call("private/close-position", params)
        result = self._submit_and_poll(symbol, side, response)
        if isinstance(result, ExecutionResult):
            return result

        order_detail = result
        exit_avg_price = float(order_detail.get("avg_price", 0.0))
        closed_quantity = float(order_detail.get("cumulative_quantity", 0.0))
        # P&L realizzato calcolato da noi (aritmetica esatta su prezzi
        # reali di Crypto.com): l'exchange non lo espone per singolo trade,
        # solo a livello di posizione/conto.
        realized_pnl = closed_quantity * (exit_avg_price - entry_avg_price) * direction

        if order_detail.get("status") == "FILLED":
            self._isolation_id_by_symbol.pop(symbol, None)

        return self._execution_result_from_order_detail(symbol, side, order_detail, realized_pnl=realized_pnl)

    def _submit_and_poll(self, symbol: str, side: OrderSide, response: dict | None) -> ExecutionResult | dict:
        """Ritorna un ExecutionResult se l'invio/polling fallisce apertamente,
        altrimenti il dict `result` di private/get-order-detail (stato
        definitivo o SUBMITTED dopo il timeout di polling)."""
        if response is None:
            return ExecutionResult(
                broker_order_id=f"CRYPTOCOM-ERROR-{int(time.time() * 1000)}",
                symbol=symbol,
                side=side,
                status=ExecutionStatus.ERROR,
                error_message="Crypto.com Exchange non raggiungibile",
            )

        code = self._error_code(response)
        if code is not None:
            status = ExecutionStatus.REJECTED if code in _KNOWN_REJECTION_CODES else ExecutionStatus.ERROR
            return ExecutionResult(
                broker_order_id=f"CRYPTOCOM-{status.value.upper()}-{int(time.time() * 1000)}",
                symbol=symbol,
                side=side,
                status=status,
                error_message=response.get("message", f"Errore Crypto.com {code}"),
            )

        order_id = response["result"]["order_id"]
        detail_response = self._poll_order_until_terminal(order_id)
        if detail_response is None:
            return ExecutionResult(
                broker_order_id=str(order_id),
                symbol=symbol,
                side=side,
                status=ExecutionStatus.ERROR,
                error_message="Crypto.com Exchange non raggiungibile durante la verifica dell'ordine",
            )
        detail_code = self._error_code(detail_response)
        if detail_code is not None:
            return ExecutionResult(
                broker_order_id=str(order_id),
                symbol=symbol,
                side=side,
                status=ExecutionStatus.ERROR,
                error_message=detail_response.get("message", f"Errore Crypto.com {detail_code}"),
            )

        return detail_response["result"]

    def _execution_result_from_order_detail(
        self, symbol: str, side: OrderSide, order_detail: dict, realized_pnl: float | None
    ) -> ExecutionResult:
        avg_price = float(order_detail.get("avg_price", 0.0))
        return ExecutionResult(
            broker_order_id=str(order_detail.get("order_id", "")),
            symbol=symbol,
            side=side,
            status=self._map_status(order_detail.get("status")),
            filled_quantity=float(order_detail.get("cumulative_quantity", 0.0)),
            avg_fill_price=avg_price if avg_price > 0 else None,
            fee=float(order_detail.get("cumulative_fee", 0.0)),
            realized_pnl=realized_pnl,
        )

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
