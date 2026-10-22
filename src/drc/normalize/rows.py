"""Row types and table schemas shared by parsers, the pipeline, and book replay.

Book event kinds:
- `reset`:    start of a snapshot for a symbol; the book is cleared (carries no level data)
- `snapshot`: one level of a snapshot (size is the absolute quantity at that price)
- `update`:   one level change (size is the new absolute quantity; 0 removes the level)

Prediction-market symbols refer to the *Yes* contract (Kalshi ticker) or a single outcome
token (Polymarket asset id); prices are probabilities in [0, 1].
"""

from __future__ import annotations

from typing import NamedTuple

import polars as pl


class Trade(NamedTuple):
    symbol: str
    trade_id: str | None
    ts_exch: int | None
    price: float
    size: float
    side: str | None  # 'buy' or 'sell' as reported by the venue (see parsers for semantics)
    replay: bool  # part of a post-subscribe history snapshot; may duplicate earlier rows


class BookRow(NamedTuple):
    symbol: str
    ts_exch: int | None
    kind: str  # reset | snapshot | update
    side: str | None  # bid | ask
    price: float | None
    size: float | None


TRADES_SCHEMA = {
    "venue": pl.Utf8,
    "symbol": pl.Utf8,
    "epoch": pl.Int64,
    "ts_recv": pl.Int64,
    "ts_exch": pl.Int64,
    "trade_id": pl.Utf8,
    "price": pl.Float64,
    "size": pl.Float64,
    "side": pl.Utf8,
    "replay": pl.Boolean,
}

BOOK_EVENTS_SCHEMA = {
    "venue": pl.Utf8,
    "symbol": pl.Utf8,
    "epoch": pl.Int64,
    "ts_recv": pl.Int64,
    "msg_id": pl.Int64,
    "ts_exch": pl.Int64,
    "kind": pl.Utf8,
    "side": pl.Utf8,
    "price": pl.Float64,
    "size": pl.Float64,
}
