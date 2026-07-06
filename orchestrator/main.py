"""Entrypoint CLI: `python -m orchestrator.main [--mode paper|live] [--once]`."""

from __future__ import annotations

import argparse
import logging

from agents.execution_agent import ExecutionAgent
from agents.order_agent import OrderAgent
from agents.risk_agent import RiskAgent
from agents.sentiment_agent import SentimentAgent
from agents.strategy_agent import StrategyAgent
from broker.base import BrokerClient
from broker.paper_broker import PaperBroker
from common.claude_client import ClaudeClient
from common.logging_config import configure_logging
from config.settings import TradingMode, settings, trading_config
from orchestrator.pipeline import Pipeline
from orchestrator.scheduler import build_scheduler
from storage.db import get_engine, get_session_factory, init_db

logger = logging.getLogger(__name__)


def _build_broker(trading_mode: TradingMode, use_ibkr_paper: bool) -> BrokerClient:
    if trading_mode is TradingMode.LIVE or use_ibkr_paper:
        from broker.ibkr_client import IBKRClient

        return IBKRClient(settings.ibkr_host, settings.ibkr_port, settings.ibkr_client_id)
    return PaperBroker()


def build_pipeline(trading_mode: TradingMode, use_ibkr_paper: bool = False) -> Pipeline:
    claude_client = ClaudeClient(api_key=settings.anthropic_api_key, model=settings.claude_model)

    broker = _build_broker(trading_mode, use_ibkr_paper)
    broker.connect()

    engine = get_engine(settings.database_url)
    init_db(engine)
    session_factory = get_session_factory(engine)

    return Pipeline(
        sentiment_agent=SentimentAgent(claude_client),
        strategy_agent=StrategyAgent(claude_client),
        order_agent=OrderAgent(claude_client),
        risk_agent=RiskAgent(claude_client, trading_config["risk_limits"]),
        execution_agent=ExecutionAgent(broker),
        broker=broker,
        session_factory=session_factory,
        trading_config=trading_config,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Piattaforma di trading intraday multi-agente")
    parser.add_argument("--mode", choices=["paper", "live"], default=settings.trading_mode.value)
    parser.add_argument("--once", action="store_true", help="Esegue un ciclo completo una volta e termina")
    parser.add_argument(
        "--ibkr-paper",
        action="store_true",
        help="Usa IBKRClient contro un account paper IBKR invece del PaperBroker interno",
    )
    args = parser.parse_args()

    configure_logging(settings.log_level)
    trading_mode = TradingMode(args.mode)

    if trading_mode is TradingMode.LIVE:
        logger.warning("MODALITA' LIVE: gli ordini verranno piazzati con denaro reale su Interactive Brokers.")

    pipeline = build_pipeline(trading_mode, use_ibkr_paper=args.ibkr_paper)

    if args.once:
        pipeline.run_once_full()
        return

    scheduler = build_scheduler(pipeline, trading_config)
    logger.info("Scheduler avviato (modalita=%s)", trading_mode.value)
    scheduler.start()


if __name__ == "__main__":
    main()
