"""Wizard di setup interattivo: chiede la Anthropic API key dell'utente,
la verifica con una chiamata minima e la salva in .env.

Nota: l'unico modo supportato per autenticare questa applicazione con Claude
è una API key generata su console.anthropic.com (fatturata a consumo sul
proprio account Anthropic) — non esiste un login OAuth pubblico riutilizzabile
da app di terze parti come questa.
"""

from __future__ import annotations

import getpass
from pathlib import Path

import anthropic

ENV_PATH = Path(".env")
ENV_EXAMPLE_PATH = Path(".env.example")

_VERIFICATION_MODEL = "claude-haiku-4-5-20251001"
_MAX_ATTEMPTS = 3


def _verify_api_key(api_key: str) -> tuple[bool, str]:
    try:
        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model=_VERIFICATION_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        return True, ""
    except anthropic.AuthenticationError:
        return False, "API key non valida o non autorizzata."
    except Exception as exc:  # noqa: BLE001 - qualunque errore di rete/API va mostrato all'utente, non propagato
        return False, f"Impossibile verificare la chiave: {exc}"


def _write_env_var(key: str, value: str) -> None:
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    elif ENV_EXAMPLE_PATH.exists():
        lines = ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_setup_wizard() -> None:
    print("Setup della piattaforma di trading intraday.")
    print("Per usare gli agenti serve una API key Anthropic del tuo account.")
    print("Puoi crearne una su https://console.anthropic.com/settings/keys")
    print()

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        api_key = getpass.getpass("Anthropic API key (input nascosto): ").strip()
        if not api_key:
            print("Nessuna chiave inserita, riprova.\n")
            continue

        print("Verifica della chiave in corso...")
        is_valid, error = _verify_api_key(api_key)
        if is_valid:
            _write_env_var("ANTHROPIC_API_KEY", api_key)
            print(f"\nChiave valida, salvata in {ENV_PATH}.")
            print("Setup completato. Ora puoi eseguire: python -m orchestrator.main --once --mode paper")
            return

        print(f"Errore: {error}")
        if attempt < _MAX_ATTEMPTS:
            print("Riprova.\n")

    print("\nTroppi tentativi falliti. Riesegui `python -m orchestrator.main --setup` per riprovare.")
