"""Interfaccia comune per l'esecuzione ordini. Implementata da PaperBroker
(simulatore futures perpetual, default per sviluppo/test).

`broker.ibkr_client.IBKRClient` (esecuzione reale su Interactive Brokers) NON
implementa più questa interfaccia: è stato scritto per bracket order azionari
con stop/take-profit obbligatori, un modello diverso da entrate/uscite dirette
long/short a mercato. Resta nel repository come riferimento/legacy, non
collegato alla pipeline finché non si deciderà di tornare all'esecuzione reale
(per ora si resta in paper trading crypto, vedi README)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from common.schemas import AccountState, ExecutionResult, OrderSide


@runtime_checkable
class BrokerClient(Protocol):
    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def get_account_state(self) -> AccountState: ...

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        leverage: float,
        reference_price: float,
        reduce_only: bool = False,
    ) -> ExecutionResult:
        """Apre o aumenta una posizione (reduce_only=False) o ne riduce/chiude
        una esistente (reduce_only=True), a mercato. `reference_price` è il
        prezzo atteso dal chiamante, usato come fallback se il broker non
        riesce a recuperare un prezzo di mercato aggiornato."""
        ...

    def close_position(self, symbol: str) -> ExecutionResult | None:
        """Chiude interamente la posizione su symbol al prezzo di mercato
        corrente. Ritorna None se non c'è alcuna posizione aperta su symbol."""
        ...
