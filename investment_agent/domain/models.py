"""Domain contracts for InvestmentAgent.

All numeric truth for explainability lives in :class:`MetricEvidence`.
The LLM may only narrate these values — it never computes them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_evidence_id(kind: str, symbol: str) -> str:
    """Build a stable-ish evidence id used to ground LLM suggestions."""
    return f"{kind}:{symbol}:{uuid4().hex[:8]}"


class AssetClass(str, Enum):
    """High-level asset class used by target_allocation."""

    EQUITY = "equity"
    BOND = "bond"
    CASH = "cash"
    OTHER = "other"


class Holding(BaseModel):
    """Configured ETF holding with optional per-ticker target weight."""

    symbol: str
    shares: float = Field(ge=0)
    target_weight: float = Field(ge=0, le=1)
    asset_class: AssetClass = AssetClass.EQUITY
    avg_cost: float | None = Field(
        default=None,
        ge=0,
        description="Average purchase price (prezzo medio di carico) from broker CSV",
    )
    isin: str | None = None
    product_name: str | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class DegiroPosition(BaseModel):
    """Single row extracted from a DEGIRO portfolio CSV export.

    Security note: this model holds only local file data (ticker, qty, avg cost).
    No bank credentials are ever accepted or stored.
    """

    product_name: str
    isin: str | None = None
    symbol: str
    quantity: float = Field(ge=0)
    avg_cost: float | None = Field(
        default=None,
        ge=0,
        description="Prezzo medio di carico / break-even price",
    )
    close_price: float | None = Field(default=None, ge=0)
    local_value: float | None = None
    currency: str | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class TargetAllocation(BaseModel):
    """Desired portfolio mix by asset class (must sum to 1.0).

    Example: 80% global equity, 20% bonds.
    """

    weights: dict[AssetClass, float]

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> TargetAllocation:
        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"target_allocation weights must sum to 1.0, got {total:.6f}")
        for key, value in self.weights.items():
            if value < 0 or value > 1:
                raise ValueError(f"invalid weight for {key}: {value}")
        return self

    def weight_of(self, asset_class: AssetClass) -> float:
        return self.weights.get(asset_class, 0.0)


class Thresholds(BaseModel):
    """Deterministic trigger thresholds (never computed by the LLM)."""

    weight_deviation_pct: float = Field(default=5.0, gt=0, description="Absolute pp from target")
    max_drawdown_pct: float = Field(default=15.0, gt=0)
    max_volatility_pct: float = Field(
        default=25.0,
        gt=0,
        description="Annualized return volatility threshold (%)",
    )
    history_period: str = Field(
        default="6mo",
        description="yfinance history window (default: last 6 months)",
    )


class PortfolioConfig(BaseModel):
    """Portfolio definition: holdings + optional class-level target_allocation."""

    name: str
    currency: str = "EUR"
    cash: float = Field(default=0.0, ge=0)
    holdings: list[Holding]
    thresholds: Thresholds = Field(default_factory=Thresholds)
    target_allocation: TargetAllocation | None = None

    @model_validator(mode="after")
    def validate_weights(self) -> PortfolioConfig:
        symbols = [h.symbol for h in self.holdings]
        if len(symbols) != len(set(symbols)):
            raise ValueError("duplicate symbols in holdings")

        # Per-ticker targets are required only when no class-level allocation is set.
        if self.target_allocation is None:
            total = sum(h.target_weight for h in self.holdings)
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"target_weight sum must be 1.0, got {total:.6f}")
        return self


class AgentConfig(BaseModel):
    """Runtime config for a DEGIRO-driven advisory run (local-only)."""

    name: str = "degiro-portfolio"
    currency: str = "EUR"
    cash: float = Field(default=0.0, ge=0)
    target_allocation: TargetAllocation
    thresholds: Thresholds = Field(default_factory=Thresholds)
    # Map ISIN or DEGIRO product code → yfinance ticker
    symbol_map: dict[str, str] = Field(default_factory=dict)
    # Map ticker → AssetClass when not inferred
    asset_class_map: dict[str, AssetClass] = Field(default_factory=dict)


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
    asset_class: AssetClass = AssetClass.EQUITY
    avg_cost: float | None = None
    unrealized_pnl_pct: float | None = None


class AssetClassWeight(BaseModel):
    """Current vs target weight for one asset class."""

    asset_class: AssetClass
    current_weight: float
    target_weight: float
    deviation_pp: float
    market_value: float


class PortfolioSnapshot(BaseModel):
    name: str
    currency: str
    cash: float
    total_value: float
    positions: list[Position]
    asset_class_weights: list[AssetClassWeight] = Field(default_factory=list)
    as_of: datetime = Field(default_factory=_utc_now)


class MetricKind(str, Enum):
    WEIGHT_DEVIATION = "weight_deviation"
    ASSET_CLASS_DEVIATION = "asset_class_deviation"
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
        "Report consultivo generato da InvestmentAgent in modalità locale. "
        "Nessuna credenziale bancaria è richiesta o memorizzata. "
        "Nessun ordine è stato né sarà eseguito automaticamente. "
        "Verifica i dati e le decisioni di investimento in autonomia. "
        "Non costituisce consulenza finanziaria."
    )
