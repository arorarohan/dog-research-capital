"""Pick the fee model for a (venue, symbol), using real market fee data when we hold it.

Polymarket: the recorder stores each market's full Gamma JSON (with `feeType` / `feeSchedule`)
in the `_catalog` channel, and the backfill stores the same fields in the markets table. Both
are checked, newest recorded catalog first, before falling back to the category table and
then the default rate. Other venues have no per-market fee data, so they use their tier table.
"""

from __future__ import annotations

from pathlib import Path

import orjson
import polars as pl

from drc.backfill.catalog import read_markets
from drc.backtest.fees import VENUE_FEES, FeeModel, PolymarketFee, resolve_polymarket_fee
from drc.recorder.writer import read_lines


def catalog_market(raw_root: Path, token_id: str) -> dict | None:
    """The most recently recorded Gamma market object that lists `token_id`, if any."""
    files = sorted((raw_root / "polymarket" / "_catalog").rglob("*.jsonl*"), reverse=True)
    for path in files:
        for rec in reversed(list(read_lines(path))):
            for m in rec.get("m", []):
                tokens = m.get("clobTokenIds") or "[]"
                if token_id in (orjson.loads(tokens) if isinstance(tokens, str) else tokens):
                    return m
    return None


def table_market(hist_root: Path, token_id: str) -> dict | None:
    """The backfilled markets-table row for `token_id`, if any."""
    files = list((hist_root / "markets" / "venue=polymarket").rglob("*.parquet"))
    if not files:
        return None
    rows = read_markets(files).filter(pl.col("token_id") == token_id)
    return rows.row(0, named=True) if rows.height else None


def fee_model_for(
    venue: str, symbol: str, raw_root: Path | None = None, hist_root: Path | None = None
) -> tuple[FeeModel, str]:
    """(fee model, human-readable source) for trading `symbol` on `venue`."""
    if venue != "polymarket":
        return VENUE_FEES[venue], "published tier schedule, entry tier"
    # A market that states no fee data falls through to the next source, not straight to default.
    found: list[dict] = []
    for label, finder, root in (
        ("recorded catalog", catalog_market, raw_root),
        ("backfilled markets table", table_market, hist_root),
    ):
        market = finder(root, symbol) if root else None
        if market is None:
            continue
        if (
            model := PolymarketFee.from_gamma(market) or PolymarketFee.from_row(market)
        ) is not None:
            return model, f"market fee data from {label}"
        found.append(market)
    return resolve_polymarket_fee(found[0] if found else None)
