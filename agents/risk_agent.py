"""Agente 4: risk manager a due velocità.

- `evaluate_trade_risk` / `finalize_risk_decision`: funzioni pure, deterministiche,
  NIENTE Claude — girano ad ogni singolo intent nel loop veloce dell'Agente 3.
  Controllano leva, esposizione massima, simboli in pausa, perdita giornaliera
  e, soprattutto, la fee-awareness: un intent che apre una posizione viene
  rifiutato se il profitto atteso (in base allo stop/take-profit interni) non
  supera le commissioni di round-trip (+ funding stimato) di un fattore
  minimo configurato. Chiudere una posizione (reduce_only) non viene MAI
  bloccato: un sistema di rischio non deve mai impedire di uscire da un
  trade.
- `RiskReviewAgent`: Claude, gira sulla stessa cadenza lenta di Agente 1/2.
  Aggiorna i `RiskParameters` usati dal controllo deterministico (es. riduce
  la leva massima, mette in pausa un simbolo) in base alle performance
  recenti. Il giudizio dell'LLM può SOLO restringere, mai allargare, i limiti
  hard-coded configurati in trading.yaml — stesso principio di sicurezza
  della versione precedente di questo agente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from agents.base import Agent
from common.claude_client import ClaudeClient, json_dumps_compact
from common.schemas import (
    AccountState,
    FeeSchedule,
    OrderIntent,
    Position,
    RiskDecision,
    RiskDecisionStatus,
    RiskParameters,
)


@dataclass
class HardLimitResult:
    max_allowed_quantity: float
    max_allowed_leverage: float
    failed_checks: list[str] = field(default_factory=list)
    hard_reject: bool = False
    estimated_round_trip_fee: float = 0.0


def evaluate_trade_risk(
    intent: OrderIntent,
    account: AccountState,
    position: Position | None,
    risk_params: RiskParameters,
    fee_schedule: FeeSchedule,
) -> HardLimitResult:
    """Applica i limiti di rischio deterministici. Funzione pura, senza
    chiamate a Claude: è la barriera di sicurezza non aggirabile dal giudizio
    dell'LLM (che entra in gioco solo a monte, in RiskReviewAgent, per
    aggiornare `risk_params` stesso — mai per approvare un singolo trade)."""

    # Chiudere/ridurre una posizione non va mai bloccato: un sistema di
    # rischio non deve mai impedire l'uscita da un trade.
    if intent.reduce_only:
        return HardLimitResult(intent.quantity, intent.leverage, [], False, 0.0)

    equity = account.equity
    if equity <= 0:
        return HardLimitResult(0.0, 0.0, ["Equity del conto non positiva"], True)

    max_daily_loss = risk_params.max_daily_loss_pct * equity
    if account.total_pnl_today <= -max_daily_loss:
        return HardLimitResult(
            0.0, 0.0, [f"Limite di perdita giornaliera raggiunto (P&L oggi: {account.total_pnl_today:.2f})"], True
        )

    if intent.symbol in risk_params.paused_symbols:
        return HardLimitResult(0.0, 0.0, [f"Simbolo {intent.symbol} in pausa per decisione del risk manager"], True)

    failed_checks: list[str] = []
    max_allowed_leverage = min(intent.leverage, risk_params.max_leverage)

    max_notional = risk_params.max_position_notional_pct * equity
    existing_notional = abs(position.quantity * position.avg_price) if position else 0.0
    remaining_notional = max(max_notional - existing_notional, 0.0)
    max_qty_by_exposure = remaining_notional / intent.reference_price if intent.reference_price > 0 else 0.0
    max_allowed_quantity = min(intent.quantity, max_qty_by_exposure)

    if max_allowed_quantity <= 0:
        return HardLimitResult(
            0.0, max_allowed_leverage, ["Esposizione massima consentita per il simbolo già raggiunta"], True
        )

    round_trip_fee = max_allowed_quantity * intent.reference_price * fee_schedule.taker_fee_pct * 2
    estimated_funding = max_allowed_quantity * intent.reference_price * fee_schedule.default_funding_rate_fallback_pct
    min_required_profit = (round_trip_fee + estimated_funding) * risk_params.min_profit_over_fees_multiple

    if intent.take_profit is not None:
        expected_profit = abs(intent.take_profit - intent.reference_price) * max_allowed_quantity
        if expected_profit < min_required_profit:
            return HardLimitResult(
                0.0,
                max_allowed_leverage,
                [
                    f"Profitto atteso ({expected_profit:.4f}) non supera le fee di round-trip stimate "
                    f"x{risk_params.min_profit_over_fees_multiple} ({min_required_profit:.4f})"
                ],
                True,
                round_trip_fee,
            )

    if max_allowed_quantity < intent.quantity:
        failed_checks.append(
            f"Size ridotta da {intent.quantity:.6f} a {max_allowed_quantity:.6f} per rispettare i limiti di rischio"
        )
    if max_allowed_leverage < intent.leverage:
        failed_checks.append(f"Leva ridotta da {intent.leverage}x a {max_allowed_leverage:.1f}x per rispettare il limite massimo")

    return HardLimitResult(max_allowed_quantity, max_allowed_leverage, failed_checks, False, round_trip_fee)


def finalize_risk_decision(intent: OrderIntent, hard_result: HardLimitResult) -> RiskDecision:
    if hard_result.hard_reject:
        return RiskDecision(
            intent=intent,
            status=RiskDecisionStatus.REJECTED,
            estimated_round_trip_fee=hard_result.estimated_round_trip_fee,
            reasoning="; ".join(hard_result.failed_checks) or "Rifiutata da un limite di rischio hard-coded.",
            failed_checks=hard_result.failed_checks,
        )

    no_change = hard_result.max_allowed_quantity == intent.quantity and hard_result.max_allowed_leverage == intent.leverage
    return RiskDecision(
        intent=intent,
        status=RiskDecisionStatus.APPROVED if no_change else RiskDecisionStatus.MODIFIED,
        adjusted_quantity=hard_result.max_allowed_quantity,
        adjusted_leverage=hard_result.max_allowed_leverage,
        estimated_round_trip_fee=hard_result.estimated_round_trip_fee,
        reasoning="; ".join(hard_result.failed_checks) or "Nessuna modifica necessaria.",
        failed_checks=hard_result.failed_checks,
    )


REVIEW_SYSTEM_PROMPT = """\
Sei il risk manager di una piattaforma di trading crypto sistematico ad alta
frequenza. Ricevi periodicamente: i parametri di rischio correnti, un
riepilogo delle performance recenti (trade eseguiti, esito, fee e funding
pagati, P&L giornaliero), e l'analisi fondamentale/strategica corrente sui
simboli tracciati.

