"""Script di prova manuale (non pytest): avvia una sessione di trading reale
in paper mode contro un backend già in esecuzione (`uvicorn api.main:app` o
`./start.sh`/`./start.ps1`), usando Claude e Binance/CoinGecko veri — utile
per vedere il sistema girare prima che il frontend sia aggiornato al nuovo
modello a sessione continua.

Uso:
    python scripts/manual_session_smoke_test.py

Legge ANTHROPIC_API_KEY da .env (stesso file usato dal resto del progetto).
Premi Ctrl+C per fermare la sessione in modo pulito.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _print_status(status: dict) -> None:
    pnl = status.get("session_pnl")
    pnl_str = f"{pnl:+.4f}" if pnl is not None else "n/d"
    equity = status.get("current_equity")
    equity_str = f"{equity:.2f}" if equity is not None else "n/d"
    print(
        f"[{status['status']}] trade={status['trades_executed']:>4} "
        f"equity={equity_str:>12} P&L_sessione={pnl_str:>12} "
        f"fee={status['fees_paid_today']:.4f} funding={status['funding_paid_today']:.4f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000", help="URL del backend (default: %(default)s)")
    parser.add_argument("--username", default="smoketest", help="Utente di prova da creare/riusare")
    parser.add_argument("--password", default="smoketest123", help="Password dell'utente di prova")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Secondi tra una lettura di stato e l'altra")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ANTHROPIC_API_KEY non trovata in .env: impostala prima di continuare.", file=sys.stderr)
        raise SystemExit(1)

    session = requests.Session()
    base = args.base_url.rstrip("/")

    register_response = session.post(
        f"{base}/api/auth/register", json={"username": args.username, "password": args.password}
    )
    if register_response.status_code == 201:
        print(f"Utente '{args.username}' creato.")
    elif register_response.status_code == 409:
        login_response = session.post(
            f"{base}/api/auth/login", json={"username": args.username, "password": args.password}
        )
        login_response.raise_for_status()
        print(f"Utente '{args.username}' già esistente, login effettuato.")
    else:
        register_response.raise_for_status()

    settings_response = session.put(f"{base}/api/settings", json={"anthropic_api_key": api_key})
    settings_response.raise_for_status()
    print("API key Anthropic impostata per questo utente.")

    symbols = session.get(f"{base}/api/symbols").json()
    print("Simboli tracciati:", {cfg["symbol"]: cfg["binance_perp"] for cfg in symbols.values()})

    start_response = session.post(f"{base}/api/session/start")
    if start_response.status_code == 409:
        print("C'è già una sessione in corso per questo utente: la riprendo per monitorarla.")
    else:
        start_response.raise_for_status()
        print("Sessione avviata.")

    try:
        while True:
            status_response = session.get(f"{base}/api/session/status")
            status_response.raise_for_status()
            _print_status(status_response.json())
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\nInterrotto: fermo la sessione...")
        stop_response = session.post(f"{base}/api/session/stop")
        stop_response.raise_for_status()
        _print_status(stop_response.json())
        print("Sessione fermata in modo pulito.")


if __name__ == "__main__":
    main()
