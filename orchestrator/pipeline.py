"""Incatena i 5 agenti della piattaforma, validando gli schemi tra uno step e
il successivo e registrando un audit trail completo su DB ad ogni ciclo."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session, sessionmaker

from agents.execution_agent import ExecutionAgent
from agents.order_agent import OrderAgent
from agents.risk_agent import RiskAgent
from agents.sentiment_agent import SentimentAgent
from agents.strategy_agent import StrategyAgent
from broker.base import BrokerClient
from common.claude_client import ClaudeClient
from common.schemas import DailyStrategy, ExecutionResult, Market
from data_sources.market_data import get_market_snapshots
from data_sources.news_feeds import fetch_all_feeds
from storage.models import persist_pipeline_run

logger = logging.getLogger(__name__)


def _symbols_for_strategy(daily_strategy: DailyStrategy) -> list[tuple[str, Market]]:
    return [(entry.symbol, entry.market) for entry in daily_strategy.watchlist]


class Pipeline:
    def __init__(
        self,
        sentiment_agent: SentimentAgent,
        strategy_agent: StrategyAgent,
        order_agent: OrderAgent,
        risk_agent: RiskAgent,
        execution_agent: ExecutionAgent,
        broker: BrokerClient,
        session_factory: sessionmaker[Session],
        trading_config: dict,
        user_id: int | None = None,
        claude_client: ClaudeClient | None = None,
    ) -> None:
        self._sentiment_agent = sentiment_agent
        self._strategy_agent = strategy_agent
        self._order_agent = order_agent
        self._risk_agent = risk_agent
        self._execution_agent = execution_agent
        self._broker = broker
        self._session_factory = session_factory
        self._trading_config = trading_config
        self._user_id = user_id
        # Riferimento pubblico al client Claude condiviso dagli agenti: usato
        # dai chiamanti (es. api/routers/pipeline.py) per leggere i token
        # consumati in questo ciclo dopo l'esecuzione e stimarne il costo.
        self.claude_client = claude_client
        self.current_strategy: DailyStrategy | None = None

    @property
    def broker(self) -> BrokerClient:
        return self._broker

    @property
    def session_factory(self) -> sessionmaker[Session]:
        return self._session_factory

    def run_pre_market(self) -> DailyStrategy:
        """Agente 1 -> Agente 2: produce la strategia operativa del giorno."""
        logger.info("Avvio ciclo pre-market: raccolta news e analisi sentiment")
        news_items = fetch_all_feeds(self._trading_config["news_feeds"])
        sentiment_report = self._sentiment_agent.run(news_items)

        logger.info("Sentiment complessivo: %s (%.2f)", sentiment_report.overall_sentiment, sentiment_report.overall_sentiment_score)
        daily_strategy = self._strategy_agent.run(sentiment_report, self._trading_config["watchlists"])
        logger.info("Strategia generata con %d strumenti in watchlist", len(daily_strategy.watchlist))

        with self._session_factory() as session:
            persist_pipeline_run(
                session, user_id=self._user_id, sentiment_report=sentiment_report, daily_strategy=daily_strategy
            )

        self.current_strategy = daily_strategy
        return daily_strategy

    def run_intraday_cycle(self, daily_strategy: DailyStrategy | None = None) -> list[ExecutionResult]:
        """Agente 3 -> Agente 4 -> Agente 5: da strategia a ordini eseguiti."""
        strategy = daily_strategy or self.current_strategy
        if strategy is None:
            logger.warning("Nessuna strategia disponibile: eseguire prima run_pre_market(). Ciclo intraday saltato.")
            return []

        if not strategy.watchlist:
            logger.info("Watchlist della strategia vuota: nessun ciclo intraday da eseguire.")
            return []

        market_snapshots = get_market_snapshots(_symbols_for_strategy(strategy))
        proposals = self._order_agent.run(strategy, market_snapshots)
        logger.info("Agente ordini: %d proposte generate", len(proposals))

        if not proposals:
            return []

        account_state = self._broker.get_account_state()
        decisions = self._risk_agent.run(proposals, account_state)
        approved = sum(1 for d in decisions if d.status.value != "rejected")
        logger.info("Risk manager: %d/%d proposte approvate o modificate", approved, len(decisions))

        results = self._execution_agent.run(decisions)
        logger.info("Esecuzione: %d ordini piazzati", len(results))

        with self._session_factory() as session:
            persist_pipeline_run(
                session,
                user_id=self._user_id,
                order_proposals=proposals,
                risk_decisions=decisions,
                execution_results=results,
            )

        return results

    def run_once_full(self) -> list[ExecutionResult]:
        """Esegue un ciclo pre-market seguito immediatamente da un ciclo intraday
        (utile per test manuali/debug con `--once`)."""
        strategy = self.run_pre_market()
        return self.run_intraday_cycle(strategy)
