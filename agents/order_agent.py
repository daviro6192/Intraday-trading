"""Agente 3: entra/esce da posizioni long/short su Binance in modo meccanico
e ad alta frequenza — NIENTE chiamate a Claude qui (la direzione arriva
dall'Agente 2, la conferma di timing è puramente tecnica). Fonde quella che
in precedenza era la generazione di proposte (Order Agent) e l'esecuzione
sul broker (Execution Agent): decide ED esegue, passando prima dal gate
deterministico dell'Agente 4 (agents.risk_agent.evaluate_trade_risk).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import ta

from agents.risk_agent import evaluate_trade_risk, finalize_risk_decision
from broker.base import BrokerClient
from common.schemas import (
    AccountState,
    ExecutionResult,
    FeeSchedule,
    OrderIntent,
    OrderSide,
    Position,
    RiskDecision,
    RiskDecisionStatus,
    RiskParameters,
    StrategyView,
    TradeDirection,
)

logger = logging.getLogger(__name__)

_EMA_FAST_PERIOD = 9
_EMA_SLOW_PERIOD = 21
_ATR_PERIOD = 14
_STOP_LOSS_ATR_MULTIPLIER = 1.5
_TAKE_PROFIT_ATR_MULTIPLIER = 2.5
# Fallback se non ci sono abbastanza klines per calcolare l'ATR (es. avvio a freddo).
_FALLBACK_STOP_PCT = 0.01
_FALLBACK_TAKE_PROFIT_PCT = 0.015


def _timing_signal(klines: list[dict] | None) -> TradeDirection | None:
    """Conferma tecnica del momento di ingresso: incrocio EMA veloce/lenta sui
    prezzi di chiusura. Ritorna None se non ci sono abbastanza dati (l'Order
    Agent aspetta il tick successivo invece di entrare alla cieca)."""
    if not klines or len(klines) < _EMA_SLOW_PERIOD:
        return None

    closes = pd.Series([k["close"] for k in klines])
    ema_fast = ta.trend.EMAIndicator(closes, window=_EMA_FAST_PERIOD).ema_indicator().iloc[-1]
    ema_slow = ta.trend.EMAIndicator(closes, window=_EMA_SLOW_PERIOD).ema_indicator().iloc[-1]

    if ema_fast > ema_slow:
        return TradeDirection.LONG
    if ema_fast < ema_slow:
        return TradeDirection.SHORT
    return None


def _atr(klines: list[dict] | None) -> float | None:
    if not klines or len(klines) < _ATR_PERIOD:
        return None
    df = pd.DataFrame(klines)
    atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=_ATR_PERIOD)
    return float(atr.average_true_range().iloc[-1])


@dataclass
class OrderAgentTick:
    """Esito di un singolo tick del loop veloce per un simbolo: intent
    generato (se c'è stata un'azione), decisione del risk gate, esito
    dell'esecuzione sul broker, ed eventuali nuovi stop/take-profit interni
    da tenere in bookkeeping per il prossimo tick (None se la posizione è
    stata chiusa o non è stata aperta)."""

    intent: OrderIntent | None = None
    risk_decision: RiskDecision | None = None
    execution_result: ExecutionResult | None = None
    new_stop_loss: float | None = None
    new_take_profit: float | None = None


class OrderAgent:
    def __init__(
        self,
        broker: BrokerClient,
        fee_schedule: FeeSchedule,
        default_leverage: float,
        max_risk_per_trade_pct: float,
        min_conviction_to_trade: float = 0.3,
    ) -> None:
        self._broker = broker
        self._fee_schedule = fee_schedule
        self._default_leverage = default_leverage
        self._max_risk_per_trade_pct = max_risk_per_trade_pct
        self._min_conviction_to_trade = min_conviction_to_trade

    def run_tick(
        self,
        symbol: str,
        view: StrategyView,
        position: Position | None,
        mark_price: float,
        klines: list[dict] | None,
        account: AccountState,
        risk_params: RiskParameters,
        current_stop_loss: float | None,
        current_take_profit: float | None,
    ) -> OrderAgentTick:
        has_position = position is not None and position.quantity != 0
        position_is_long = has_position and position.quantity > 0

        # 1. Direzione flat o conviction troppo bassa: chiudi se c'è una posizione.
        if view.direction is TradeDirection.FLAT or view.conviction < self._min_conviction_to_trade:
            if has_position:
                return self._close(symbol, position, mark_price, account, risk_params, "strategia flat o bassa conviction")
            return OrderAgentTick()

        # 2. Posizione aperta: verifica coerenza con la vista corrente.
        if has_position:
            position_direction = TradeDirection.LONG if position_is_long else TradeDirection.SHORT
            if position_direction is not view.direction:
                return self._close(symbol, position, mark_price, account, risk_params, "la strategia ha invertito direzione")

            if current_stop_loss is not None and self._breached(mark_price, current_stop_loss, position_is_long, is_stop=True):
                return self._close(symbol, position, mark_price, account, risk_params, "stop-loss interno raggiunto")

            if current_take_profit is not None and self._breached(
                mark_price, current_take_profit, position_is_long, is_stop=False
            ):
                return self._close(symbol, position, mark_price, account, risk_params, "take-profit interno raggiunto")

            # Posizione coerente ed entro i livelli: nessuna azione questo tick.
            return OrderAgentTick(new_stop_loss=current_stop_loss, new_take_profit=current_take_profit)

        # 3. Nessuna posizione: serve una conferma tecnica di timing prima di entrare.
        timing_direction = _timing_signal(klines)
        if timing_direction is not view.direction:
            return OrderAgentTick()

        return self._open(symbol, view.direction, mark_price, klines, account, risk_params)

    @staticmethod
    def _breached(mark_price: float, level: float, is_long: bool, is_stop: bool) -> bool:
        if is_long:
            return mark_price <= level if is_stop else mark_price >= level
        return mark_price >= level if is_stop else mark_price <= level

    def _open(
        self,
        symbol: str,
        direction: TradeDirection,
        mark_price: float,
        klines: list[dict] | None,
        account: AccountState,
        risk_params: RiskParameters,
    ) -> OrderAgentTick:
        atr = _atr(klines)
        if atr is not None and atr > 0:
            stop_distance = atr * _STOP_LOSS_ATR_MULTIPLIER
            take_distance = atr * _TAKE_PROFIT_ATR_MULTIPLIER
        else:
            stop_distance = mark_price * _FALLBACK_STOP_PCT
            take_distance = mark_price * _FALLBACK_TAKE_PROFIT_PCT

        is_long = direction is TradeDirection.LONG
        stop_loss = mark_price - stop_distance if is_long else mark_price + stop_distance
        take_profit = mark_price + take_distance if is_long else mark_price - take_distance

        risk_per_unit = abs(mark_price - stop_loss)
        if risk_per_unit <= 0:
            return OrderAgentTick()

        quantity = (self._max_risk_per_trade_pct * account.equity) / risk_per_unit
        if quantity <= 0:
            return OrderAgentTick()

        intent = OrderIntent(
            symbol=symbol,
            side=OrderSide.BUY if is_long else OrderSide.SELL,
            quantity=quantity,
            leverage=self._default_leverage,
            reduce_only=False,
            reference_price=mark_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason=f"Apertura {direction.value} su conferma tecnica EMA{_EMA_FAST_PERIOD}/{_EMA_SLOW_PERIOD}",
        )

        return self._evaluate_and_execute(intent, None, account, risk_params, stop_loss, take_profit)

    def _close(
        self,
        symbol: str,
        position: Position,
        mark_price: float,
        account: AccountState,
        risk_params: RiskParameters,
        reason: str,
    ) -> OrderAgentTick:
        closing_side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
        intent = OrderIntent(
            symbol=symbol,
            side=closing_side,
            quantity=abs(position.quantity),
            leverage=position.leverage,
            reduce_only=True,
            reference_price=mark_price,
            reason=reason,
        )
        return self._evaluate_and_execute(intent, position, account, risk_params, None, None)

    def _evaluate_and_execute(
        self,
        intent: OrderIntent,
        position: Position | None,
        account: AccountState,
        risk_params: RiskParameters,
        new_stop_loss: float | None,
        new_take_profit: float | None,
    ) -> OrderAgentTick:
        hard_result = evaluate_trade_risk(intent, account, position, risk_params, self._fee_schedule)
        decision = finalize_risk_decision(intent, hard_result)

        if decision.status is RiskDecisionStatus.REJECTED:
            logger.info("Order Agent: intent %s %s rifiutato dal risk gate: %s", intent.side.value, intent.symbol, decision.reasoning)
            return OrderAgentTick(intent=intent, risk_decision=decision)

        execution_result = self._broker.place_order(
            symbol=intent.symbol,
            side=intent.side,
            quantity=decision.final_quantity,
            leverage=decision.final_leverage,
            reference_price=intent.reference_price,
            reduce_only=intent.reduce_only,
        )

        if intent.reduce_only:
            new_stop_loss, new_take_profit = None, None

        return OrderAgentTick(
            intent=intent,
            risk_decision=decision,
            execution_result=execution_result,
            new_stop_loss=new_stop_loss,
            new_take_profit=new_take_profit,
        )
