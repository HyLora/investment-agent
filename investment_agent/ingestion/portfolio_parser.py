"""Parse DEGIRO portfolio CSV exports into structured holdings.

Expected workflow
-----------------
1. In the DEGIRO web/app UI, export the *Portfolio* overview as CSV.
2. Save the file locally (never commit real broker data).
3. Pass the path to :func:`parse_degiro_portfolio_csv`.

This module never contacts DEGIRO servers and never asks for credentials.
It only reads a user-supplied local file.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable, Mapping

from investment_agent.domain.models import (
    AssetClass,
    DegiroPosition,
    Holding,
    TargetAllocation,
)


class DegiroParseError(ValueError):
    """Raised when a DEGIRO CSV cannot be interpreted."""


# Flexible header aliases (EN / IT / NL) seen in DEGIRO exports.
_PRODUCT_ALIASES = {"product", "prodotto", "productnaam", "nome", "name"}
_ISIN_ALIASES = {"isin", "symbol/isin", "symbol / isin"}
_SYMBOL_ALIASES = {"symbol", "ticker", "codice", "code"}
_QTY_ALIASES = {"quantity", "quantità", "quantita", "amount", "aantal", "qty", "shares"}
_AVG_COST_ALIASES = {
    "break-even price",
    "break even price",
    "break-even prijs",
    "prezzo medio",
    "prezzo medio di carico",
    "avg cost",
    "average cost",
    "average price",
    "cost basis",
}
_CLOSE_ALIASES = {
    "close",
    "close price",
    "closing",
    "closing price",
    "prezzo di chiusura",
    "slotkoers",
}
_VALUE_ALIASES = {"local value", "valore locale", "waarde in", "value"}
_CCY_ALIASES = {"local currency", "currency", "valuta", "valuta locale"}


def _norm_header(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _find_column(headers: Iterable[str], aliases: set[str]) -> str | None:
    normalized = {_norm_header(h): h for h in headers}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def _parse_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text in {"-", "—", "n/a", "N/A"}:
        return None
    # DEGIRO often uses European formats: 1.234,56 or 1234,56
    text = text.replace("€", "").replace("%", "").strip()
    if re.search(r"\d,\d{1,4}$", text) and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text and "." not in text:
        text = text.replace(",", ".")
    text = re.sub(r"[^\d.\-]", "", text)
    if not text or text in {".", "-", "-."}:
        return None
    return float(text)


def parse_degiro_portfolio_csv(
    path: str | Path,
    *,
    symbol_map: Mapping[str, str] | None = None,
    encoding: str = "utf-8-sig",
) -> list[DegiroPosition]:
    """Read a DEGIRO portfolio CSV and extract ticker, quantity, avg cost.

    Parameters
    ----------
    path:
        Local filesystem path to the CSV export.
    symbol_map:
        Optional ISIN / product-name → yfinance ticker mapping.
        Example: ``{"IE00BK5BQT80": "VWCE.DE"}``.
    encoding:
        File encoding (DEGIRO exports are often UTF-8 with BOM).

    Returns
    -------
    list[DegiroPosition]
        One entry per non-empty product row.

    Raises
    ------
    DegiroParseError
        If required columns (product/quantity) are missing or no rows parse.
    FileNotFoundError
        If ``path`` does not exist.
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"DEGIRO CSV not found: {csv_path}")

    symbol_map = {k.upper(): v.upper() for k, v in (symbol_map or {}).items()}

    with csv_path.open(newline="", encoding=encoding) as handle:
        # Skip comment / blank preamble lines (common in sample exports).
        data_lines = [
            line for line in handle if line.strip() and not line.lstrip().startswith("#")
        ]
        if not data_lines:
            raise DegiroParseError(f"CSV is empty after removing comments: {csv_path}")
        sample = "".join(data_lines[:20])
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(data_lines, dialect=dialect)
        if not reader.fieldnames:
            raise DegiroParseError("CSV has no header row")

        product_col = _find_column(reader.fieldnames, _PRODUCT_ALIASES)
        isin_col = _find_column(reader.fieldnames, _ISIN_ALIASES)
        symbol_col = _find_column(reader.fieldnames, _SYMBOL_ALIASES)
        qty_col = _find_column(reader.fieldnames, _QTY_ALIASES)
        avg_col = _find_column(reader.fieldnames, _AVG_COST_ALIASES)
        close_col = _find_column(reader.fieldnames, _CLOSE_ALIASES)
        value_col = _find_column(reader.fieldnames, _VALUE_ALIASES)
        ccy_col = _find_column(reader.fieldnames, _CCY_ALIASES)

        if product_col is None and symbol_col is None and isin_col is None:
            raise DegiroParseError(
                "Cannot find product/symbol/ISIN column in DEGIRO CSV. "
                f"Headers: {list(reader.fieldnames)}"
            )
        if qty_col is None:
            raise DegiroParseError(
                "Cannot find quantity column in DEGIRO CSV. "
                f"Headers: {list(reader.fieldnames)}"
            )

        positions: list[DegiroPosition] = []
        for row in reader:
            product = (row.get(product_col) or "").strip() if product_col else ""
            isin = (row.get(isin_col) or "").strip().upper() if isin_col else None
            raw_symbol = (row.get(symbol_col) or "").strip().upper() if symbol_col else ""
            qty = _parse_number(row.get(qty_col))
            if qty is None or qty <= 0:
                continue
            if not product and not raw_symbol and not isin:
                continue

            symbol = _resolve_symbol(raw_symbol, isin, product, symbol_map)
            if not symbol:
                raise DegiroParseError(
                    f"Unable to resolve yfinance ticker for row "
                    f"product={product!r} isin={isin!r}. "
                    "Add an entry to symbol_map in the agent config."
                )

            positions.append(
                DegiroPosition(
                    product_name=product or symbol,
                    isin=isin or None,
                    symbol=symbol,
                    quantity=qty,
                    avg_cost=_parse_number(row.get(avg_col)) if avg_col else None,
                    close_price=_parse_number(row.get(close_col)) if close_col else None,
                    local_value=_parse_number(row.get(value_col)) if value_col else None,
                    currency=(row.get(ccy_col) or "").strip() or None if ccy_col else None,
                )
            )

    if not positions:
        raise DegiroParseError(f"No portfolio rows parsed from {csv_path}")
    return positions


