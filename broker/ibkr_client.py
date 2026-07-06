"""Esecuzione ordini reale su Interactive Brokers via ib_async
(fork mantenuto di ib_insync, ora archiviato).

L'import di ib_async è volutamente lazy (dentro __init__): questo modulo può
essere importato anche in ambienti senza IB Gateway/ib_async installato
(es. CI, container di solo test con PaperBroker); l'errore compare solo se si
prova davvero a istanziare IBKRClient.
"""

from __future__ import annotations

import logging

from common.schemas import AccountState, ExecutionResult, ExecutionStatus, Market, OrderSide, Position, RiskDecision

logger = logging.getLogger(__name__)

_IB_STATUS_MAP = {
    "PendingSubmit": ExecutionStatus.SUBMITTED,
    "PreSubmitted": ExecutionStatus.SUBMITTED,
    "Submitted": ExecutionStatus.SUBMITTED,
    "Filled": ExecutionStatus.FILLED,
    "Cancelled": ExecutionStatus.CANCELLED,
    "ApiCancelled": ExecutionStatus.CANCELLED,
    "Inactive": ExecutionStatus.ERROR,
}

# Mappatura suffisso ticker Borsa Italiana (usato in trading.yaml, es. ENI.MI) -> exchange IBKR
_EU_EXCHANGE_BY_SUFFIX = {
    "MI": "BVME",
}


class IBKRClient:
    def __init__(self, host: str, port: int, client_id: int) -> None:
        try:
            from ib_async import IB
        except ImportError as exc:
            raise ImportError(
                "ib_async non installato: richiesto solo per l'esecuzione reale su Interactive Brokers "
                "(pip install ib_async). Per sviluppo/test usare broker.paper_broker.PaperBroker."
            ) from exc

        self._ib = IB()
        self._host = host
        self._port = port
        self._client_id = client_id

    def connect(self) -> None:
        self._ib.connect(self._host, self._port, clientId=self._client_id)
        logger.info("Connesso a IB Gateway/TWS su %s:%s (clientId=%s)", self._host, self._port, self._client_id)

    def disconnect(self) -> None:
        self._ib.disconnect()

    def _build_contract(self, symbol: str, market: Market):
        from ib_async import Crypto, Forex, Stock

        if market is Market.US_EQUITY:
            return Stock(symbol, "SMART", "USD")
        if market is Market.EU_EQUITY:
            local_symbol, _, suffix = symbol.partition(".")
            exchange = _EU_EXCHANGE_BY_SUFFIX.get(suffix, "SMART")
            return Stock(local_symbol, exchange, "EUR")
        if market is Market.FOREX:
            return Forex(symbol)
        if market is Market.CRYPTO:
            base, _, quote = symbol.partition("/")
            return Crypto(base, "PAXOS", quote or "USD")
        raise ValueError(f"Mercato non supportato da IBKRClient: {market}")

    def get_account_state(self) -> AccountState:
        summary = {row.tag: row.value for row in self._ib.accountSummary()}
        equity = float(summary.get("NetLiquidation", 0.0))
        cash = float(summary.get("TotalCashValue", 0.0))

        positions = [
            Position(
                symbol=p.contract.symbol,
                quantity=float(p.position),
                avg_price=float(p.avgCost),
            )
            for p in self._ib.positions()
        ]

        return AccountState(equity=equity, cash=cash, open_positions=positions)

    def place_bracket_order(self, decision: RiskDecision) -> ExecutionResult:
        proposal = decision.proposal
        contract = self._build_contract(proposal.symbol, proposal.market)
        self._ib.qualifyContracts(contract)

        action = "BUY" if proposal.side is OrderSide.BUY else "SELL"
        bracket = self._ib.bracketOrder(
            action,
            decision.final_quantity,
            limitPrice=proposal.entry_price,
            takeProfitPrice=decision.final_take_profit,
            stopLossPrice=decision.final_stop_loss,
        )
        for order in bracket:
            self._ib.placeOrder(contract, order)

        self._ib.sleep(1)
        parent = bracket.parent

        return ExecutionResult(
            broker_order_id=str(parent.orderId),
            symbol=proposal.symbol,
            side=proposal.side,
            status=_IB_STATUS_MAP.get(parent.orderStatus.status, ExecutionStatus.SUBMITTED),
            filled_quantity=float(parent.orderStatus.filled or 0.0),
            avg_fill_price=float(parent.orderStatus.avgFillPrice) if parent.orderStatus.avgFillPrice else None,
        )

    def cancel_order(self, broker_order_id: str) -> None:
        for trade in self._ib.trades():
            if str(trade.order.orderId) == broker_order_id:
                self._ib.cancelOrder(trade.order)
                return
        logger.warning("cancel_order: nessun ordine trovato con id %s", broker_order_id)
