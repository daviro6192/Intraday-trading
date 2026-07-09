"""Simulatore di futures USDT-M perpetual in-memory: nessun rischio reale, ma
prezzi/funding presi da Binance quando raggiungibile, con leva, margine,
commissioni e liquidazione simulate — abbastanza realistico da poter validare
una strategia ad alta frequenza nell'arco di più giorni.

Semplificazioni esplicite (documentate qui per chi legge il codice):
- Isolated margin per posizione, una sola posizione netta per simbolo (nessun
  hedging long+short simultaneo sullo stesso simbolo).
- La leva di una posizione è fissata all'apertura; aggiunte nella stessa
  direzione riusano la leva esistente (per cambiare leva, chiudere e riaprire).
- La liquidazione è simulata come chiusura totale della posizione al prezzo di
  liquidazione stimato (formula isolated-margin semplificata), non replica
  esattamente il motore di liquidazione a cascata di un vero exchange.
"""

from __future__ import annotations

import itertools
import logging
import random
from datetime import datetime, timedelta, timezone

from common.schemas import AccountState, ExecutionResult, ExecutionStatus, FeeSchedule, OrderSide, Position
from data_sources.binance_market_data import get_funding_rate, get_mark_price

logger = logging.getLogger(__name__)

# Piccola escursione simulata rispetto al prezzo di riferimento, per non far
# eseguire ogni ordine esattamente al prezzo "di libro": approssima lo
# spread/impatto di mercato di un ordine reale.
_MAX_SIMULATED_SLIPPAGE_PCT = 0.0005


def _reference_price(symbol: str, fallback: float) -> float:
    """Prezzo di mark corrente da Binance, o `fallback` (prezzo atteso dal
    chiamante) se Binance non è raggiungibile: il simulatore non deve
    bloccarsi per l'indisponibilità di una fonte dati esterna."""
    mark_price = get_mark_price(symbol)
    return mark_price if mark_price is not None else fallback


