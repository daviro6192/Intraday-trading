"""Modelli ORM per l'audit trail completo di ogni fase della pipeline: ogni
esecuzione (PipelineRun) è collegata al report di sentiment, alla strategia,
alle proposte di ordine, alle decisioni del risk manager e agli esiti di
esecuzione che ne sono derivati."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from common.schemas import DailyStrategy, ExecutionResult, OrderProposal, RiskDecision, SentimentReport


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    sentiment_reports: Mapped[list["SentimentReportRecord"]] = relationship(back_populates="run")
    strategies: Mapped[list["DailyStrategyRecord"]] = relationship(back_populates="run")
    order_proposals: Mapped[list["OrderProposalRecord"]] = relationship(back_populates="run")
    risk_decisions: Mapped[list["RiskDecisionRecord"]] = relationship(back_populates="run")
    execution_results: Mapped[list["ExecutionResultRecord"]] = relationship(back_populates="run")


class SentimentReportRecord(Base):
    __tablename__ = "sentiment_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    payload: Mapped[str] = mapped_column(Text)

    run: Mapped[PipelineRun] = relationship(back_populates="sentiment_reports")


class DailyStrategyRecord(Base):
    __tablename__ = "daily_strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    payload: Mapped[str] = mapped_column(Text)

    run: Mapped[PipelineRun] = relationship(back_populates="strategies")


class OrderProposalRecord(Base):
    __tablename__ = "order_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    symbol: Mapped[str] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text)

    run: Mapped[PipelineRun] = relationship(back_populates="order_proposals")


class RiskDecisionRecord(Base):
    __tablename__ = "risk_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    symbol: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text)

    run: Mapped[PipelineRun] = relationship(back_populates="risk_decisions")


class ExecutionResultRecord(Base):
    __tablename__ = "execution_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    symbol: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text)

    run: Mapped[PipelineRun] = relationship(back_populates="execution_results")


def persist_pipeline_run(
    session: Session,
    *,
    sentiment_report: SentimentReport | None = None,
    daily_strategy: DailyStrategy | None = None,
    order_proposals: list[OrderProposal] | None = None,
    risk_decisions: list[RiskDecision] | None = None,
    execution_results: list[ExecutionResult] | None = None,
) -> int:
    """Registra su DB l'audit trail di un ciclo di pipeline (anche parziale, se
    si interrompe prima dell'esecuzione)."""
    run = PipelineRun()
    session.add(run)
    session.flush()  # per ottenere run.id

    if sentiment_report is not None:
        session.add(SentimentReportRecord(run_id=run.id, payload=sentiment_report.model_dump_json()))

    if daily_strategy is not None:
        session.add(DailyStrategyRecord(run_id=run.id, payload=daily_strategy.model_dump_json()))

    for proposal in order_proposals or []:
        session.add(OrderProposalRecord(run_id=run.id, symbol=proposal.symbol, payload=proposal.model_dump_json()))

    for decision in risk_decisions or []:
        session.add(
            RiskDecisionRecord(
                run_id=run.id,
                symbol=decision.proposal.symbol,
                status=decision.status.value,
                payload=decision.model_dump_json(),
            )
        )

    for result in execution_results or []:
        session.add(
            ExecutionResultRecord(
                run_id=run.id,
                symbol=result.symbol,
                status=result.status.value,
                payload=result.model_dump_json(),
            )
        )

    session.commit()
    return run.id
