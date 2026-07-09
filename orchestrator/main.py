"""Entrypoint CLI: `python -m orchestrator.main --setup`.

Il flusso di trading continuo (ciclo lento fondamentale/strategia + ciclo
veloce ordini/rischio) non ha più un equivalente CLI a singolo
utente/configurazione globale dopo la riscrittura per il trading continuo
crypto: usa la piattaforma web (`POST /api/session/start` / `/stop`, vedi
`api/routers/session.py` e `orchestrator/session_manager.py`), che gestisce
sessioni multi-utente con stato persistito su DB. Questo entrypoint resta
solo per il wizard di setup della API key, riutilizzabile indipendentemente
da come poi si avvia la piattaforma.
"""

from __future__ import annotations

import argparse

from orchestrator.setup_wizard import run_setup_wizard


def main() -> None:
    parser = argparse.ArgumentParser(description="Piattaforma di trading intraday multi-agente")
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Wizard interattivo per configurare la Anthropic API key in .env",
    )
    args = parser.parse_args()

    if args.setup:
        run_setup_wizard()
        return

    parser.error(
        "Nessun'altra modalità CLI disponibile: avvia la piattaforma web (./start.sh o ./start.ps1) "
        "e usa /api/session/start per avviare una sessione di trading continua."
    )


if __name__ == "__main__":
    main()