def _resolve_symbol(
    raw_symbol: str,
    isin: str | None,
    product: str,
    symbol_map: Mapping[str, str],
) -> str | None:
    if raw_symbol and raw_symbol in symbol_map:
        return symbol_map[raw_symbol]
    if isin and isin in symbol_map:
        return symbol_map[isin]
    product_key = product.upper()
    if product_key in symbol_map:
        return symbol_map[product_key]
    if raw_symbol and re.fullmatch(r"[A-Z0-9._\-]+", raw_symbol) and not _looks_like_isin(raw_symbol):
        return raw_symbol
    if isin and _looks_like_isin(isin) and isin in symbol_map:
        return symbol_map[isin]
    return None


def _looks_like_isin(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", value.upper()))


def positions_to_holdings(
    positions: list[DegiroPosition],
    *,
    target_allocation: TargetAllocation | None = None,
    asset_class_map: Mapping[str, AssetClass] | None = None,
) -> list[Holding]:
    """Convert parsed DEGIRO positions into domain :class:`Holding` objects.

    When ``target_allocation`` is provided, per-ticker ``target_weight`` is set
    to 0 (class-level targets drive metrics). Otherwise equal weights are used
    as a neutral placeholder.
    """
    asset_class_map = {k.upper(): v for k, v in (asset_class_map or {}).items()}
    n = len(positions) or 1
    equal = 0.0 if target_allocation is not None else 1.0 / n

    holdings: list[Holding] = []
    for pos in positions:
        asset_class = asset_class_map.get(pos.symbol, _infer_asset_class(pos))
        holdings.append(
            Holding(
                symbol=pos.symbol,
                shares=pos.quantity,
                target_weight=equal,
                asset_class=asset_class,
                avg_cost=pos.avg_cost,
                isin=pos.isin,
                product_name=pos.product_name,
            )
        )
    return holdings


def _infer_asset_class(pos: DegiroPosition) -> AssetClass:
    """Best-effort classification from product name keywords."""
    text = f"{pos.product_name} {pos.symbol}".lower()
    bond_tokens = ("bond", "obbligaz", "aggregate", "treasury", "govt", "gilt", "aggh")
    if any(token in text for token in bond_tokens):
        return AssetClass.BOND
    return AssetClass.EQUITY
