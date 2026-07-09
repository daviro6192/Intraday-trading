"""Funzioni pure (nessun sleep, nessun loop) per un singolo passaggio del
ciclo lento (Agente 1 + Agente 2 + review periodica dell'Agente 4) o del
ciclo veloce (Agente 3 + gate dell'Agente 4). Riusate sia da
orchestrator/session_manager.py (loop reale con asyncio) sia direttamente
dai test, per poter verificare il comportamento senza attese reali."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from common.schemas import ExecutionResult, FundamentalAnalysis, RiskParameters, StrategyView
from data_sources.binance_market_data import get_mark_price, get_recent_klines
from data_sources.coingecko_fundamentals import fetch_coin_fundamentals
from orchestrator.factory import LiveComponents, persist_live_state
from storage.models import persist_analysis_cycle, persist_risk_parameters, persist_trade_tick

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """Stato in-memory di una sessione live: ultime analisi/viste prodotte
    dal ciclo lento, parametri di rischio correnti, e bookkeeping interno
    (stop-loss/take-profit) per simbolo mantenuto dal ciclo veloce."""

    db_session_id: int
    risk_parameters: RiskParameters
    fundamentals_by_symbol: dict[str, FundamentalAnalysis] = field(default_factory=dict)
    strategy_views: dict[str, StrategyView] = field(default_factory=dict)
    stop_loss_by_symbol: dict[str, float] = field(default_factory=dict)
    take_profit_by_symbol: dict[str, float] = field(default_factory=dict)
    trades_executed: int = 0


def _build_performance_summary(components: LiveComponents, state: SessionState) -> dict:
    account = components.broker.get_account_state()
    return {
        "trades_executed_in_session": state.trades_executed,
        "equity": account.equity,
        "realized_pnl_today": account.realized_pnl_today,
        "unrealized_pnl_today": account.unrealized_pnl_today,
        "fees_paid_today": account.fees_paid_today,
        "funding_paid_today": account.funding_paid_today,
    }


def run_fundamental_strategy_cycle(components: LiveComponents, state: SessionState) -> None:
    """Un passaggio del ciclo lento: aggiorna l'analisi fondamentale, la
    vista di strategia per ciascun simbolo tracciato, e periodicamente
    rivede i parametri di rischio. Persiste tutto su DB."""
    coingecko_ids = [cfg["coingecko_id"] for cfg in components.symbols.values()]
    fundamentals_raw = fetch_coin_fundamentals(coingecko_ids)

    fundamentals_by_symbol: dict[str, dict | None] = {}
    for cfg in components.symbols.values():
        fundamentals_by_symbol[cfg["binance_perp"]] = fundamentals_raw.get(cfg["coingecko_id"])

    analyses = components.fundamental_agent.run(fundamentals_by_symbol)
    for analysis in analyses:
        state.fundamentals_by_symbol[analysis.symbol] = analysis

    previous_views = list(state.strategy_views.values())
    views = components.strategy_agent.run(list(state.fundamentals_by_symbol.values()), previous_views)
    for view in views:
        state.strategy_views[view.symbol] = view

    with components.session_factory() as db:
        persist_analysis_cycle(
            db,
            user_id=components.user_id,
            session_id=state.db_session_id,
            fundamental_analyses=list(state.fundamentals_by_symbol.values()),
            strategy_views=list(state.strategy_views.values()),
        )

    performance_summary = _build_performance_summary(components, state)
    context = {
        "fundamentals": [a.model_dump(mode="json") for a in state.fundamentals_by_symbol.values()],
        "strategy_views": [v.model_dump(mode="json") for v in state.strategy_views.values()],
    }
    state.risk_parameters = components.risk_review_agent.run(state.risk_parameters, performance_summary, context)

    with components.session_factory() as db:
        persist_risk_parameters(db, session_id=state.db_session_id, risk_parameters=state.risk_parameters)

    persist_live_state(components.session_factory, components.user_id, components.broker, state.risk_parameters)


def run_order_risk_tick(components: LiveComponents, state: SessionState) -> list[ExecutionResult]:
    """Un tick del ciclo veloce: per ciascun simbolo con una vista di
    strategia disponibile, valuta entrata/uscita long/short tramite l'Order
    Agent (meccanico) e il gate del Risk Agent (deterministico). Persiste
    solo i tick in cui è stato generato un intent."""
    execution_results: list[ExecutionResult] = []

    for cfg in components.symbols.values():
        symbol = cfg["binance_perp"]
        view = state.strategy_views.get(symbol)
        if view is None:
            continue

        account = components.broker.get_account_state()
        position = next((p for p in account.open_positions if p.symbol == symbol), None)

        mark_price = get_mark_price(symbol)
        if mark_price is None:
            mark_price = position.avg_price if position is not None else None
        if mark_price is None:
            logger.warning("Nessun prezzo disponibile per %s: tick saltato", symbol)
            continue

        klines = get_recent_klines(symbol)

        tick = components.order_agent.run_tick(
            symbol,
            view,
            position,
            mark_price,
            klines,
            account,
            state.risk_parameters,
            state.stop_loss_by_symbol.get(symbol),
            state.take_profit_by_symbol.get(symbol),
        )

        if tick.new_stop_loss is not None:
            state.stop_loss_by_symbol[symbol] = tick.new_stop_loss
        else:
            state.stop_loss_by_symbol.pop(symbol, None)

        if tick.new_take_profit is not None:
            state.take_profit_by_symbol[symbol] = tick.new_take_profit
        else:
            state.take_profit_by_symbol.pop(symbol, None)

        if tick.intent is None:
            continue

        with components.session_factory() as db:
            persist_trade_tick(
                db,
                session_id=state.db_session_id,
                order_intent=tick.intent,
                risk_decision=tick.risk_decision,
                execution_result=tick.execution_result,
            )

        if tick.execution_result is not None and tick.execution_result.status.value == "filled":
            execution_results.append(tick.execution_result)

    if execution_results:
        persist_live_state(components.session_factory, components.user_id, components.broker, state.risk_parameters)

    return execution_results
