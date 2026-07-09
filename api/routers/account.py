"""Stato del conto (equity, posizioni, P&L) dal broker configurato
dall'utente corrente. Non richiede una API key Anthropic configurata,
a differenza degli endpoint della pipeline."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from api.security import get_current_user
from orchestrator.factory import build_broker_for_user, build_fee_schedule_from_config
from storage.models import User

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/state")
def get_account_state(user: User = Depends(get_current_user)) -> dict:
    try:
        broker = build_broker_for_user(user.settings, build_fee_schedule_from_config())
        broker.connect()
        state = broker.get_account_state()
    except Exception as exc:  # noqa: BLE001 - errore di connessione al broker va mostrato all'utente, non 500 generico
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Impossibile leggere lo stato del conto: {exc}"
        ) from exc

    return state.model_dump(mode="json")
