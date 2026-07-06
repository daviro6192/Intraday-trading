"""Agente 1: analizza sentiment di mercato, notizie macro/politiche e prepara
il contesto per l'Agente 2 (strategia)."""

from __future__ import annotations

from datetime import datetime, timezone

from agents.base import Agent
from common.claude_client import ClaudeClient
from common.schemas import NewsItem, SentimentReport

SYSTEM_PROMPT = """\
Sei un analista di sentiment di mercato per una piattaforma di trading intraday.
Ricevi un elenco di notizie recenti (macroeconomiche, politiche, geopolitiche,
banche centrali, earnings) e devi produrre un report di sentiment strutturato
per orientare la strategia operativa della giornata.

Nel report:
- valuta il sentiment complessivo del mercato (bullish/bearish/neutral) e uno
  score numerico da -1 a +1;
- identifica gli eventi/catalizzatori chiave della giornata (es. dati macro in
  uscita, riunioni di banche centrali, elezioni, tensioni geopolitiche);
- se le notizie citano esplicitamente ticker o settori, stima un sentiment
  specifico per ciascuno;
- segnala eventuali risk flag (es. alta volatilità attesa, eventi a rischio
  sorpresa) che il trader dovrebbe conoscere.

Sii conciso ma concreto: la tua analisi guida direttamente le decisioni di un
altro agente che genera la strategia del giorno. Rispondi esclusivamente
tramite il tool fornito.
"""


class SentimentAgent(Agent):
    def __init__(self, claude_client: ClaudeClient) -> None:
        super().__init__(claude_client, SYSTEM_PROMPT)

    def run(self, news_items: list[NewsItem]) -> SentimentReport:
        if not news_items:
            return SentimentReport(
                report_date=datetime.now(timezone.utc),
                macro_summary="Nessuna notizia disponibile al momento dell'analisi.",
                overall_sentiment="neutral",
                overall_sentiment_score=0.0,
                risk_flags=["Nessuna fonte di news disponibile: operare con cautela."],
            )

        news_lines = "\n".join(
            f"- [{item.source}] {item.title} ({item.published_at or 'data sconosciuta'}): {item.summary}"
            for item in news_items
        )
        user_message = (
            f"Ecco le notizie raccolte oggi ({len(news_items)} elementi):\n\n{news_lines}\n\n"
            "Analizza queste notizie e produci il report di sentiment di mercato."
        )

        report = self._run_structured(user_message, SentimentReport)
        report.report_date = datetime.now(timezone.utc)
        report.sources = news_items
        return report
