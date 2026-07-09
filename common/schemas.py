"""Contratti dati (Pydantic) scambiati tra i 4 agenti della pipeline crypto
continua: Fundamental Agent -> Strategy Agent -> Order Agent -> Risk Agent.

Ogni agente valida il proprio output contro questi modelli prima di passarlo
allo stadio successivo. Se la validazione fallisce, il ciclo si interrompe
invece di propagare dati malformati verso ordini reali (vedi
orchestrator/cycles.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Market(str, Enum):
    US_EQUITY = "US_EQUITY"
    EU_EQUITY = "EU_EQUITY"
    FOREX = "FOREX"
    CRYPTO = "CRYPTO"


class Bias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class TradeDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class RiskDecisionStatus(str, Enum):
    APPROVED = "approved"
    MODIFIED = "modified"
    REJECTED = "rejected"


class ExecutionStatus(str, Enum):
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    ERROR = "error"


# --------------------------------------------------------------------------
# Screener: gira una volta all'avvio di ogni sessione per scegliere i 3
# simboli da tradare tra un universo di candidati più ampio (non per forza
# Bitcoin/Solana/altri simboli "storici") in base a volatilità/liquidità.
# --------------------------------------------------------------------------


class SymbolCandidate(BaseModel):
    symbol: str
    binance_perp: str
    coingecko_id: str
    price_change_24h_pct: float
    quote_volume_24h_usdt: float


class SymbolSelection(BaseModel):
    """Wrapper per l'output dello screener: i ticker perpetual scelti (lo
    stesso valore `binance_perp` ricevuto in input tra i candidati). Il
    numero esatto richiesto è configurabile (trading.yaml,
    symbols_per_session): la validazione del conteggio e dell'appartenenza
    ai candidati avviene lato Python in select_symbols_for_session, non qui
    (uno schema statico non può esprimere un conteggio che varia a runtime)."""

    selected_binance_perps: list[str] = Field(min_length=1)
    rationale: str


# --------------------------------------------------------------------------
# Agente 1 -> Agente 2: analisi fondamentale crypto (niente sentiment/news)
# --------------------------------------------------------------------------


class FundamentalAnalysis(BaseModel):
    symbol: str
    as_of: datetime
    structural_bias: Bias
    score: float = Field(ge=-1.0, le=1.0, description="-1 molto ribassista, +1 molto rialzista")
    rationale: str
    flags: list[str] = Field(default_factory=list)


class FundamentalAnalysisBatch(BaseModel):
    """Wrapper per l'output dell'Agente 1: Claude deve restituire un oggetto
    JSON (richiesto dallo schema dei tool), non un array nudo."""

    analyses: list[FundamentalAnalysis] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Agente 2 -> Agente 3: vista di strategia per simbolo, aggiornata di continuo
# --------------------------------------------------------------------------


class StrategyView(BaseModel):
    symbol: str
    direction: TradeDirection
    conviction: float = Field(ge=0.0, le=1.0)
    invalidation_condition: str
    rationale: str
    updated_at: datetime = Field(default_factory=_utcnow)


class StrategyViewBatch(BaseModel):
    views: list[StrategyView] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Agente 3: intent di ordine meccanico (entrata/uscita long/short su Binance)
# --------------------------------------------------------------------------


class OrderIntent(BaseModel):
    symbol: str
    side: OrderSide
    quantity: float = Field(gt=0)
    leverage: float = Field(gt=0)
    reduce_only: bool = False
    reference_price: float = Field(gt=0)
    stop_loss: float | None = None
    take_profit: float | None = None
    reason: str
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------
# Agente 4: parametri di rischio (review periodica Claude) e decisione
# per singolo intent (gate deterministico ad ogni tick)
# --------------------------------------------------------------------------


class FeeSchedule(BaseModel):
    maker_fee_pct: float
    taker_fee_pct: float
    funding_interval_hours: int
    default_funding_rate_fallback_pct: float


class RiskParameters(BaseModel):
    max_leverage: float
    max_position_notional_pct: float
    max_daily_loss_pct: float
    min_profit_over_fees_multiple: float
    paused_symbols: list[str] = Field(default_factory=list)
    rationale: str
    updated_at: datetime = Field(default_factory=_utcnow)


class RiskDecision(BaseModel):
    intent: OrderIntent
    status: RiskDecisionStatus
    adjusted_quantity: float | None = None
    adjusted_leverage: float | None = None
    estimated_round_trip_fee: float = 0.0
    reasoning: str
    failed_checks: list[str] = Field(default_factory=list)

    @property
    def final_quantity(self) -> float:
        return self.adjusted_quantity if self.adjusted_quantity is not None else self.intent.quantity

    @property
    def final_leverage(self) -> float:
        return self.adjusted_leverage if self.adjusted_leverage is not None else self.intent.leverage


# --------------------------------------------------------------------------
# Stato del conto (posizioni futures perpetual simulate, leva/margine)
# --------------------------------------------------------------------------


class Position(BaseModel):
    symbol: str
    quantity: float
    avg_price: float
    leverage: float = 1.0
    initial_margin: float = 0.0
    liquidation_price: float | None = None
    market_value: float = 0.0
    unrealized_pnl: float = 0.0


class AccountState(BaseModel):
    equity: float
    cash: float
    open_positions: list[Position] = Field(default_factory=list)
    realized_pnl_today: float = 0.0
    unrealized_pnl_today: float = 0.0
    fees_paid_today: float = 0.0
    funding_paid_today: float = 0.0
    as_of: datetime = Field(default_factory=_utcnow)

    @property
    def total_pnl_today(self) -> float:
        return self.realized_pnl_today + self.unrealized_pnl_today


# --------------------------------------------------------------------------
# Esito esecuzione ordine (piazzato dall'Agente 3 sul PaperBroker)
# --------------------------------------------------------------------------


class ExecutionResult(BaseModel):
    broker_order_id: str
    symbol: str
    side: OrderSide
    status: ExecutionStatus
    filled_quantity: float = 0.0
    avg_fill_price: float | None = None
    fee: float = 0.0
    realized_pnl: float | None = None
    submitted_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    error_message: str | None = None
