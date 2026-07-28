from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_evidence_id(kind: str, symbol: str) -> str:
    return f"{kind}:{symbol}:{uuid4().hex[:8]}"


class Holding(BaseModel):
    """Configured ETF holding with target allocation."""

    symbol: str
    shares: float = Field(ge=0)
    target_weight: float = Field(ge=0, le=1)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class Thresholds(BaseModel):
    weight_deviation_pct: float = Field(default=5.0, gt=0, description="Absolute pp from target")
    max_drawdown_pct: float = Field(default=15.0, gt=0)
    max_volatility_pct: float = Field(
        default=25.0,
        gt=0,
        description="Annualized return volatility threshold (%)",
    )
    history_period: str = Field(default="1y")


class PortfolioConfig(BaseModel):
    name: str
    currency: str = "EUR"
    cash: float = Field(default=0.0, ge=0)
    holdings: list[Holding]
    thresholds: Thresholds = Field(default_factory=Thresholds)

    @model_validator(mode="after")
    def target_weights_sum_near_one(self) -> PortfolioConfig:
        total = sum(h.target_weight for h in self.holdings)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"target_weight sum must be 1.0, got {total:.6f}")
        symbols = [h.symbol for h in self.holdings]
        if len(symbols) != len(set(symbols)):
            raise ValueError("duplicate symbols in holdings")
        return self


class MarketQuote(BaseModel):
    symbol: str
    price: float
    currency: str | None = None
    as_of: datetime = Field(default_factory=_utc_now)


class Position(BaseModel):
    symbol: str
    shares: float
    price: float
    market_value: float
    current_weight: float
    target_weight: float
    weight_deviation_pp: float  # percentage points: (current - target) * 100


class PortfolioSnapshot(BaseModel):
    name: str
    currency: str
    cash: float
    total_value: float
    positions: list[Position]
    as_of: datetime = Field(default_factory=_utc_now)


class MetricKind(str, Enum):
    WEIGHT_DEVIATION = "weight_deviation"
    DRAWDOWN = "drawdown"
    VOLATILITY = "volatility"


class MetricEvidence(BaseModel):
    """Deterministic metric that may trigger a recommendation.

    This is the single source of truth for numbers shown next to LLM text.
    """

    evidence_id: str
    kind: MetricKind
    symbol: str
    value: float
    threshold: float
    unit: str
    formula: str
    triggered: bool
    details: dict[str, Any] = Field(default_factory=dict)
    computed_at: datetime = Field(default_factory=_utc_now)


class ActionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    REBALANCE = "REBALANCE"


class Suggestion(BaseModel):
    """Advisory suggestion. Never executed by the system."""

    action: ActionType
    symbol: str
    rationale_text: str
    evidence_ids: list[str] = Field(default_factory=list)
    indicative_shares: float | None = Field(
        default=None,
        description="Optional non-binding share quantity for the user to consider",
    )
    indicative_notional: float | None = None
    evidence: list[MetricEvidence] = Field(
        default_factory=list,
        description="Filled by ExplainabilityBinder from evidence_ids",
    )
    explainability_valid: bool = False
    validation_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def non_hold_requires_evidence_ids(self) -> Suggestion:
        if self.action != ActionType.HOLD and not self.evidence_ids:
            raise ValueError(f"{self.action} suggestion for {self.symbol} requires evidence_ids")
        return self


class AdvisoryReport(BaseModel):
    portfolio: PortfolioSnapshot
    evidence: list[MetricEvidence]
    suggestions: list[Suggestion]
    generated_at: datetime = Field(default_factory=_utc_now)
    advisor_backend: str
    disclaimer: str = (
        "Report consultivo generato da InvestmentAgent. "
        "Nessun ordine è stato né sarà eseguito automaticamente. "
        "Verifica i dati e le decisioni di investimento in autonomia. "
        "Non costituisce consulenza finanziaria."
    )
