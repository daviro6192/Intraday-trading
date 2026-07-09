"""Avvio/stop della sessione di trading continua (tasto 'Inizia'/'Fine') e
lettura degli aggregati (trade eseguiti, controvalore, P&L di sessione)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from api.deps import get_db, get_session_factory_dep
from api.schemas import SessionStatusResponse
from api.security import get_current_user
from orchestrator.factory import build_broker_for_user, build_fee_schedule_from_config
from orchestrator.session_manager import SessionAlreadyRunningError, session_manager
from storage.models import ExecutionResultRecord, TradingSession, User

router = APIRouter(prefix="/api/session", tags=["session"])


def _read_status(user: User, db: Session) -> SessionStatusResponse:
    live_session = session_manager.get(user.id)

    latest_db_session = db.scalar(
        select(TradingSession)
        .where(TradingSession.user_id == user.id)
        .order_by(TradingSession.started_at.desc())
        .limit(1)
    )
    if latest_db_session is None:
        return SessionStatusResponse(
            session_id=None,
            status="not_started",
            started_at=None,
            stopped_at=None,
            trades_executed=0,
            starting_equity=None,
            current_equity=None,
            session_pnl=None,
            fees_paid_today=0.0,
            funding_paid_today=0.0,
        )

    if live_session is not None:
        account = live_session.components.broker.get_account_state()
        trades_executed = live_session.state.trades_executed
        status_value = live_session.status
    else:
        trades_executed = (
            db.scalar(
                select(func.count())
                .select_from(ExecutionResultRecord)
                .where(
                    ExecutionResultRecord.session_id == latest_db_session.id,
                    ExecutionResultRecord.status == "filled",
                )
            )
            or 0
        )
        broker = build_broker_for_user(user.settings, build_fee_schedule_from_config())
        account = broker.get_account_state()
        status_value = latest_db_session.status

    session_pnl = (
        account.equity - latest_db_session.starting_equity if latest_db_session.starting_equity else account.equity
    )

    return SessionStatusResponse(
        session_id=latest_db_session.id,
        status=status_value,
        started_at=latest_db_session.started_at,
        stopped_at=latest_db_session.stopped_at,
        trades_executed=trades_executed,
        starting_equity=latest_db_session.starting_equity,
        current_equity=account.equity,
        session_pnl=session_pnl,
        # fees_paid_today/funding_paid_today sul broker sono cumulativi da
        # sempre: sottraiamo quanto già pagato prima dell'avvio di QUESTA
        # sessione, altrimenti mostrerebbero il totale storico dell'account,
        # non quanto pagato durante la sessione in corso.
        fees_paid_today=account.fees_paid_today - latest_db_session.starting_fees_paid,
        funding_paid_today=account.funding_paid_today - latest_db_session.starting_funding_paid,
    )


@router.post("/start", response_model=SessionStatusResponse, status_code=status.HTTP_201_CREATED)
async def start_session(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    factory: sessionmaker[Session] = Depends(get_session_factory_dep),
) -> SessionStatusResponse:
    try:
        await session_manager.start_session(user, factory)
    except SessionAlreadyRunningError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - errori di configurazione (es. simboli non validi) vanno mostrati all'utente
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Impossibile avviare la sessione: {exc}"
        ) from exc

    return _read_status(user, db)


@router.post("/stop", response_model=SessionStatusResponse)
async def stop_session(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    factory: sessionmaker[Session] = Depends(get_session_factory_dep),
) -> SessionStatusResponse:
    await session_manager.stop_session(user.id, factory)
    return _read_status(user, db)


@router.get("/status", response_model=SessionStatusResponse)
def get_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> SessionStatusResponse:
    return _read_status(user, db)
