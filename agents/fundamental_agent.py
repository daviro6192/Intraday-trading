"""Agente 1: analisi ESCLUSIVAMENTE fondamentale dei simboli crypto tracciati
(niente sentiment/news/rumor). Alimenta l'Agente 2 (strategia)."""

from __future__ import annotations

from datetime import datetime, timezone

from agents.base import Agent
from common.claude_client import ClaudeClient, json_dumps_compact
from common.schemas import FundamentalAnalysis, FundamentalAnalysisBatch

SYSTEM_PROMPT = """\
Sei un analista fondamentale crypto per una piattaforma di trading
sistematico. Ricevi metriche di mercato pubbliche (market cap e relativo
rank, supply circolante/totale/massima, distanza da ATH/ATL, variazioni di
prezzo su 24h/7d/30d/1y, rapporto volume/market cap, dominance) per un
insieme fisso di simboli crypto.

Il tuo compito è ESCLUSIVAMENTE analisi strutturale/fondamentale:
- non ragionare su notizie, eventi, sentiment di mercato, social media o
  rumor: se non è deducibile dalle metriche fornite, non è pertinente;
- valuta la solidità strutturale di ciascun simbolo: dinamica della supply
  (es. inflazione/scarsità), posizione nel ciclo rispetto ad ATH/ATL,
  liquidità relativa (volume/market cap), tendenza di lungo periodo
  (30d/1y) rispetto al breve (24h/7d);
- produci per ciascun simbolo un bias strutturale (bullish/bearish/neutral),
  uno score numerico da -1 a +1, una motivazione concisa basata solo sui
  dati, ed eventuali flag rilevanti (es. "vicino all'ATH", "supply
  inflation elevata", "bassa liquidità relativa al market cap", "dati
  fondamentali non disponibili" se mancano dati per quel simbolo).

Rispondi esclusivamente tramite il tool fornito.
"""


class FundamentalAgent(Agent):
    def __init__(self, claude_client: ClaudeClient) -> None:
        super().__init__(claude_client, SYSTEM_PROMPT)

    def run(self, fundamentals_by_symbol: dict[str, dict | None]) -> list[FundamentalAnalysis]:
        user_message = (
            "Metriche fondamentali correnti per i simboli tracciati "
            "(un valore `null`/assente indica dati non disponibili per quel simbolo):\n"
            f"{json_dumps_compact(fundamentals_by_symbol)}\n\n"
            "Produci l'analisi fondamentale per ciascun simbolo elencato."
        )
        batch = self._run_structured(user_message, FundamentalAnalysisBatch)

        now = datetime.now(timezone.utc)
        for analysis in batch.analyses:
            analysis.as_of = now
        return batch.analyses
