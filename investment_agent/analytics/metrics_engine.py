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

    def compute(
        self,
        snapshot: PortfolioSnapshot,
        history: dict[str, pd.DataFrame],
        thresholds: Thresholds,
    ) -> list[MetricEvidence]:
        evidence: list[MetricEvidence] = []
        evidence.extend(self._weight_deviations(snapshot, thresholds))
        evidence.extend(self._drawdowns(history, thresholds))
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
                    },
                )
            )
        return items
