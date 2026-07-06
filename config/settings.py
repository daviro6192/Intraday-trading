"""Configurazione applicativa letta da variabili d'ambiente (.env) e trading.yaml."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).resolve().parent
TRADING_YAML_PATH = CONFIG_DIR / "trading.yaml"


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-sonnet-5", alias="CLAUDE_MODEL")

    trading_mode: TradingMode = Field(default=TradingMode.PAPER, alias="TRADING_MODE")

    ibkr_host: str = Field(default="127.0.0.1", alias="IBKR_HOST")
    ibkr_port: int = Field(default=7497, alias="IBKR_PORT")
    ibkr_client_id: int = Field(default=1, alias="IBKR_CLIENT_ID")

    database_url: str = Field(default="sqlite:///./trading.db", alias="DATABASE_URL")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    secret_key: str = Field(default="dev-only-insecure-secret", alias="SECRET_KEY")

    @property
    def is_live(self) -> bool:
        return self.trading_mode is TradingMode.LIVE


def load_trading_config(path: Path = TRADING_YAML_PATH) -> dict[str, Any]:
    """Carica watchlist, limiti di rischio e orari di sessione da trading.yaml."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


settings = Settings()
trading_config = load_trading_config()
