"""Impostazioni personali dell'utente: API key Anthropic, connessione IBKR,
watchlist e limiti di rischio."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import SettingsResponse, SettingsUpdateRequest, UsageStats
from api.security import get_current_user
from common.pricing import estimate_cost_usd
from storage.models import User

router = APIRouter(prefix="/api", tags=["settings"])


def _to_response(user: User) -> SettingsResponse:
    s = user.settings
    return SettingsResponse(
        has_api_key=bool(s.anthropic_api_key),
        claude_model=s.claude_model,
        trading_mode=s.trading_mode,
        ibkr_host=s.ibkr_host,
        ibkr_port=s.ibkr_port,
        ibkr_client_id=s.ibkr_client_id,
    )


@router.get("/settings", response_model=SettingsResponse)
def get_settings(user: User = Depends(get_current_user)) -> SettingsResponse:
    return _to_response(user)


@router.put("/settings", response_model=SettingsResponse)
def update_settings(
    body: SettingsUpdateRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> SettingsResponse:
    s = user.settings
    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in updates.items():
        setattr(s, field, value)
    db.commit()
    db.refresh(user)
    return _to_response(user)


@router.get("/watchlist")
def get_watchlist(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return json.loads(user.settings.watchlists_json)


@router.put("/watchlist")
def update_watchlist(
    body: dict[str, Any], user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    user.settings.watchlists_json = json.dumps(body)
    db.commit()
    return body


@router.get("/usage", response_model=UsageStats)
def get_usage(user: User = Depends(get_current_user)) -> UsageStats:
    """Spesa Claude stimata dai token consumati da questa piattaforma per
    l'utente corrente. Non è il saldo prepagato reale (Anthropic non lo
    espone via API con una chiave normale): controllalo su
    console.anthropic.com/settings/billing."""
    s = user.settings
    cost = estimate_cost_usd(
        s.claude_model,
        s.total_input_tokens,
        s.total_output_tokens,
        s.total_cache_creation_tokens,
        s.total_cache_read_tokens,
    )
    return UsageStats(
        claude_model=s.claude_model,
        total_input_tokens=s.total_input_tokens,
        total_output_tokens=s.total_output_tokens,
        total_cache_creation_tokens=s.total_cache_creation_tokens,
        total_cache_read_tokens=s.total_cache_read_tokens,
        estimated_cost_usd=cost,
    )


@router.get("/risk-limits")
def get_risk_limits(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return json.loads(user.settings.risk_limits_json)


@router.put("/risk-limits")
def update_risk_limits(
    body: dict[str, Any], user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    user.settings.risk_limits_json = json.dumps(body)
    db.commit()
    return body
