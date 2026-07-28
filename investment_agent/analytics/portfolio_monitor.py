from __future__ import annotations

from investment_agent.domain.models import (
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
            deviation_pp = (current_w - holding.target_weight) * 100.0
            positions.append(
                Position(
                    symbol=holding.symbol,
                    shares=holding.shares,
                    price=price,
                    market_value=mv,
                    current_weight=current_w,
                    target_weight=holding.target_weight,
                    weight_deviation_pp=deviation_pp,
                )
            )

        return PortfolioSnapshot(
            name=config.name,
            currency=config.currency,
            cash=config.cash,
            total_value=total,
            positions=positions,
        )
