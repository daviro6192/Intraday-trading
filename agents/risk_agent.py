"""Agente 4: risk manager. Valuta le proposte di ordine dell'Agente 3 rispetto
allo stato del conto e ai limiti di rischio configurati, con l'obiettivo di
massimizzare il profitto atteso senza esporre il conto a perdite eccessive
sul singolo trade o sulla giornata.

Design di sicurezza: i limiti numerici in trading.yaml (risk_limits) sono
applicati in modo deterministico in `evaluate_hard_limits`, PRIMA e DOPO la
chiamata a Claude. Il giudizio qualitativo dell'LLM può solo essere più
prudente di quanto consentito dai limiti hard-coded (può rifiutare o ridurre
ulteriormente una proposta), mai più permissivo: la quantità finale è sempre
il minimo tra quanto propone l'LLM e il tetto calcolato deterministicamente,
e un rifiuto hard-coded non può mai essere trasformato in approvazione.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from agents.base import Agent
from common.claude_client import ClaudeClient, json_dumps_compact
from common.schemas import AccountState, OrderProposal, RiskDecision, RiskDecisionStatus

SYSTEM_PROMPT = """\
Sei il risk manager di una piattaforma di trading intraday. Ricevi una lista
di proposte di ordine già passate al vaglio di controlli di rischio
deterministici (che hanno eventualmente già ridotto la size massima
consentita o segnalato un rifiuto obbligatorio), lo stato corrente del conto
e i limiti di rischio configurati.

Per ciascuna proposta, fornisci una valutazione qualitativa aggiuntiva:
- se il controllo deterministico segnala un rifiuto obbligatorio
  (hard_reject=true), la tua valutazione deve essere "rejected": non hai
  la possibilità di approvarla;
- altrimenti valuta se il setup ha senso nel contesto della strategia e dei
  dati disponibili: puoi approvarla, ridurla ulteriormente rispetto al tetto
  massimo già calcolato (mai aumentarla), o rifiutarla se non ti convince
  qualitativamente (es. rapporto reward/risk risicato, setup incoerente con
  il resto del portafoglio, eccessiva concentrazione di rischio direzionale);
- la size che suggerisci non può mai superare max_allowed_quantity indicato
  per quella proposta: verrà comunque forzata nel codice, ma deve riflettere
  il tuo giudizio.

Il tuo obiettivo è massimizzare il profitto atteso del conto riducendo il
rischio di perdite eccessive sul singolo trade. Rispondi esclusivamente
tramite il tool fornito.
"""


class RiskAssessment(BaseModel):
    symbol: str
    status: RiskDecisionStatus
    suggested_quantity: float | None = Field(default=None, ge=0.0)
    suggested_stop_loss: float | None = None
    suggested_take_profit: float | None = None
    reasoning: str


class RiskAssessmentBatch(BaseModel):
    assessments: list[RiskAssessment] = Field(default_factory=list)


@dataclass
class HardLimitResult:
    max_allowed_quantity: float
    failed_checks: list[str] = field(default_factory=list)
    hard_reject: bool = False


def evaluate_hard_limits(proposal: OrderProposal, account: AccountState, risk_limits: dict) -> HardLimitResult:
    """Applica i limiti di rischio deterministici da trading.yaml a una singola
    proposta. Funzione pura, senza chiamate a Claude: è la barriera di
    sicurezza non aggirabile dal giudizio dell'LLM."""
    equity = account.equity
    failed_checks: list[str] = []

    if equity <= 0:
        return HardLimitResult(0.0, ["Equity del conto non positiva"], True)

    max_daily_loss = risk_limits["max_daily_loss_pct"] * equity
    if account.total_pnl_today <= -max_daily_loss:
        return HardLimitResult(
            0.0,
            [f"Limite di perdita giornaliera raggiunto (P&L oggi: {account.total_pnl_today:.2f})"],
            True,
        )

    min_rr = risk_limits["min_reward_risk_ratio"]
    if proposal.reward_risk_ratio < min_rr:
        return HardLimitResult(
            0.0,
            [f"Rapporto reward/risk {proposal.reward_risk_ratio:.2f} inferiore al minimo richiesto {min_rr}"],
            True,
        )

    held_symbols = {p.symbol for p in account.open_positions}
    if proposal.symbol not in held_symbols and len(held_symbols) >= risk_limits["max_concurrent_positions"]:
        return HardLimitResult(0.0, ["Numero massimo di posizioni concorrenti raggiunto"], True)

    risk_per_unit = proposal.risk_per_unit
    if risk_per_unit <= 0:
        return HardLimitResult(0.0, ["Stop-loss non valido: rischio per unità nullo"], True)

    max_qty_by_risk = (risk_limits["max_risk_per_trade_pct"] * equity) / risk_per_unit

    existing_position = next((p for p in account.open_positions if p.symbol == proposal.symbol), None)
    existing_notional = abs(existing_position.market_value) if existing_position else 0.0
    max_notional_by_exposure = risk_limits["max_exposure_per_symbol_pct"] * equity
    remaining_notional = max(max_notional_by_exposure - existing_notional, 0.0)
    max_qty_by_exposure = remaining_notional / proposal.entry_price if proposal.entry_price > 0 else 0.0

    max_allowed_quantity = min(proposal.proposed_quantity, max_qty_by_risk, max_qty_by_exposure)

    if max_allowed_quantity <= 0:
        return HardLimitResult(0.0, ["Esposizione massima consentita per lo strumento già raggiunta"], True)

    if max_allowed_quantity < proposal.proposed_quantity:
        failed_checks.append(
            f"Size ridotta da {proposal.proposed_quantity} a {max_allowed_quantity:.4f} "
            "per rispettare i limiti di rischio configurati"
        )

    return HardLimitResult(max_allowed_quantity, failed_checks, False)


