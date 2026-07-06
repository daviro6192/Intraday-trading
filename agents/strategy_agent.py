"""Agente 2: usa il report di sentiment per generare la strategia operativa
del giorno e la propone all'Agente 3 (proposte di ordine)."""

from __future__ import annotations

from datetime import datetime, timezone

from agents.base import Agent
from common.claude_client import ClaudeClient, json_dumps_compact
from common.schemas import DailyStrategy, SentimentReport

SYSTEM_PROMPT = """\
Sei uno stratega di trading intraday. Ricevi il report di sentiment di mercato
prodotto da un altro agente e l'elenco degli strumenti disponibili (azioni
USA, azioni EU/IT, forex, crypto), organizzati per mercato.

Il tuo compito è produrre la strategia operativa per la giornata odierna:
- stabilisci la propensione al rischio complessiva della giornata (bassa,
  media, alta) in base al contesto di sentiment ed eventi rilevanti;
- seleziona un sottoinsieme ragionevole di strumenti dalla watchlist fornita
  (non è necessario includerli tutti) assegnando a ciascuno un bias
  (bullish/bearish/neutral), una motivazione concreta legata al sentiment o ai
  dati disponibili, e un setup/timeframe suggerito per l'intraday;
- se il contesto è troppo incerto o rischioso (es. eventi macro imminenti ad
  alto impatto), è legittimo proporre una propensione al rischio bassa o una
  watchlist ridotta/vuota.

La strategia deve essere operativa e specifica, non generica: verrà usata da
un altro agente per generare proposte di ordine concrete. Rispondi
esclusivamente tramite il tool fornito.
"""


class StrategyAgent(Agent):
    def __init__(self, claude_client: ClaudeClient) -> None:
        super().__init__(claude_client, SYSTEM_PROMPT)

    def run(self, sentiment_report: SentimentReport, watchlists: dict) -> DailyStrategy:
        user_message = (
            "Report di sentiment di mercato:\n"
            f"{sentiment_report.model_dump_json(indent=2)}\n\n"
            "Watchlist di strumenti disponibili per mercato:\n"
            f"{json_dumps_compact(watchlists)}\n\n"
            "Genera la strategia operativa per la giornata odierna."
        )
        strategy = self._run_structured(user_message, DailyStrategy)
        strategy.strategy_date = datetime.now(timezone.utc)
        strategy.sentiment_summary = strategy.sentiment_summary or sentiment_report.macro_summary
        return strategy
