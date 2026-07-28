"""Portfolio valuation and weight computation.

Compares live market values against per-ticker and/or asset-class targets.
All calculations are deterministic — never delegated to an LLM.
"""

from __future__ import annotations

from investment_agent.domain.models import (
    AssetClass,
    AssetClassWeight,
    Holding,
    MarketQuote,
    PortfolioConfig,
    PortfolioSnapshot,
    Position,
)


class PortfolioMonitor:
    """Values holdings and computes current vs target weights."""

    def build_snapshot(
        self,
        config: PortfolioConfig,
        quotes: dict[str, MarketQuote],
    ) -> PortfolioSnapshot:
        """Build a marked-to-market snapshot with weight deviations.

        Parameters
        ----------
        config:
            Portfolio definition (holdings, cash, optional target_allocation).
        quotes:
            Mapping symbol → live :class:`MarketQuote` from yfinance.
        """
        invested = 0.0
        interim: list[tuple[Holding, float, float]] = []
        for holding in config.holdings:
            quote = quotes.get(holding.symbol)
            if quote is None:
                raise KeyError(f"Missing quote for {holding.symbol}")
            mv = holding.shares * quote.price
            interim.append((holding, quote.price, mv))
            invested += mv

        total = invested + config.cash
        if total <= 0:
            raise ValueError("Portfolio total value must be > 0")

        positions: list[Position] = []
        for holding, price, mv in interim:
            current_w = mv / total
            # Prefer explicit per-ticker target; else derive from class allocation share.
            target_w = self._ticker_target_weight(holding, config, mv, total, interim)
            deviation_pp = (current_w - target_w) * 100.0
            pnl_pct = None
            if holding.avg_cost and holding.avg_cost > 0:
                pnl_pct = (price / holding.avg_cost - 1.0) * 100.0
            positions.append(
                Position(
                    symbol=holding.symbol,
                    shares=holding.shares,
                    price=price,
                    market_value=mv,
                    current_weight=current_w,
                    target_weight=target_w,
                    weight_deviation_pp=deviation_pp,
                    asset_class=holding.asset_class,
                    avg_cost=holding.avg_cost,
                    unrealized_pnl_pct=pnl_pct,
                )
            )

        class_weights = self._asset_class_weights(positions, config, total)

        return PortfolioSnapshot(
            name=config.name,
            currency=config.currency,
            cash=config.cash,
            total_value=total,
            positions=positions,
            asset_class_weights=class_weights,
        )

    @staticmethod
    def _ticker_target_weight(
        holding: Holding,
        config: PortfolioConfig,
        market_value: float,
        total: float,
        interim: list[tuple[Holding, float, float]],
    ) -> float:
        """Resolve target weight for a ticker.

        If ``target_allocation`` is set, distribute each class target across
        tickers in that class proportionally to their current market value
        (neutral within-class). Otherwise use ``holding.target_weight``.
        """
        if config.target_allocation is None:
            return holding.target_weight

        class_target = config.target_allocation.weight_of(holding.asset_class)
        class_mv = sum(mv for h, _p, mv in interim if h.asset_class == holding.asset_class)
        if class_mv <= 0:
            return class_target
        return class_target * (market_value / class_mv)

    @staticmethod
    def _asset_class_weights(
        positions: list[Position],
        config: PortfolioConfig,
        total: float,
    ) -> list[AssetClassWeight]:
        if config.target_allocation is None:
            return []

        by_class: dict[AssetClass, float] = {}
        for pos in positions:
            by_class[pos.asset_class] = by_class.get(pos.asset_class, 0.0) + pos.market_value
        if config.cash > 0:
            by_class[AssetClass.CASH] = by_class.get(AssetClass.CASH, 0.0) + config.cash

        results: list[AssetClassWeight] = []
        classes = set(by_class) | set(config.target_allocation.weights)
        for asset_class in sorted(classes, key=lambda c: c.value):
            mv = by_class.get(asset_class, 0.0)
            current = mv / total if total else 0.0
            target = config.target_allocation.weight_of(asset_class)
            results.append(
                AssetClassWeight(
                    asset_class=asset_class,
                    current_weight=current,
                    target_weight=target,
                    deviation_pp=(current - target) * 100.0,
                    market_value=mv,
                )
            )
        return results
