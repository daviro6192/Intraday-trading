"""Agente 2: mantiene una vista di strategia per simbolo (long/short/flat),
aggiornata di continuo in base all'ultima analisi fondamentale e alla propria
vista precedente (continuità: non ribaltare bias senza un cambiamento
materiale nei fondamentali). Alimenta l'Agente 3 (ordini)."""

from __future__ import annotations

from agents.base import Agent
from common.claude_client import ClaudeClient, json_dumps_compact
from common.schemas import FundamentalAnalysis, StrategyView, StrategyViewBatch

SYSTEM_PROMPT = """\
Sei uno stratega di trading sistematico crypto. Ricevi l'analisi
fondamentale più recente per un insieme fisso di simboli (prodotta da un
altro agente) e le tue stesse viste di strategia della chiamata precedente
(se esistono).

Il tuo compito è produrre/aggiornare, per ciascun simbolo, una vista di
strategia operativa:
- direction: long, short o flat. Deve riflettere il bias strutturale
  dell'analisi fondamentale più recente;
- conviction: quanto sei convinto di questa direzione (0-1);
- invalidation_condition: una condizione esplicita e verificabile (es. "lo
  score fondamentale scende sotto -0.2" oppure "il prezzo perde il livello
  di supporto strutturale") che, se si verifica, invaliderebbe questa vista
  e dovrebbe far chiudere eventuali posizioni aperte in questa direzione;
- rationale: motivazione concisa.

Principio di continuità: non ribaltare la direzione di un simbolo rispetto
alla tua vista precedente a meno che i fondamentali non siano cambiati in
modo materiale. Piccole oscillazioni dello score non giustificano un
cambio di direzione: preferisci stabilità a meno che il cambiamento sia
chiaro. Se non hai una vista precedente per un simbolo, costruiscine una
da zero in base ai soli fondamentali correnti.

Rispondi esclusivamente tramite il tool fornito.
"""


class StrategyAgent(Agent):
    def __init__(self, claude_client: ClaudeClient) -> None:
        super().__init__(claude_client, SYSTEM_PROMPT)

    def run(
        self, fundamentals: list[FundamentalAnalysis], previous_views: list[StrategyView]
    ) -> list[StrategyView]:
        user_message = (
            "Analisi fondamentale più recente per i simboli tracciati:\n"
            f"{json_dumps_compact([f.model_dump(mode='json') for f in fundamentals])}\n\n"
            "Tue viste di strategia precedenti (lista vuota se è la prima esecuzione):\n"
            f"{json_dumps_compact([v.model_dump(mode='json') for v in previous_views])}\n\n"
            "Produci/aggiorna la vista di strategia per ciascun simbolo presente nell'analisi fondamentale."
        )
        batch = self._run_structured(user_message, StrategyViewBatch)
        return batch.views
