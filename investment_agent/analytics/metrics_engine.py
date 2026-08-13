"""Deterministic metrics engine.

Computes exact percentage deviations and risk metrics (drawdown, volatility).
The LLM must never recalculate these values — it only receives them as evidence.
"""

from __future__ import annotations

import pandas as pd

from investment_agent.domain.models import (
    MetricEvidence,
    MetricKind,
    PortfolioSnapshot,
    Thresholds,
    new_evidence_id,
)


class MetricsEngine:
    """Computes exact trigger metrics that later ground LLM suggestions."""

    TRADING_DAYS_PER_YEAR = 252

    def compute(
        self,
        snapshot: PortfolioSnapshot,
        history: dict[str, pd.DataFrame],
        thresholds: Thresholds,
    ) -> list[MetricEvidence]:
        """Return all metric evidence rows for the current snapshot.

        Includes:
        - per-ticker weight deviation (pp)
        - asset-class deviation vs ``target_allocation`` (pp)
        - recent max drawdown from history (%)
        - annualized volatility (%)
        """
        evidence: list[MetricEvidence] = []
        evidence.extend(self._weight_deviations(snapshot, thresholds))
        evidence.extend(self._asset_class_deviations(snapshot, thresholds))
        evidence.extend(self._drawdowns(history, thresholds))
        evidence.extend(self._volatilities(history, thresholds))
        evidence.extend(self._period_returns(history, thresholds))
        return evidence

    def _weight_deviations(
        self,
        snapshot: PortfolioSnapshot,
        thresholds: Thresholds,
    ) -> list[MetricEvidence]:
        items: list[MetricEvidence] = []
        for pos in snapshot.positions:
            abs_dev = abs(pos.weight_deviation_pp)
            triggered = abs_dev >= thresholds.weight_deviation_pct
            items.append(
                MetricEvidence(
                    evidence_id=new_evidence_id(MetricKind.WEIGHT_DEVIATION.value, pos.symbol),
                    kind=MetricKind.WEIGHT_DEVIATION,
                    symbol=pos.symbol,
                    value=round(pos.weight_deviation_pp, 4),
                    threshold=thresholds.weight_deviation_pct,
                    unit="pp",
                    formula=(
                        "(current_weight - target_weight) * 100; "
                        f"current={pos.current_weight:.6f}, target={pos.target_weight:.6f}"
                    ),
                    triggered=triggered,
                    details={
                        "current_weight": pos.current_weight,
                        "target_weight": pos.target_weight,
                        "abs_deviation_pp": abs_dev,
                        "market_value": pos.market_value,
                        "shares": pos.shares,
                        "price": pos.price,
                        "asset_class": pos.asset_class.value,
                        "avg_cost": pos.avg_cost,
                        "unrealized_pnl_pct": pos.unrealized_pnl_pct,
                    },
                )
            )
        return items

    def _asset_class_deviations(
        self,
        snapshot: PortfolioSnapshot,
        thresholds: Thresholds,
    ) -> list[MetricEvidence]:
        items: list[MetricEvidence] = []
        for row in snapshot.asset_class_weights:
            abs_dev = abs(row.deviation_pp)
            triggered = abs_dev >= thresholds.weight_deviation_pct
            symbol = row.asset_class.value.upper()
            items.append(
                MetricEvidence(
                    evidence_id=new_evidence_id(MetricKind.ASSET_CLASS_DEVIATION.value, symbol),
                    kind=MetricKind.ASSET_CLASS_DEVIATION,
                    symbol=symbol,
                    value=round(row.deviation_pp, 4),
                    threshold=thresholds.weight_deviation_pct,
                    unit="pp",
                    formula=(
                        "(class_current_weight - class_target_weight) * 100; "
                        f"current={row.current_weight:.6f}, target={row.target_weight:.6f}"
                    ),
                    triggered=triggered,
                    details={
                        "asset_class": row.asset_class.value,
                        "current_weight": row.current_weight,
                        "target_weight": row.target_weight,
                        "abs_deviation_pp": abs_dev,
                        "market_value": row.market_value,
                    },
                )
            )
        return items

    def _drawdowns(
        self,
        history: dict[str, pd.DataFrame],
        thresholds: Thresholds,
    ) -> list[MetricEvidence]:
        items: list[MetricEvidence] = []
        for symbol, frame in history.items():
            if "Close" not in frame.columns or frame.empty:
                continue
            closes = frame["Close"].astype(float)
            running_max = closes.cummax()
            drawdown = (closes / running_max) - 1.0
            max_dd = float(drawdown.min())  # negative or zero
            max_dd_pct = abs(max_dd) * 100.0
            triggered = max_dd_pct >= thresholds.max_drawdown_pct
            peak_idx = running_max.idxmax() if not running_max.empty else None
            trough_idx = drawdown.idxmin() if not drawdown.empty else None
            items.append(
                MetricEvidence(
                    evidence_id=new_evidence_id(MetricKind.DRAWDOWN.value, symbol),
                    kind=MetricKind.DRAWDOWN,
                    symbol=symbol,
                    value=round(max_dd_pct, 4),
                    threshold=thresholds.max_drawdown_pct,
                    unit="%",
                    formula="max_t (peak_to_trough): abs(min(Close/cummax(Close) - 1)) * 100",
                    triggered=triggered,
                    details={
                        "max_drawdown_fraction": max_dd,
                        "peak_date": str(peak_idx) if peak_idx is not None else None,
                        "trough_date": str(trough_idx) if trough_idx is not None else None,
                        "last_close": float(closes.iloc[-1]),
                        "history_bars": len(closes),
                    },
                )
            )
        return items

    def _volatilities(
        self,
        history: dict[str, pd.DataFrame],
        thresholds: Thresholds,
    ) -> list[MetricEvidence]:
        items: list[MetricEvidence] = []
        for symbol, frame in history.items():
            if "Close" not in frame.columns or len(frame) < 2:
                continue
            closes = frame["Close"].astype(float)
            daily_returns = closes.pct_change().dropna()
            if daily_returns.empty:
                continue
            daily_std = float(daily_returns.std(ddof=1))
            annualized_pct = daily_std * (self.TRADING_DAYS_PER_YEAR**0.5) * 100.0
            triggered = annualized_pct >= thresholds.max_volatility_pct
            items.append(
                MetricEvidence(
                    evidence_id=new_evidence_id(MetricKind.VOLATILITY.value, symbol),
                    kind=MetricKind.VOLATILITY,
                    symbol=symbol,
                    value=round(annualized_pct, 4),
                    threshold=thresholds.max_volatility_pct,
                    unit="%",
                    formula=(
                        "std(daily_returns, ddof=1) * sqrt(252) * 100; "
                        f"n={len(daily_returns)}"
                    ),
                    triggered=triggered,
                    details={
                        "daily_std": daily_std,
                        "observations": len(daily_returns),
                        "last_close": float(closes.iloc[-1]),
                    },
                )
            )
        return items

    def _period_returns(
        self,
        history: dict[str, pd.DataFrame],
        thresholds: Thresholds,
    ) -> list[MetricEvidence]:
        """Observed total return over the history window (not a future forecast)."""
        items: list[MetricEvidence] = []
        for symbol, frame in history.items():
            if "Close" not in frame.columns or len(frame) < 2:
                continue
            closes = frame["Close"].astype(float)
            start = float(closes.iloc[0])
            end = float(closes.iloc[-1])
            if start <= 0:
                continue
            period_pct = (end / start - 1.0) * 100.0
            # Informational only — never triggers actions by itself.
            items.append(
                MetricEvidence(
                    evidence_id=new_evidence_id(MetricKind.PERIOD_RETURN.value, symbol),
                    kind=MetricKind.PERIOD_RETURN,
                    symbol=symbol,
                    value=round(period_pct, 4),
                    threshold=0.0,
                    unit="%",
                    formula="(last_close / first_close - 1) * 100 over history window",
                    triggered=False,
                    details={
                        "first_close": start,
                        "last_close": end,
                        "bars": len(closes),
                        "history_period": thresholds.history_period,
                        "note": "rendimento storico osservato; non è una previsione",
                    },
                )
            )
        return items