Il tuo compito è aggiornare i parametri di rischio (leva massima, esposizione
massima per simbolo, limite di perdita giornaliera, moltiplicatore minimo di
profitto sulle fee, simboli da mettere in pausa) in base a ciò che osservi:
- se le performance recenti (i trade DAVVERO eseguiti in questa sessione)
  sono deboli, o le fee/funding stanno erodendo il profitto, riduci
  leva/esposizione o alza il moltiplicatore minimo richiesto;
- metti un simbolo in pausa SOLO sulla base di prove concrete già osservate
  in questa sessione (es. più trade in perdita su quel simbolo, fee che
  erodono sistematicamente il P&L): la volatilità o l'incertezza descritta
  nell'analisi fondamentale NON è di per sé un motivo sufficiente per mettere
  in pausa un simbolo che non ha ancora avuto occasione di essere tradato —
  l'obiettivo della piattaforma è testare la strategia con trade reali, non
  evitare a priori simboli volatili;
- puoi anche allentare i parametri rispetto alla chiamata precedente se le
  performance lo giustificano, ma NON puoi mai superare i tetti massimi
  configurati a livello di sistema (verranno comunque forzati nel codice,
  ma la tua proposta deve riflettere questo vincolo).

Rispondi esclusivamente tramite il tool fornito.
"""


class RiskReviewAgent(Agent):
    def __init__(self, claude_client: ClaudeClient, static_risk_limits: dict) -> None:
        super().__init__(claude_client, REVIEW_SYSTEM_PROMPT)
        self._static_risk_limits = static_risk_limits

    def run(
        self,
        current_params: RiskParameters,
        performance_summary: dict,
        context: dict,
    ) -> RiskParameters:
        user_message = (
            "Parametri di rischio correnti:\n"
            f"{json_dumps_compact(current_params.model_dump(mode='json'))}\n\n"
            "Tetti massimi di sistema (non superabili, verranno comunque forzati nel codice):\n"
            f"{json_dumps_compact(self._static_risk_limits)}\n\n"
            "Riepilogo performance recenti:\n"
            f"{json_dumps_compact(performance_summary)}\n\n"
            "Contesto corrente (analisi fondamentale e viste di strategia):\n"
            f"{json_dumps_compact(context)}\n\n"
            "Aggiorna i parametri di rischio."
        )
        reviewed = self._run_structured(user_message, RiskParameters)

        # Il giudizio dell'LLM può solo restringere, mai allargare, i limiti
        # hard-coded di trading.yaml.
        reviewed.max_leverage = min(reviewed.max_leverage, self._static_risk_limits["max_leverage"])
        reviewed.max_position_notional_pct = min(
            reviewed.max_position_notional_pct, self._static_risk_limits["max_exposure_per_symbol_pct"]
        )
        reviewed.max_daily_loss_pct = min(reviewed.max_daily_loss_pct, self._static_risk_limits["max_daily_loss_pct"])
        reviewed.min_profit_over_fees_multiple = max(
            reviewed.min_profit_over_fees_multiple, self._static_risk_limits["min_profit_over_fees_multiple"]
        )
        reviewed.updated_at = datetime.now(timezone.utc)
        return reviewed