class RiskAgent(Agent):
    def __init__(self, claude_client: ClaudeClient, risk_limits: dict) -> None:
        super().__init__(claude_client, SYSTEM_PROMPT)
        self._risk_limits = risk_limits

    def run(self, proposals: list[OrderProposal], account_state: AccountState) -> list[RiskDecision]:
        if not proposals:
            return []

        hard_results = [evaluate_hard_limits(p, account_state, self._risk_limits) for p in proposals]

        screening_payload = [
            {
                "symbol": proposal.symbol,
                "proposal": proposal.model_dump(mode="json"),
                "max_allowed_quantity": hard_result.max_allowed_quantity,
                "hard_reject": hard_result.hard_reject,
                "deterministic_failed_checks": hard_result.failed_checks,
            }
            for proposal, hard_result in zip(proposals, hard_results)
        ]

        user_message = (
            "Stato del conto:\n"
            f"{account_state.model_dump_json(indent=2)}\n\n"
            "Limiti di rischio configurati:\n"
            f"{json_dumps_compact(self._risk_limits)}\n\n"
            "Proposte di ordine con esito dei controlli deterministici già applicati:\n"
            f"{json_dumps_compact(screening_payload)}\n\n"
            "Fornisci la tua valutazione per ciascuna proposta (una per symbol)."
        )

        batch = self._run_structured(user_message, RiskAssessmentBatch)
        assessments_by_symbol = {a.symbol: a for a in batch.assessments}

        decisions: list[RiskDecision] = []
        for proposal, hard_result in zip(proposals, hard_results):
            assessment = assessments_by_symbol.get(proposal.symbol)
            decisions.append(self._finalize_decision(proposal, hard_result, assessment))
        return decisions

    @staticmethod
    def _finalize_decision(
        proposal: OrderProposal, hard_result: HardLimitResult, assessment: RiskAssessment | None
    ) -> RiskDecision:
        failed_checks = list(hard_result.failed_checks)

        if hard_result.hard_reject:
            return RiskDecision(
                proposal=proposal,
                status=RiskDecisionStatus.REJECTED,
                reasoning=(assessment.reasoning if assessment else "Rifiutata da un limite di rischio hard-coded."),
                failed_checks=failed_checks,
            )

        if assessment is None or assessment.status is RiskDecisionStatus.REJECTED:
            return RiskDecision(
                proposal=proposal,
                status=RiskDecisionStatus.REJECTED,
                reasoning=(assessment.reasoning if assessment else "Nessuna valutazione ricevuta dal risk manager."),
                failed_checks=failed_checks,
            )

        # La quantità finale non può mai superare il tetto deterministico,
        # indipendentemente da cosa suggerisce l'LLM.
        requested_quantity = assessment.suggested_quantity
        if requested_quantity is None or requested_quantity > hard_result.max_allowed_quantity:
            final_quantity = hard_result.max_allowed_quantity
        else:
            final_quantity = requested_quantity

        status = (
            RiskDecisionStatus.APPROVED
            if final_quantity == proposal.proposed_quantity
            else RiskDecisionStatus.MODIFIED
        )

        return RiskDecision(
            proposal=proposal,
            status=status,
            adjusted_quantity=final_quantity,
            adjusted_stop_loss=assessment.suggested_stop_loss,
            adjusted_take_profit=assessment.suggested_take_profit,
            reasoning=assessment.reasoning,
            failed_checks=failed_checks,
        )
