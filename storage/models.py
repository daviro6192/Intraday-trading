"""Modelli ORM per l'audit trail della piattaforma crypto continua.

Due granularità distinte:
- `PipelineRun`: un passaggio del ciclo LENTO (Agente 1 fondamentale + Agente 2
  strategia), con le analisi/viste prodotte per ciascun simbolo tracciato.
- `TradingSession`: una sessione di trading continua (dal click "Inizia" al
  click "Fine"), a cui sono collegati sia i `PipelineRun` del ciclo lento sia
  gli intent/decisioni/esecuzioni del ciclo VELOCE (Agente 3 + Agente 4), per
  poter aggregare trade eseguiti/equity/P&L di sessione.

Include inoltre gli utenti della piattaforma web e le loro impostazioni
personali (API key, simboli tracciati, limiti di rischio)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from common.schemas import ExecutionResult, FundamentalAnalysis, OrderIntent, RiskDecision, RiskParameters, StrategyView


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """DateTime che garantisce tzinfo=UTC in lettura.

    SQLite non ha un vero tipo timestamp: anche con `DateTime(timezone=True)`,
    rilegge sempre un datetime "naive" (senza tzinfo), pur avendo salvato
    correttamente l'istante UTC. Senza questo, il frontend riceve un ISO
    string senza indicazione di fuso (es. "2026-07-09T12:34:49") e
    `new Date(...)` in JavaScript lo interpreta come ora LOCALE invece che
    UTC, mostrando un orario sbagliato di qualche ora invece di convertirlo
    correttamente nel fuso del browser."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)

    settings: Mapped["UserSettings"] = relationship(back_populates="user", uselist=False)


class UserSettings(Base):
    """Impostazioni personali di un utente: seedate dai default di
    config/trading.yaml alla registrazione, poi modificabili via API senza
    toccare la configurazione globale usata dalla CLI."""

    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    anthropic_api_key: Mapped[str] = mapped_column(Text, default="")
    claude_model: Mapped[str] = mapped_column(Text, default="claude-sonnet-5")
    trading_mode: Mapped[str] = mapped_column(Text, default="paper")

    ibkr_host: Mapped[str] = mapped_column(Text, default="127.0.0.1")
    ibkr_port: Mapped[int] = mapped_column(default=7497)
    ibkr_client_id: Mapped[int] = mapped_column(default=1)

    # Credenziali Binance Futures Testnet, cifrate a riposo (common/crypto.py)
    # — a differenza di anthropic_api_key, una API secret con permessi di
    # trading è più sensibile. Usate solo quando trading_mode="binance_testnet".
    binance_testnet_api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    binance_testnet_api_secret_encrypted: Mapped[str] = mapped_column(Text, default="")

    # Credenziali Crypto.com Exchange (UAT Sandbox), stesso principio delle
    # credenziali Binance sopra. Usate solo quando trading_mode="crypto_com_testnet".
    crypto_com_api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    crypto_com_api_secret_encrypted: Mapped[str] = mapped_column(Text, default="")

    # Blob JSON: struttura identica alle rispettive sezioni di config/trading.yaml
    symbols_json: Mapped[str] = mapped_column(Text, default="{}")
    risk_limits_json: Mapped[str] = mapped_column(Text, default="{}")

    # Token cumulativi usati dalla pipeline di questo utente, per stimare la
    # spesa Claude (Anthropic non espone il saldo prepagato reale via API
    # con una chiave normale — vedi common/pricing.py).
    total_input_tokens: Mapped[int] = mapped_column(default=0)
    total_output_tokens: Mapped[int] = mapped_column(default=0)
    total_cache_creation_tokens: Mapped[int] = mapped_column(default=0)
    total_cache_read_tokens: Mapped[int] = mapped_column(default=0)

    # Stato persistito del PaperBroker (cassa, posizioni aperte, P&L
    # realizzato/fee/funding oggi): senza questo, ogni ricostruzione dei
    # componenti live perderebbe la memoria dei trade precedenti tra una
    # sessione e l'altra. Vuoto finché non viene eseguito il primo ordine.
    paper_broker_state_json: Mapped[str] = mapped_column(Text, default="")

    # Ultimi RiskParameters prodotti da RiskReviewAgent, per non ripartire
    # sempre dai default di trading.yaml ad ogni nuova sessione.
    risk_parameters_json: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="settings")


class TradingSession(Base):
    """Una sessione di trading continua: dal click 'Inizia' (started_at) al
    click 'Fine' (stopped_at). Aggrega i cicli lenti (PipelineRun) e veloci
    (intent/decisioni/esecuzioni) per poter mostrare in dashboard trade
    eseguiti, controvalore e P&L della sessione."""

    __tablename__ = "trading_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    stopped_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # running | stopped | interrupted (persa per riavvio del processo) | error
    status: Mapped[str] = mapped_column(Text, default="running")
    starting_equity: Mapped[float] = mapped_column(default=0.0)
    # fees_paid_today/funding_paid_today sul broker sono cumulativi da sempre
    # (mai azzerati automaticamente): per mostrare "fee/funding pagati IN
    # QUESTA sessione" serve sottrarre il valore che avevano già all'avvio.
    starting_fees_paid: Mapped[float] = mapped_column(default=0.0)
    starting_funding_paid: Mapped[float] = mapped_column(default=0.0)

    pipeline_runs: Mapped[list["PipelineRun"]] = relationship(back_populates="session")
    order_intents: Mapped[list["OrderIntentRecord"]] = relationship(back_populates="session")
    risk_decisions: Mapped[list["RiskDecisionRecord"]] = relationship(back_populates="session")
    execution_results: Mapped[list["ExecutionResultRecord"]] = relationship(back_populates="session")
    risk_parameters_history: Mapped[list["RiskParametersRecord"]] = relationship(back_populates="session")


