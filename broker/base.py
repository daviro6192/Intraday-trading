"""Interfaccia comune per l'esecuzione ordini, implementata sia dal PaperBroker
(simulatore, default per sviluppo/test) sia da IBKRClient (Interactive Brokers reale)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from common.schemas import AccountState, ExecutionResult, RiskDecision


@runtime_checkable
class BrokerClient(Protocol):
    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def get_account_state(self) -> AccountState: ...

    def place_bracket_order(self, decision: RiskDecision) -> ExecutionResult:
        """Piazza un ordine bracket (entry + stop-loss + take-profit obbligatori)
        a partire da una RiskDecision già approvata dall'Agente 4."""
        ...

    def cancel_order(self, broker_order_id: str) -> None: ...
