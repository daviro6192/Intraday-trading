"""Screener di simboli: gira UNA VOLTA all'avvio di ogni sessione (non a ogni
ciclo lento) per scegliere i simboli su cui tradare tra un universo di
candidati più ampio di un set fisso storico — non per forza Bitcoin, Solana
o altri "simboli storici", ma quelli che sembrano offrire le migliori
opportunità di trading intraday in base a volatilità/liquidità del giorno."""

from __future__ import annotations

from agents.base import Agent
from common.claude_client import ClaudeClient, json_dumps_compact
from common.schemas import SymbolCandidate, SymbolSelection

SYSTEM_PROMPT = """\
Sei lo screener di simboli di una piattaforma di trading crypto intraday
sistematico. Ricevi un elenco di coppie perpetual candidate (Binance
USDT-M futures) con variazione di prezzo nelle ultime 24h e volume
scambiato in USDT nelle ultime 24h, e il numero esatto di simboli da
scegliere per la sessione odierna.

Il tuo compito è scegliere ESATTAMENTE il numero di simboli richiesto tra
quelli candidati, su cui tradare per l'intera sessione:
- privilegia simboli con volatilità significativa nelle ultime 24h (alta
  variazione di prezzo in valore assoluto, sia positiva che negativa): più
  movimento significa più opportunità di trading intraday profittevole;
- scarta simboli con volume scambiato molto più basso degli altri
  candidati: bassa liquidità rende difficile eseguire/uscire da posizioni
  senza slippage eccessivo, anche in simulazione;
- non c'è NESSUN vincolo che debba includere Bitcoin o altri simboli
  "storici": scegli solo in base a volatilità e liquidità di oggi;
- se due simboli sono equivalenti per opportunità, preferisci quello con
  volume più alto (esecuzione più affidabile).

Rispondi esclusivamente tramite il tool fornito, con esattamente il numero
di simboli richiesto, scelti tra quelli dell'elenco candidato (usa lo
stesso ticker perpetual esatto ricevuto in input, es. "BTCUSDT").
"""


class SymbolScreenerAgent(Agent):
    def __init__(self, claude_client: ClaudeClient) -> None:
        super().__init__(claude_client, SYSTEM_PROMPT)

    def run(self, candidates: list[SymbolCandidate], count: int) -> SymbolSelection:
        user_message = (
            "Candidati (statistiche 24h da Binance futures):\n"
            f"{json_dumps_compact([c.model_dump(mode='json') for c in candidates])}\n\n"
            f"Scegli esattamente {count} simboli tra questi per la sessione di trading di oggi."
        )
        return self._run_structured(user_message, SymbolSelection)
