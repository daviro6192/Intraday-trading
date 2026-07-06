"""Contratti dati (Pydantic) scambiati tra i 5 agenti della pipeline.

Ogni agente valida il proprio output contro questi modelli prima di passarlo
allo stadio successivo. Se la validazione fallisce, la pipeline interrompe
l'esecuzione per quel ciclo invece di propagare dati malformati verso ordini
reali (vedi orchestrator/pipeline.py).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Market(str, Enum):
    US_EQUITY = "US_EQUITY"
    EU_EQUITY = "EU_EQUITY"
    FOREX = "FOREX"
    CRYPTO = "CRYPTO"


class Bias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class RiskAppetite(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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
# Agente 1 -> Agente 2: sentiment di mercato e contesto macro/news
# --------------------------------------------------------------------------


class NewsItem(BaseModel):
    title: str
    summary: str = ""
    source: str
    url: str = ""
    published_at: datetime | None = None


class SymbolSentiment(BaseModel):
    symbol: str
    score: float = Field(ge=-1.0, le=1.0, description="-1 molto negativo, +1 molto positivo")
    rationale: str


class SentimentReport(BaseModel):
    report_date: datetime
    macro_summary: str
    overall_sentiment: Bias
    overall_sentiment_score: float = Field(ge=-1.0, le=1.0)
    key_events: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    sentiment_by_symbol: list[SymbolSentiment] = Field(default_factory=list)
    sources: list[NewsItem] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Agente 2 -> Agente 3: strategia giornaliera
# --------------------------------------------------------------------------


class WatchlistEntry(BaseModel):
    symbol: str
    market: Market
    bias: Bias
    rationale: str
    suggested_setup: str = ""
    timeframe: str = "intraday"


class DailyStrategy(BaseModel):
    strategy_date: datetime
    risk_appetite: RiskAppetite
    watchlist: list[WatchlistEntry] = Field(default_factory=list)
    notes: str = ""
    sentiment_summary: str = ""


# --------------------------------------------------------------------------
# Agente 3 -> Agente 4: proposte di ordine
# --------------------------------------------------------------------------


class OrderProposal(BaseModel):
    symbol: str
    market: Market
    side: OrderSide
    order_type: OrderType = OrderType.LIMIT
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    proposed_quantity: float = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    @property
    def reward_per_unit(self) -> float:
        return abs(self.take_profit - self.entry_price)

    @property
    def reward_risk_ratio(self) -> float:
        risk = self.risk_per_unit
        return self.reward_per_unit / risk if risk > 0 else 0.0


class OrderProposalBatch(BaseModel):
    """Wrapper per l'output dell'Agente 3: Claude deve restituire un oggetto
    JSON (richiesto dallo schema dei tool), non un array nudo."""

    proposals: list[OrderProposal] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Agente 4 -> Agente 5: decisioni del risk manager
# --------------------------------------------------------------------------


class RiskDecision(BaseModel):
    proposal: OrderProposal
    status: RiskDecisionStatus
    adjusted_quantity: float | None = None
    adjusted_stop_loss: float | None = None
    adjusted_take_profit: float | None = None
    reasoning: str
    failed_checks: list[str] = Field(default_factory=list)

    @property
    def final_quantity(self) -> float:
        return self.adjusted_quantity if self.adjusted_quantity is not None else self.proposal.proposed_quantity

    @property
    def final_stop_loss(self) -> float:
        return self.adjusted_stop_loss if self.adjusted_stop_loss is not None else self.proposal.stop_loss

    @property
    def final_take_profit(self) -> float:
        return self.adjusted_take_profit if self.adjusted_take_profit is not None else self.proposal.take_profit


# --------------------------------------------------------------------------
# Stato del conto, usato dall'Agente 4 e aggiornato dall'Agente 5
# --------------------------------------------------------------------------


class Position(BaseModel):
    symbol: str
    quantity: float
    avg_price: float
    market_value: float = 0.0
    unrealized_pnl: float = 0.0


class AccountState(BaseModel):
    equity: float
    cash: float
    open_positions: list[Position] = Field(default_factory=list)
    realized_pnl_today: float = 0.0
    unrealized_pnl_today: float = 0.0
    as_of: datetime = Field(default_factory=datetime.utcnow)

    @property
    def total_pnl_today(self) -> float:
        return self.realized_pnl_today + self.unrealized_pnl_today


# --------------------------------------------------------------------------
# Agente 5: esito esecuzione ordine
# --------------------------------------------------------------------------


class ExecutionResult(BaseModel):
    broker_order_id: str
    symbol: str
    side: OrderSide
    status: ExecutionStatus
    filled_quantity: float = 0.0
    avg_fill_price: float | None = None
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    error_message: str | None = None
