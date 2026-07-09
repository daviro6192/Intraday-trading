"""Modelli Pydantic per le richieste/risposte dell'API web (distinti dai
contratti tra agenti in common/schemas.py, che restano invariati)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    created_at: datetime


class SettingsResponse(BaseModel):
    has_api_key: bool
    claude_model: str
    trading_mode: str
    ibkr_host: str
    ibkr_port: int
    ibkr_client_id: int


class UsageStats(BaseModel):
    claude_model: str
    total_input_tokens: int
    total_output_tokens: int
    total_cache_creation_tokens: int
    total_cache_read_tokens: int
    estimated_cost_usd: float


class SettingsUpdateRequest(BaseModel):
    anthropic_api_key: str | None = None
    claude_model: str | None = None
    trading_mode: str | None = None
    ibkr_host: str | None = None
    ibkr_port: int | None = None
    ibkr_client_id: int | None = None


class SessionStatusResponse(BaseModel):
    session_id: int | None
    status: str  # not_started | running | stopped | interrupted | error
    started_at: datetime | None
    stopped_at: datetime | None
    trades_executed: int
    starting_equity: float | None
    current_equity: float | None
    session_pnl: float | None
    fees_paid_today: float
    funding_paid_today: float