class PaperBroker:
    def __init__(
        self,
        fee_schedule: FeeSchedule,
        starting_cash: float = 100_000.0,
        maintenance_margin_rate_pct: float = 0.005,
    ) -> None:
        self._fee_schedule = fee_schedule
        self._maintenance_margin_rate_pct = maintenance_margin_rate_pct
        self._cash = starting_cash
        self._positions: dict[str, Position] = {}
        self._last_funding_at: dict[str, datetime] = {}
        self._realized_pnl_today = 0.0
        self._fees_paid_today = 0.0
        self._funding_paid_today = 0.0
        self._order_id_counter = itertools.count(1)
        self._connected = False

    def connect(self) -> None:
        self._connected = True
        logger.info("PaperBroker connesso (simulazione futures perpetual, capitale iniziale=%.2f)", self._cash)

    def disconnect(self) -> None:
        self._connected = False

    # ------------------------------------------------------------------
    # Persistenza stato tra richieste/cicli
    # ------------------------------------------------------------------

    def export_state(self) -> dict:
        return {
            "cash": self._cash,
            "realized_pnl_today": self._realized_pnl_today,
            "fees_paid_today": self._fees_paid_today,
            "funding_paid_today": self._funding_paid_today,
            "positions": {
                symbol: {
                    "quantity": position.quantity,
                    "avg_price": position.avg_price,
                    "leverage": position.leverage,
                    "initial_margin": position.initial_margin,
                    "liquidation_price": position.liquidation_price,
                }
                for symbol, position in self._positions.items()
            },
            "last_funding_at": {symbol: dt.isoformat() for symbol, dt in self._last_funding_at.items()},
        }

    def load_state(self, state: dict) -> None:
        self._cash = state.get("cash", self._cash)
        self._realized_pnl_today = state.get("realized_pnl_today", 0.0)
        self._fees_paid_today = state.get("fees_paid_today", 0.0)
        self._funding_paid_today = state.get("funding_paid_today", 0.0)
        self._positions = {}
        for symbol, data in state.get("positions", {}).items():
            self._positions[symbol] = Position(
                symbol=symbol,
                quantity=data["quantity"],
                avg_price=data["avg_price"],
                leverage=data.get("leverage", 1.0),
                initial_margin=data.get("initial_margin", 0.0),
                liquidation_price=data.get("liquidation_price"),
                market_value=0.0,
                unrealized_pnl=0.0,
            )
        self._last_funding_at = {
            symbol: datetime.fromisoformat(iso) for symbol, iso in state.get("last_funding_at", {}).items()
        }

    # ------------------------------------------------------------------
    # Lettura stato conto: funding accrual + liquidazioni + mark-to-market
    # ------------------------------------------------------------------

    def get_account_state(self) -> AccountState:
        self._accrue_funding()
        self._check_liquidations()

        positions: list[Position] = []
        total_market_value = 0.0
        total_unrealized = 0.0

        for symbol, position in self._positions.items():
            mark_price = _reference_price(symbol, position.avg_price)
            market_value = position.quantity * mark_price
            unrealized_pnl = position.quantity * (mark_price - position.avg_price)

            positions.append(
                Position(
                    symbol=symbol,
                    quantity=position.quantity,
                    avg_price=position.avg_price,
                    leverage=position.leverage,
                    initial_margin=position.initial_margin,
                    liquidation_price=position.liquidation_price,
                    market_value=market_value,
                    unrealized_pnl=unrealized_pnl,
                )
            )
            total_market_value += market_value
            total_unrealized += unrealized_pnl

        return AccountState(
            equity=self._cash + total_unrealized,
            cash=self._cash,
            open_positions=positions,
            realized_pnl_today=self._realized_pnl_today,
            unrealized_pnl_today=total_unrealized,
            fees_paid_today=self._fees_paid_today,
            funding_paid_today=self._funding_paid_today,
            as_of=datetime.now(timezone.utc),
        )

    def _accrue_funding(self) -> None:
        """Applica il funding rate periodico (long paga/short riceve se il
        tasso è positivo, o viceversa) per ogni intervallo trascorso dall'
        ultimo accrual, usando il tasso corrente da Binance con fallback a
        quello di default configurato se non raggiungibile."""
        interval_hours = self._fee_schedule.funding_interval_hours
        now = datetime.now(timezone.utc)

        for symbol, position in list(self._positions.items()):
            last_at = self._last_funding_at.get(symbol, now)
            elapsed_hours = (now - last_at).total_seconds() / 3600
            intervals = int(elapsed_hours // interval_hours) if interval_hours > 0 else 0
            if intervals <= 0:
                continue

            funding_rate = get_funding_rate(symbol)
            if funding_rate is None:
                funding_rate = self._fee_schedule.default_funding_rate_fallback_pct

            mark_price = _reference_price(symbol, position.avg_price)
            notional = abs(position.quantity) * mark_price
            direction = 1 if position.quantity > 0 else -1
            payment = direction * notional * funding_rate * intervals

            self._cash -= payment
            self._funding_paid_today += payment
            self._last_funding_at[symbol] = last_at + timedelta(hours=interval_hours * intervals)

    def _check_liquidations(self) -> None:
        """Forza la chiusura di ogni posizione il cui prezzo di mark ha
        superato il prezzo di liquidazione stimato, realizzando la perdita
        (circa pari al margine iniziale, come in un vero conto isolated-margin)."""
        for symbol, position in list(self._positions.items()):
            if position.liquidation_price is None:
                continue

            mark_price = _reference_price(symbol, position.avg_price)
            is_long = position.quantity > 0
            liquidated = (is_long and mark_price <= position.liquidation_price) or (
                not is_long and mark_price >= position.liquidation_price
            )
            if not liquidated:
                continue

            direction = 1 if is_long else -1
            realized = position.quantity * (position.liquidation_price - position.avg_price) * direction
            self._realized_pnl_today += realized
            self._cash += realized
            logger.warning(
                "PaperBroker: posizione %s LIQUIDATA a %.4f (prezzo di mark %.4f, leva %.1fx, P&L realizzato %.2f)",
                symbol,
                position.liquidation_price,
                mark_price,
                position.leverage,
                realized,
            )
            del self._positions[symbol]
            self._last_funding_at.pop(symbol, None)

    # ------------------------------------------------------------------
    # Esecuzione ordini
    # ------------------------------------------------------------------

    def _locked_margin(self) -> float:
        return sum(position.initial_margin for position in self._positions.values())

    def _available_margin(self) -> float:
        return self._cash - self._locked_margin()

    @staticmethod
    def _liquidation_price(avg_price: float, leverage: float, maintenance_margin_rate_pct: float, is_long: bool) -> float:
        if is_long:
            return avg_price * (1 - 1 / leverage + maintenance_margin_rate_pct)
        return avg_price * (1 + 1 / leverage - maintenance_margin_rate_pct)

    def _reject(self, symbol: str, side: OrderSide, reason: str) -> ExecutionResult:
        logger.warning("PaperBroker: ordine %s %s rifiutato (%s)", side.value, symbol, reason)
        return ExecutionResult(
            broker_order_id=f"PAPER-{next(self._order_id_counter)}",
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
        existing = self._positions.get(symbol)

        if reduce_only and (existing is None or existing.quantity == 0):
            return self._reject(symbol, side, "reduce_only richiesto ma non c'è alcuna posizione aperta")

        mark_price = _reference_price(symbol, reference_price)
        slippage_pct = random.uniform(0.0, _MAX_SIMULATED_SLIPPAGE_PCT)
        fill_price = mark_price * (1 + slippage_pct) if side is OrderSide.BUY else mark_price * (1 - slippage_pct)

        signed_qty = quantity if side is OrderSide.BUY else -quantity

        if reduce_only and existing is not None:
            # Non permettere che un ordine "solo riduzione" flippi la posizione:
            # se richiesta una quantità maggiore di quella aperta, la limitiamo.
            max_reducible = abs(existing.quantity)
            if abs(signed_qty) > max_reducible:
                signed_qty = max_reducible if signed_qty > 0 else -max_reducible

        fee = quantity * fill_price * self._fee_schedule.taker_fee_pct
        realized_pnl: float | None = None

        if existing is None or existing.quantity == 0:
            required_margin = quantity * fill_price / leverage
            if required_margin > self._available_margin():
                return self._reject(symbol, side, "margine disponibile insufficiente per aprire la posizione")

            self._cash -= fee
            self._fees_paid_today += fee
            liquidation_price = self._liquidation_price(
                fill_price, leverage, self._maintenance_margin_rate_pct, signed_qty > 0
            )
            self._positions[symbol] = Position(
                symbol=symbol,
                quantity=signed_qty,
                avg_price=fill_price,
                leverage=leverage,
                initial_margin=required_margin,
                liquidation_price=liquidation_price,
                market_value=0.0,
                unrealized_pnl=0.0,
            )
            self._last_funding_at[symbol] = datetime.now(timezone.utc)
        else:
            same_direction = (existing.quantity > 0) == (signed_qty > 0)

            if same_direction:
                new_quantity = existing.quantity + signed_qty
                new_notional = abs(new_quantity) * (
                    (abs(existing.quantity) * existing.avg_price + abs(signed_qty) * fill_price) / abs(new_quantity)
                )
                new_avg_price = new_notional / abs(new_quantity)
                required_margin = new_notional / existing.leverage
                additional_margin = required_margin - existing.initial_margin
                if additional_margin > self._available_margin():
                    return self._reject(symbol, side, "margine disponibile insufficiente per aumentare la posizione")

                self._cash -= fee
                self._fees_paid_today += fee
                existing.quantity = new_quantity
                existing.avg_price = new_avg_price
                existing.initial_margin = required_margin
                existing.liquidation_price = self._liquidation_price(
                    new_avg_price, existing.leverage, self._maintenance_margin_rate_pct, new_quantity > 0
                )
            else:
                closing_qty = min(abs(existing.quantity), abs(signed_qty))
                direction = 1 if existing.quantity > 0 else -1
                realized_pnl = closing_qty * (fill_price - existing.avg_price) * direction
                self._realized_pnl_today += realized_pnl
                self._cash += realized_pnl
                self._cash -= fee
                self._fees_paid_today += fee

                was_flip = abs(signed_qty) > abs(existing.quantity)
                new_quantity = existing.quantity + signed_qty

                if new_quantity == 0:
                    del self._positions[symbol]
                    self._last_funding_at.pop(symbol, None)
                elif was_flip:
                    flip_quantity = new_quantity
                    required_margin = abs(flip_quantity) * fill_price / leverage
                    if required_margin > self._available_margin():
                        # Il margine per la nuova gamba non basta: chiudiamo comunque
                        # la posizione precedente (già fillata sopra) ma non apriamo
                        # la nuova, evitando di lasciare un conto sovra-esposto.
                        del self._positions[symbol]
                        self._last_funding_at.pop(symbol, None)
                        logger.warning(
                            "PaperBroker: chiusura di %s eseguita, riapertura in direzione opposta rifiutata "
                            "(margine insufficiente)",
                            symbol,
                        )
                    else:
                        liquidation_price = self._liquidation_price(
                            fill_price, leverage, self._maintenance_margin_rate_pct, flip_quantity > 0
                        )
                        self._positions[symbol] = Position(
                            symbol=symbol,
                            quantity=flip_quantity,
                            avg_price=fill_price,
                            leverage=leverage,
                            initial_margin=required_margin,
                            liquidation_price=liquidation_price,
                            market_value=0.0,
                            unrealized_pnl=0.0,
                        )
                        self._last_funding_at[symbol] = datetime.now(timezone.utc)
                else:
                    new_notional = abs(new_quantity) * existing.avg_price
                    existing.quantity = new_quantity
                    existing.initial_margin = new_notional / existing.leverage
                    existing.liquidation_price = self._liquidation_price(
                        existing.avg_price, existing.leverage, self._maintenance_margin_rate_pct, new_quantity > 0
                    )

        order_id = f"PAPER-{next(self._order_id_counter)}"
        logger.info(
            "PaperBroker: eseguito %s %s x%.6f a %.4f (leva %.1fx, order_id=%s, fee=%.4f)",
            side.value,
            symbol,
            quantity,
            fill_price,
            leverage,
            order_id,
            fee,
        )

        return ExecutionResult(
            broker_order_id=order_id,
            symbol=symbol,
            side=side,
            status=ExecutionStatus.FILLED,
            filled_quantity=quantity,
            avg_fill_price=fill_price,
            fee=fee,
            realized_pnl=realized_pnl,
        )

    def close_position(self, symbol: str) -> ExecutionResult | None:
        position = self._positions.get(symbol)
        if position is None or position.quantity == 0:
            return None

        closing_side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
        reference_price = _reference_price(symbol, position.avg_price)
        return self.place_order(
            symbol=symbol,
            side=closing_side,
            quantity=abs(position.quantity),
            leverage=position.leverage,
            reference_price=reference_price,
            reduce_only=True,
        )

    def cancel_order(self, broker_order_id: str) -> None:
        logger.info("PaperBroker: cancel_order ignorato per %s (fill immediato simulato)", broker_order_id)
