"""Simulatore di broker in-memory: nessuna dipendenza da un conto reale, ma
i fill e il valore delle posizioni riflettono i prezzi di mercato correnti
(quando disponibili) invece di limitarsi al prezzo proposto dall'agente.
È il default per sviluppo e test, e il fallback quando IB Gateway non è
raggiungibile.
"""

from __future__ import annotations

import itertools
import logging
import random
from datetime import datetime, timezone

from common.schemas import AccountState, ExecutionResult, ExecutionStatus, Market, OrderSide, Position, RiskDecision
from data_sources.market_data import get_market_snapshot

logger = logging.getLogger(__name__)

# Piccola escursione simulata rispetto al prezzo di riferimento, per non far
# eseguire ogni ordine esattamente al prezzo "di libro": approssima lo
# spread/impatto di mercato di un ordine reale.
_MAX_SIMULATED_SLIPPAGE_PCT = 0.0005


def _reference_price(symbol: str, market: Market, fallback: float) -> float:
    """Prezzo corrente da usare per fill/mark-to-market. Ritorna `fallback`
    (il prezzo proposto dall'agente) se il dato di mercato non è disponibile
    (rete assente, simbolo non coperto, ecc.) — un simulatore non deve
    bloccarsi per l'indisponibilità di una fonte dati esterna."""
    snapshot = get_market_snapshot(symbol, market)
    last_price = snapshot.get("last_price") if snapshot else None
    return float(last_price) if last_price else fallback


class PaperBroker:
    def __init__(self, starting_cash: float = 100_000.0) -> None:
        self._cash = starting_cash
        self._positions: dict[str, Position] = {}
        self._position_markets: dict[str, Market] = {}
        self._realized_pnl_today = 0.0
        self._order_id_counter = itertools.count(1)
        self._connected = False

    def connect(self) -> None:
        self._connected = True
        logger.info("PaperBroker connesso (simulazione, capitale iniziale=%.2f)", self._cash)

    def disconnect(self) -> None:
        self._connected = False

    def get_account_state(self) -> AccountState:
        """Rivaluta ogni posizione aperta al prezzo di mercato corrente (se
        disponibile), così equity e P&L riflettono i movimenti di mercato
        anche tra un ciclo e l'altro, non solo al momento del fill."""
        positions: list[Position] = []
        total_market_value = 0.0
        total_unrealized = 0.0

        for symbol, position in self._positions.items():
            market = self._position_markets.get(symbol)
            last_price = (
                _reference_price(symbol, market, position.avg_price) if market is not None else position.avg_price
            )
            market_value = position.quantity * last_price
            unrealized_pnl = position.quantity * (last_price - position.avg_price)

            positions.append(
                Position(
                    symbol=symbol,
                    quantity=position.quantity,
                    avg_price=position.avg_price,
                    market_value=market_value,
                    unrealized_pnl=unrealized_pnl,
                )
            )
            total_market_value += market_value
            total_unrealized += unrealized_pnl

        return AccountState(
            equity=self._cash + total_market_value,
            cash=self._cash,
            open_positions=positions,
            realized_pnl_today=self._realized_pnl_today,
            unrealized_pnl_today=total_unrealized,
            as_of=datetime.now(timezone.utc),
        )

    def _apply_fill(self, symbol: str, signed_qty: float, fill_price: float, market: Market) -> None:
        """Aggiorna quantità/prezzo medio della posizione, realizzando il P&L
        sulla porzione eventualmente chiusa (media pesata sull'apertura,
        P&L realizzato sulla chiusura — come un vero conto)."""
        existing = self._positions.get(symbol)

        if existing is None or existing.quantity == 0:
            self._positions[symbol] = Position(
                symbol=symbol, quantity=signed_qty, avg_price=fill_price, market_value=0.0, unrealized_pnl=0.0
            )
            self._position_markets[symbol] = market
            return

        same_direction = (existing.quantity > 0) == (signed_qty > 0)
        if same_direction:
            new_quantity = existing.quantity + signed_qty
            existing.avg_price = (existing.quantity * existing.avg_price + signed_qty * fill_price) / new_quantity
            existing.quantity = new_quantity
        else:
            closing_qty = min(abs(existing.quantity), abs(signed_qty))
            direction = 1 if existing.quantity > 0 else -1
            self._realized_pnl_today += closing_qty * (fill_price - existing.avg_price) * direction

            was_flip = abs(signed_qty) > abs(existing.quantity)
            existing.quantity += signed_qty
            if was_flip:
                existing.avg_price = fill_price

        if existing.quantity == 0:
            del self._positions[symbol]
            self._position_markets.pop(symbol, None)
        else:
            self._positions[symbol] = existing
            self._position_markets[symbol] = market

    def place_bracket_order(self, decision: RiskDecision) -> ExecutionResult:
        proposal = decision.proposal
        quantity = decision.final_quantity

        reference_price = _reference_price(proposal.symbol, proposal.market, proposal.entry_price)
        slippage_pct = random.uniform(0.0, _MAX_SIMULATED_SLIPPAGE_PCT)
        fill_price = (
            reference_price * (1 + slippage_pct) if proposal.side is OrderSide.BUY else reference_price * (1 - slippage_pct)
        )

        signed_qty = quantity if proposal.side is OrderSide.BUY else -quantity
        notional = quantity * fill_price
        if proposal.side is OrderSide.BUY:
            self._cash -= notional
        else:
            self._cash += notional

        self._apply_fill(proposal.symbol, signed_qty, fill_price, proposal.market)

        order_id = f"PAPER-{next(self._order_id_counter)}"
        logger.info(
            "PaperBroker: eseguito %s %s x%.4f a %.4f (order_id=%s, prezzo riferimento=%.4f)",
            proposal.side.value,
            proposal.symbol,
            quantity,
            fill_price,
            order_id,
            reference_price,
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
