"""Simulatore di broker in-memory: nessuna dipendenza esterna, esegue subito
gli ordini al prezzo di entry proposto. È il default per sviluppo e test, e
il fallback quando IB Gateway non è raggiungibile."""

from __future__ import annotations

import itertools
import logging
from datetime import datetime, timezone

from common.schemas import AccountState, ExecutionResult, ExecutionStatus, OrderSide, Position, RiskDecision

logger = logging.getLogger(__name__)


class PaperBroker:
    def __init__(self, starting_cash: float = 100_000.0) -> None:
        self._cash = starting_cash
        self._positions: dict[str, Position] = {}
        self._realized_pnl_today = 0.0
        self._order_id_counter = itertools.count(1)
        self._connected = False

    def connect(self) -> None:
        self._connected = True
        logger.info("PaperBroker connesso (simulazione, capitale iniziale=%.2f)", self._cash)

    def disconnect(self) -> None:
        self._connected = False

    def get_account_state(self) -> AccountState:
        market_value = sum(p.market_value for p in self._positions.values())
        unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        return AccountState(
            equity=self._cash + market_value,
            cash=self._cash,
            open_positions=list(self._positions.values()),
            realized_pnl_today=self._realized_pnl_today,
            unrealized_pnl_today=unrealized,
            as_of=datetime.now(timezone.utc),
        )

    def place_bracket_order(self, decision: RiskDecision) -> ExecutionResult:
        proposal = decision.proposal
        quantity = decision.final_quantity
        fill_price = proposal.entry_price
        notional = quantity * fill_price

        if proposal.side is OrderSide.BUY:
            self._cash -= notional
        else:
            self._cash += notional

        existing = self._positions.get(proposal.symbol)
        signed_qty = quantity if proposal.side is OrderSide.BUY else -quantity
        if existing is None:
            self._positions[proposal.symbol] = Position(
                symbol=proposal.symbol,
                quantity=signed_qty,
                avg_price=fill_price,
                market_value=notional,
                unrealized_pnl=0.0,
            )
        else:
            new_quantity = existing.quantity + signed_qty
            if new_quantity == 0:
                del self._positions[proposal.symbol]
            else:
                existing.quantity = new_quantity
                existing.avg_price = fill_price
                existing.market_value = new_quantity * fill_price
                self._positions[proposal.symbol] = existing

        order_id = f"PAPER-{next(self._order_id_counter)}"
        logger.info(
            "PaperBroker: eseguito %s %s x%.4f a %.4f (order_id=%s)",
            proposal.side.value,
            proposal.symbol,
            quantity,
            fill_price,
            order_id,
        )

        return ExecutionResult(
            broker_order_id=order_id,
            symbol=proposal.symbol,
            side=proposal.side,
            status=ExecutionStatus.FILLED,
            filled_quantity=quantity,
            avg_fill_price=fill_price,
        )

    def cancel_order(self, broker_order_id: str) -> None:
        logger.info("PaperBroker: cancel_order ignorato per %s (fill immediato simulato)", broker_order_id)
