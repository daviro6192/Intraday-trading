"""Agente 3: analizza la strategia del giorno e i dati di mercato correnti e
genera proposte concrete di ordini (buy/sell) da sottoporre al risk manager."""

from __future__ import annotations

from agents.base import Agent
from common.claude_client import ClaudeClient, json_dumps_compact
from common.schemas import DailyStrategy, OrderProposal, OrderProposalBatch

SYSTEM_PROMPT = """\
Sei un trader esecutivo intraday. Ricevi la strategia operativa del giorno
(prodotta da un altro agente) e uno snapshot dei dati tecnici correnti
(prezzo, medie mobili, RSI, MACD, ATR) per gli strumenti in watchlist.

Per ciascuno strumento della strategia per cui individui un setup concreto e
attuabile ORA, genera una proposta di ordine con:
- lato (buy/sell) coerente con il bias e i dati tecnici;
- prezzo di entrata realistico rispetto al prezzo corrente;
- stop-loss e take-profit espliciti (obbligatori, nessuna proposta senza
  entrambi);
- una size proposta ragionevole in unità dello strumento (un altro agente,
  il risk manager, la validerà e correggerà secondo i limiti di rischio del
  conto: proponi una size "di buon senso", non preoccuparti di calcolare tu
  i limiti esatti);
- un livello di confidenza (0-1) e una motivazione che colleghi la proposta
  alla strategia e ai dati tecnici osservati.

Se per uno strumento non c'è un setup valido in questo momento (es. prezzo
non ancora al livello desiderato, indicatori contrastanti), non generare una
proposta per quello strumento: è del tutto legittimo restituire una lista
vuota o parziale. Rispondi esclusivamente tramite il tool fornito.
"""


class OrderAgent(Agent):
    def __init__(self, claude_client: ClaudeClient) -> None:
        super().__init__(claude_client, SYSTEM_PROMPT)

    def run(self, daily_strategy: DailyStrategy, market_snapshots: dict) -> list[OrderProposal]:
        if not daily_strategy.watchlist:
            return []

        user_message = (
            "Strategia operativa del giorno:\n"
            f"{daily_strategy.model_dump_json(indent=2)}\n\n"
            "Snapshot dati tecnici correnti per gli strumenti in watchlist:\n"
            f"{json_dumps_compact(market_snapshots)}\n\n"
            "Genera le proposte di ordine attuabili ora, se ce ne sono."
        )
        batch = self._run_structured(user_message, OrderProposalBatch)
        return batch.proposals
