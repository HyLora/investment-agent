"""Market data adapters.

Downloads live quotes and historical OHLCV via yfinance.
Default history window is 6 months as required by the advisory pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd
import yfinance as yf

from investment_agent.domain.models import MarketQuote


class MarketDataPort(ABC):
    """Port for market data providers (enables stubs in tests)."""

    @abstractmethod
    def fetch_quotes(self, symbols: Iterable[str]) -> dict[str, MarketQuote]:
        """Fetch latest market prices for ``symbols``."""
        raise NotImplementedError

    @abstractmethod
    def fetch_history(self, symbols: Iterable[str], period: str = "6mo") -> dict[str, pd.DataFrame]:
        """Fetch historical OHLCV for ``symbols`` over ``period``."""
        raise NotImplementedError


class YFinanceClient(MarketDataPort):
    """Downloads ETF quotes and historical OHLCV via yfinance."""

    def fetch_quotes(self, symbols: Iterable[str]) -> dict[str, MarketQuote]:
        """Resolve live (or last close) prices for each symbol."""
        result: dict[str, MarketQuote] = {}
        now = datetime.now(timezone.utc)
        for symbol in symbols:
            ticker = yf.Ticker(symbol)
            price = self._resolve_price(ticker)
            if price is None or price <= 0:
                raise ValueError(f"Unable to resolve market price for {symbol}")
            currency = None
            try:
                currency = (ticker.fast_info or {}).get("currency")  # type: ignore[union-attr]
            except Exception:
                info = getattr(ticker, "info", {}) or {}
                currency = info.get("currency")
            result[symbol] = MarketQuote(
                symbol=symbol, price=float(price), currency=currency, as_of=now
            )
        return result

    def fetch_history(self, symbols: Iterable[str], period: str = "6mo") -> dict[str, pd.DataFrame]:
        """Download historical bars (default: last 6 months)."""
        out: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            hist = yf.Ticker(symbol).history(period=period, auto_adjust=True)
            if hist is None or hist.empty:
                raise ValueError(f"No historical data for {symbol} (period={period})")
            out[symbol] = hist
        return out

    @staticmethod
    def _resolve_price(ticker: yf.Ticker) -> float | None:
        try:
            fast = ticker.fast_info
            for key in ("last_price", "lastPrice", "regular_market_price", "previous_close"):
                value = getattr(fast, key, None) if not isinstance(fast, dict) else fast.get(key)
                if value is not None:
                    return float(value)
        except Exception:
            pass
        hist = ticker.history(period="5d", auto_adjust=True)
        if hist is not None and not hist.empty:
            return float(hist["Close"].iloc[-1])
        return None
