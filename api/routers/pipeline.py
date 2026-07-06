"""Trigger manuali della pipeline e consultazione dell'audit trail
dell'utente corrente."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from api.deps import get_db, get_session_factory_dep
from api.schemas import PipelineRunDetail, PipelineRunSummary
from api.security import get_current_user
from common.schemas import DailyStrategy
from orchestrator.factory import build_pipeline_for_user
from orchestrator.pipeline import Pipeline
from storage.models import DailyStrategyRecord, PipelineRun, User

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


def _latest_strategy(db: Session, user_id: int) -> DailyStrategy | None:
    record = db.scalar(
        select(DailyStrategyRecord)
        .join(PipelineRun)
        .where(PipelineRun.user_id == user_id)
        .order_by(DailyStrategyRecord.created_at.desc())
        .limit(1)
    )
    if record is None:
        return None
    return DailyStrategy.model_validate_json(record.payload)


def _accumulate_claude_usage(db: Session, user: User, pipeline: Pipeline) -> None:
    """Somma i token consumati in questo ciclo alle impostazioni dell'utente,
    per stimare la spesa Claude (vedi common/pricing.py)."""
    claude_client = pipeline.claude_client
    if claude_client is None:
        return
    settings = user.settings
    settings.total_input_tokens += claude_client.total_input_tokens
    settings.total_output_tokens += claude_client.total_output_tokens
    settings.total_cache_creation_tokens += claude_client.total_cache_creation_tokens
    settings.total_cache_read_tokens += claude_client.total_cache_read_tokens
    db.commit()


@router.post("/run-pre-market")
def run_pre_market(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    factory: sessionmaker[Session] = Depends(get_session_factory_dep),
) -> dict:
    pipeline = None
    try:
        pipeline = build_pipeline_for_user(user, factory)
        strategy = pipeline.run_pre_market()
    except Exception as exc:  # noqa: BLE001 - errori esterni (Claude/broker) diventano un errore API leggibile
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Errore nel ciclo pre-market: {exc}"
        ) from exc
    finally:
        if pipeline is not None:
            _accumulate_claude_usage(db, user, pipeline)
    return strategy.model_dump(mode="json")


@router.post("/run-intraday")
def run_intraday(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    factory: sessionmaker[Session] = Depends(get_session_factory_dep),
) -> dict:
    strategy = _latest_strategy(db, user.id)
    if strategy is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nessuna strategia disponibile: eseguire prima /api/pipeline/run-pre-market",
        )

    pipeline = None
    try:
        pipeline = build_pipeline_for_user(user, factory)
        results = pipeline.run_intraday_cycle(strategy)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Errore nel ciclo intraday: {exc}"
        ) from exc
    finally:
        if pipeline is not None:
            _accumulate_claude_usage(db, user, pipeline)

    return {"execution_results": [r.model_dump(mode="json") for r in results]}


@router.get("/runs", response_model=list[PipelineRunSummary])
def list_runs(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[PipelineRunSummary]:
    runs = db.scalars(
        select(PipelineRun).where(PipelineRun.user_id == user.id).order_by(PipelineRun.started_at.desc())
    ).all()
    return [
        PipelineRunSummary(
            id=run.id,
            started_at=run.started_at,
            has_sentiment_report=len(run.sentiment_reports) > 0,
            has_daily_strategy=len(run.strategies) > 0,
            order_proposals_count=len(run.order_proposals),
            risk_decisions_count=len(run.risk_decisions),
            execution_results_count=len(run.execution_results),
        )
        for run in runs
    ]


@router.get("/runs/{run_id}", response_model=PipelineRunDetail)
def get_run(run_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> PipelineRunDetail:
    run = db.get(PipelineRun, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run non trovata")

    return PipelineRunDetail(
        id=run.id,
        started_at=run.started_at,
        sentiment_report=json.loads(run.sentiment_reports[0].payload) if run.sentiment_reports else None,
        daily_strategy=json.loads(run.strategies[0].payload) if run.strategies else None,
        order_proposals=[json.loads(p.payload) for p in run.order_proposals],
        risk_decisions=[json.loads(d.payload) for d in run.risk_decisions],
        execution_results=[json.loads(e.payload) for e in run.execution_results],
    )
