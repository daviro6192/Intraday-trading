"""Modelli Pydantic per le richieste/risposte dell'API web (distinti dai
contratti tra agenti in common/schemas.py, che restano invariati)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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


class SettingsUpdateRequest(BaseModel):
    anthropic_api_key: str | None = None
    claude_model: str | None = None
    trading_mode: str | None = None
    ibkr_host: str | None = None
    ibkr_port: int | None = None
    ibkr_client_id: int | None = None


class PipelineRunSummary(BaseModel):
    id: int
    started_at: datetime
    has_sentiment_report: bool
    has_daily_strategy: bool
    order_proposals_count: int
    risk_decisions_count: int
    execution_results_count: int


class PipelineRunDetail(BaseModel):
    id: int
    started_at: datetime
    sentiment_report: dict[str, Any] | None
    daily_strategy: dict[str, Any] | None
    order_proposals: list[dict[str, Any]]
    risk_decisions: list[dict[str, Any]]
    execution_results: list[dict[str, Any]]
