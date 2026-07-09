"""Storico dei trade eseguiti, aggregato per giorno (UTC) e consultabile nel
dettaglio giorno per giorno — indipendente dalla sessione corrente, copre
tutte le sessioni passate dell'utente."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import TradeDaySummary, TradeDetail
from api.security import get_current_user
from storage.models import ExecutionResultRecord, TradingSession, User

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("/days", response_model=list[TradeDaySummary])
def list_trade_days(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[TradeDaySummary]:
    day_column = func.date(ExecutionResultRecord.created_at)
    rows = db.execute(
        select(day_column.label("day"), ExecutionResultRecord.payload)
        .join(TradingSession, TradingSession.id == ExecutionResultRecord.session_id)
        .where(TradingSession.user_id == user.id, ExecutionResultRecord.status == "filled")
        .order_by(day_column.desc())
    ).all()

    summaries: dict[str, TradeDaySummary] = {}
    for day, payload_json in rows:
        payload = json.loads(payload_json)
        summary = summaries.setdefault(
            day, TradeDaySummary(date=day, trades_count=0, total_realized_pnl=0.0, total_fees=0.0)
        )
        summary.trades_count += 1
        summary.total_realized_pnl += payload.get("realized_pnl") or 0.0
        summary.total_fees += payload.get("fee") or 0.0

    return list(summaries.values())


@router.get("/days/{day}", response_model=list[TradeDetail])
def get_trade_day_detail(
    day: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[TradeDetail]:
    day_column = func.date(ExecutionResultRecord.created_at)
    rows = db.execute(
        select(ExecutionResultRecord)
        .join(TradingSession, TradingSession.id == ExecutionResultRecord.session_id)
        .where(TradingSession.user_id == user.id, day_column == day, ExecutionResultRecord.status == "filled")
        .order_by(ExecutionResultRecord.created_at.asc())
    ).scalars()

    details: list[TradeDetail] = []
    for record in rows:
        payload = json.loads(record.payload)
        details.append(
            TradeDetail(
                id=record.id,
                created_at=record.created_at,
                session_id=record.session_id,
                symbol=record.symbol,
                side=payload.get("side", ""),
                status=record.status,
                quantity=payload.get("filled_quantity", 0.0),
                avg_fill_price=payload.get("avg_fill_price"),
                fee=payload.get("fee", 0.0),
                realized_pnl=payload.get("realized_pnl"),
            )
        )
    return details
