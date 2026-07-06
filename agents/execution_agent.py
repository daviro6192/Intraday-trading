"""Agente 5: piazza effettivamente gli ordini approvati dal risk manager sulla
piattaforma di trading (IBKR reale o PaperBroker in simulazione).

Non chiama Claude: le decisioni sono già state prese dagli agenti precedenti,
qui serve esecuzione affidabile e deterministica, non ulteriore "giudizio".
"""

from __future__ import annotations

import logging

from broker.base import BrokerClient
from common.schemas import ExecutionResult, ExecutionStatus, RiskDecision, RiskDecisionStatus

logger = logging.getLogger(__name__)


class ExecutionAgent:
    def __init__(self, broker: BrokerClient) -> None:
        self._broker = broker

    def run(self, decisions: list[RiskDecision]) -> list[ExecutionResult]:
        results: list[ExecutionResult] = []
        for decision in decisions:
            if decision.status is RiskDecisionStatus.REJECTED:
                logger.info(
                    "Ordine per %s non eseguito (rifiutato dal risk manager): %s",
                    decision.proposal.symbol,
                    decision.reasoning,
                )
                continue

            try:
                result = self._broker.place_bracket_order(decision)
            except Exception as exc:  # noqa: BLE001 - un errore di esecuzione non deve fermare gli altri ordini
                logger.exception("Errore nel piazzare l'ordine per %s", decision.proposal.symbol)
                result = ExecutionResult(
                    broker_order_id="ERROR",
                    symbol=decision.proposal.symbol,
                    side=decision.proposal.side,
                    status=ExecutionStatus.ERROR,
                    error_message=str(exc),
                )
            results.append(result)
        return results
