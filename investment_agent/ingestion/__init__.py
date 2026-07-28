"""Data ingestion: parse local broker exports.

Security: only local CSV files are read. No bank credentials, tokens, or
broker API keys are accepted by this package.
"""

from __future__ import annotations

from investment_agent.ingestion.portfolio_parser import (
    DegiroParseError,
    parse_degiro_portfolio_csv,
    positions_to_holdings,
)

__all__ = [
    "DegiroParseError",
    "parse_degiro_portfolio_csv",
    "positions_to_holdings",
]