class PipelineRun(Base):
    """Un passaggio del ciclo lento: Agente 1 (fondamentale) + Agente 2
    (strategia) per tutti i simboli tracciati."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable: utile per test/uso senza una sessione attiva collegata.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("trading_sessions.id"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)

    session: Mapped[TradingSession | None] = relationship(back_populates="pipeline_runs")
    fundamental_analyses: Mapped[list["FundamentalAnalysisRecord"]] = relationship(back_populates="run")
    strategy_views: Mapped[list["StrategyViewRecord"]] = relationship(back_populates="run")


class FundamentalAnalysisRecord(Base):
    __tablename__ = "fundamental_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    symbol: Mapped[str] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text)

    run: Mapped[PipelineRun] = relationship(back_populates="fundamental_analyses")


class StrategyViewRecord(Base):
    __tablename__ = "strategy_views"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_runs.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    symbol: Mapped[str] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text)

    run: Mapped[PipelineRun] = relationship(back_populates="strategy_views")


class OrderIntentRecord(Base):
    """Collegato alla sessione (non al PipelineRun): il ciclo veloce di
    Agente 3 non è legato a un passaggio del ciclo lento, gira in modo
    indipendente per tutta la durata della sessione."""

    __tablename__ = "order_intents"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("trading_sessions.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    symbol: Mapped[str] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text)

    session: Mapped[TradingSession] = relationship(back_populates="order_intents")


class RiskDecisionRecord(Base):
    __tablename__ = "risk_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("trading_sessions.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    symbol: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text)

    session: Mapped[TradingSession] = relationship(back_populates="risk_decisions")


class ExecutionResultRecord(Base):
    __tablename__ = "execution_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("trading_sessions.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    symbol: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text)

    session: Mapped[TradingSession] = relationship(back_populates="execution_results")


class RiskParametersRecord(Base):
    """Audit delle revisioni periodiche di RiskReviewAgent (Agente 4, cadenza
    lenta): storico di come sono cambiati leva massima, esposizione, ecc."""

    __tablename__ = "risk_parameters_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("trading_sessions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    payload: Mapped[str] = mapped_column(Text)

    session: Mapped[TradingSession | None] = relationship(back_populates="risk_parameters_history")


def persist_analysis_cycle(
    session: Session,
    *,
    user_id: int | None,
    session_id: int | None,
    fundamental_analyses: list[FundamentalAnalysis],
    strategy_views: list[StrategyView],
) -> int:
    """Registra su DB un passaggio del ciclo lento (Agente 1 + Agente 2)."""
    run = PipelineRun(user_id=user_id, session_id=session_id)
    session.add(run)
    session.flush()  # per ottenere run.id

    for analysis in fundamental_analyses:
        session.add(FundamentalAnalysisRecord(run_id=run.id, symbol=analysis.symbol, payload=analysis.model_dump_json()))

    for view in strategy_views:
        session.add(StrategyViewRecord(run_id=run.id, symbol=view.symbol, payload=view.model_dump_json()))

    session.commit()
    return run.id


def persist_trade_tick(
    session: Session,
    *,
    session_id: int,
    order_intent: OrderIntent | None,
    risk_decision: RiskDecision | None,
    execution_result: ExecutionResult | None,
) -> None:
    """Registra su DB l'esito di un singolo tick del ciclo veloce (Agente 3 +
    Agente 4). Chiamato solo quando è stato generato un intent (non ad ogni
    tick "vuoto"), per tenere sotto controllo il volume di righe anche con
    centinaia di trade/giorno."""
    if order_intent is not None:
        session.add(OrderIntentRecord(session_id=session_id, symbol=order_intent.symbol, payload=order_intent.model_dump_json()))

    if risk_decision is not None:
        session.add(
            RiskDecisionRecord(
                session_id=session_id,
                symbol=risk_decision.intent.symbol,
                status=risk_decision.status.value,
                payload=risk_decision.model_dump_json(),
            )
        )

    if execution_result is not None:
        session.add(
            ExecutionResultRecord(
                session_id=session_id,
                symbol=execution_result.symbol,
                status=execution_result.status.value,
                payload=execution_result.model_dump_json(),
            )
        )

    session.commit()


def persist_risk_parameters(session: Session, *, session_id: int | None, risk_parameters: RiskParameters) -> None:
    session.add(RiskParametersRecord(session_id=session_id, payload=risk_parameters.model_dump_json()))
    session.commit()
