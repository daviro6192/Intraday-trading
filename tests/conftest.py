"""Fixture condivise: un Claude client finto (nessuna chiamata di rete nei test)
e utility per costruire rapidamente proposte/stati di conto di esempio."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import BaseModel

from common.schemas import AccountState, Market, OrderProposal, OrderSide, OrderType


class FakeClaudeClient:
    """Sostituisce common.claude_client.ClaudeClient nei test: nessuna chiamata
    di rete, risposte pre-programmate per nome del modello Pydantic atteso."""

    def __init__(self, responses: dict[str, Callable[[str], BaseModel] | BaseModel]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str]] = []
        # Stessi contatori di common.claude_client.ClaudeClient, usati da
        # api/routers/pipeline.py per accumulare la spesa stimata dopo un ciclo.
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_creation_tokens = 0
        self.total_cache_read_tokens = 0

    def run_structured(
        self,
        system_prompt: str,
        user_message: str,
        response_model: type[BaseModel],
        **kwargs: Any,
    ) -> BaseModel:
        self.calls.append((response_model.__name__, user_message))
        # Incremento fisso e deterministico, per poter verificare nei test che
        # l'accumulo lato API rifletta esattamente il numero di chiamate fatte.
        self.total_input_tokens += 100
        self.total_output_tokens += 50
        entry = self._responses[response_model.__name__]
        if isinstance(entry, BaseModel):
            return entry
        return entry(user_message)


@pytest.fixture
def fake_claude_client_factory():
    def _factory(responses: dict[str, Callable[[str], BaseModel] | BaseModel]) -> FakeClaudeClient:
        return FakeClaudeClient(responses)

    return _factory


def make_order_proposal(
    symbol: str = "AAPL",
    market: Market = Market.US_EQUITY,
    side: OrderSide = OrderSide.BUY,
    entry_price: float = 100.0,
    stop_loss: float = 98.0,
    take_profit: float = 106.0,
    proposed_quantity: float = 10.0,
    confidence: float = 0.7,
) -> OrderProposal:
    return OrderProposal(
        symbol=symbol,
        market=market,
        side=side,
        order_type=OrderType.LIMIT,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        proposed_quantity=proposed_quantity,
        confidence=confidence,
        rationale="setup di test",
    )


def make_account_state(
    equity: float = 100_000.0,
    cash: float | None = None,
    open_positions: list | None = None,
    realized_pnl_today: float = 0.0,
    unrealized_pnl_today: float = 0.0,
) -> AccountState:
    return AccountState(
        equity=equity,
        cash=cash if cash is not None else equity,
        open_positions=open_positions or [],
        realized_pnl_today=realized_pnl_today,
        unrealized_pnl_today=unrealized_pnl_today,
        as_of=datetime.now(timezone.utc),
    )
