"""Costruisce una Pipeline per uno specifico utente della piattaforma web,
leggendo le sue impostazioni dal DB (storage.models.UserSettings) invece
della configurazione globale (config.settings/trading_config) usata da
orchestrator/main.py per la CLI a singolo utente."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session, sessionmaker

from agents.execution_agent import ExecutionAgent
from agents.order_agent import OrderAgent
from agents.risk_agent import RiskAgent
from agents.sentiment_agent import SentimentAgent
from agents.strategy_agent import StrategyAgent
from broker.base import BrokerClient
from broker.paper_broker import PaperBroker
from common.claude_client import ClaudeClient
from orchestrator.pipeline import Pipeline
from storage.models import User, UserSettings


def build_broker_for_user(user_settings: UserSettings) -> BrokerClient:
    if user_settings.trading_mode == "live":
        from broker.ibkr_client import IBKRClient

        return IBKRClient(user_settings.ibkr_host, user_settings.ibkr_port, user_settings.ibkr_client_id)

    broker = PaperBroker()
    # Senza questo, ogni richiesta costruirebbe un simulatore vuoto da
    # $100.000, perdendo la memoria dei trade precedenti tra un ciclo e
    # l'altro (vedi commento su UserSettings.paper_broker_state_json).
    if user_settings.paper_broker_state_json:
        broker.load_state(json.loads(user_settings.paper_broker_state_json))
    return broker


def save_paper_broker_state(broker: BrokerClient, user_settings: UserSettings) -> None:
    """Se il broker è il PaperBroker, persiste cassa/posizioni/P&L correnti
    su UserSettings. Va chiamato dopo ogni operazione che può aver piazzato
    ordini (il chiamante è responsabile di fare il commit della sessione)."""
    if isinstance(broker, PaperBroker):
        user_settings.paper_broker_state_json = json.dumps(broker.export_state())


def build_pipeline_for_user(user: User, session_factory: sessionmaker[Session]) -> Pipeline:
    user_settings = user.settings
    if user_settings is None:
        raise ValueError(f"Utente {user.id} senza impostazioni: mancato seed alla registrazione.")

    claude_client = ClaudeClient(api_key=user_settings.anthropic_api_key, model=user_settings.claude_model)

    broker = build_broker_for_user(user_settings)
    broker.connect()

    trading_config = {
        "news_feeds": json.loads(user_settings.news_feeds_json),
        "watchlists": json.loads(user_settings.watchlists_json),
        "risk_limits": json.loads(user_settings.risk_limits_json),
    }

    return Pipeline(
        sentiment_agent=SentimentAgent(claude_client),
        strategy_agent=StrategyAgent(claude_client),
        order_agent=OrderAgent(claude_client),
        risk_agent=RiskAgent(claude_client, trading_config["risk_limits"]),
        execution_agent=ExecutionAgent(broker),
        broker=broker,
        session_factory=session_factory,
        trading_config=trading_config,
        user_id=user.id,
        claude_client=claude_client,
    )
