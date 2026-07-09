"""Gestisce le sessioni di trading continue (tasto 'Inizia'/'Fine'): un task
asyncio lento (Agente 1+2, chiama Claude) e uno veloce (Agente 3+4,
meccanico) per utente, avviabili/fermabili a richiesta. Singleton in-memory
per processo: coerente con il deployment single-process di questa
piattaforma (vedi README) — non sopravvive a un riavvio del server (le
sessioni rimaste 'running' vengono riconciliate a 'interrupted' allo startup,
vedi api/main.py)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from orchestrator.cycles import SessionState, run_fundamental_strategy_cycle, run_order_risk_tick
from orchestrator.factory import LiveComponents, build_live_components_for_user, persist_live_state
from storage.models import TradingSession, User

logger = logging.getLogger(__name__)


class SessionAlreadyRunningError(RuntimeError):
    pass


class LiveSession:
    def __init__(self, user_id: int, db_session_id: int, components: LiveComponents, state: SessionState) -> None:
        self.user_id = user_id
        self.db_session_id = db_session_id
        self.components = components
        self.state = state
        self.status = "running"
        self._stop_event = asyncio.Event()
        self._slow_task: asyncio.Task | None = None
        self._fast_task: asyncio.Task | None = None

    def start(self) -> None:
        self._slow_task = asyncio.create_task(self._slow_loop())
        self._fast_task = asyncio.create_task(self._fast_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        for task in (self._slow_task, self._fast_task):
            if task is not None:
                await task
        persist_live_state(
            self.components.session_factory, self.user_id, self.components.broker, self.state.risk_parameters
        )

    async def _wait_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _slow_loop(self) -> None:
        interval_seconds = self.components.cycles_config["slow_cycle_interval_minutes"] * 60
        try:
            while not self._stop_event.is_set():
                await asyncio.to_thread(run_fundamental_strategy_cycle, self.components, self.state)
                await self._wait_or_stop(interval_seconds)
        except Exception:
            logger.exception("Sessione %s: ciclo lento (fondamentale/strategia) terminato con un errore", self.db_session_id)
            self.status = "error"
            self._stop_event.set()

    async def _fast_loop(self) -> None:
        interval_seconds = self.components.cycles_config["fast_cycle_interval_seconds"]
        try:
            while not self._stop_event.is_set():
                results = await asyncio.to_thread(run_order_risk_tick, self.components, self.state)
                self.state.trades_executed += len(results)
                await self._wait_or_stop(interval_seconds)
        except Exception:
            logger.exception("Sessione %s: ciclo veloce (ordini/rischio) terminato con un errore", self.db_session_id)
            self.status = "error"
            self._stop_event.set()


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[int, LiveSession] = {}

    def get(self, user_id: int) -> LiveSession | None:
        return self._sessions.get(user_id)

    async def start_session(self, user: User, session_factory: sessionmaker[Session]) -> TradingSession:
        if user.id in self._sessions:
            raise SessionAlreadyRunningError("È già in corso una sessione di trading per questo utente.")

        components = build_live_components_for_user(user, session_factory)
        starting_equity = components.broker.get_account_state().equity

        with session_factory() as db:
            db_session = TradingSession(user_id=user.id, status="running", starting_equity=starting_equity)
            db.add(db_session)
            db.commit()
            db.refresh(db_session)
            db_session_id = db_session.id
            started_at = db_session.started_at

        state = SessionState(db_session_id=db_session_id, risk_parameters=components.default_risk_parameters)
        live_session = LiveSession(user.id, db_session_id, components, state)
        self._sessions[user.id] = live_session
        live_session.start()

        return TradingSession(
            id=db_session_id, user_id=user.id, started_at=started_at, status="running", starting_equity=starting_equity
        )

    async def stop_session(self, user_id: int, session_factory: sessionmaker[Session]) -> None:
        live_session = self._sessions.pop(user_id, None)
        if live_session is None:
            return

        await live_session.stop()

        with session_factory() as db:
            db_session = db.get(TradingSession, live_session.db_session_id)
            if db_session is not None:
                db_session.stopped_at = datetime.now(timezone.utc)
                db_session.status = "error" if live_session.status == "error" else "stopped"
                db.commit()

    def mark_interrupted_sessions_on_startup(self, session_factory: sessionmaker[Session]) -> None:
        """Da chiamare una volta all'avvio del processo: nessuna LiveSession
        in-memory può essere sopravvissuta a un riavvio, quindi ogni
        TradingSession rimasta 'running' su DB va riconciliata."""
        from sqlalchemy import select

        with session_factory() as db:
            stale_sessions = db.scalars(select(TradingSession).where(TradingSession.status == "running")).all()
            for db_session in stale_sessions:
                db_session.status = "interrupted"
                db_session.stopped_at = datetime.now(timezone.utc)
            db.commit()


session_manager = SessionManager()
