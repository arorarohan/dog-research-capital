"""Venue-specific parsers: one raw message in, normalized `Trade` / `BookRow` rows out.

Trade `side` in the normalized tables is always the *aggressor* (taker) side: 'buy' means a
buyer crossed the spread and lifted the ask. Each venue's label was checked empirically against
the prevailing mid (`python -m drc.validate.trade_side`):
- kraken, polymarket: label is already the aggressor side
- kalshi: taker side of the Yes contract ('yes' taker -> 'buy')
- coinbase: the label is the *resting (maker)* side, so it is flipped here
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from drc.normalize.rows import BookRow, Trade
from drc.normalize.timeutil import iso_to_ns, ms_to_ns

Row = Trade | BookRow
Parser = Callable[[object], Iterator[Row]]


def _f(x) -> float:
    return float(x)


# ---- Coinbase Advanced Trade -------------------------------------------------------------


def coinbase_l2(msg: dict) -> Iterator[BookRow]:
    for ev in msg.get("events", []):
        sym = ev["product_id"]
        is_snap = ev["type"] == "snapshot"
        if is_snap:
            yield BookRow(sym, iso_to_ns(msg["timestamp"]), "reset", None, None, None)
        for u in ev["updates"]:
            yield BookRow(
                sym,
                iso_to_ns(u["event_time"]),
                "snapshot" if is_snap else "update",
                "bid" if u["side"] == "bid" else "ask",  # 'offer' -> ask
                _f(u["price_level"]),
                _f(u["new_quantity"]),
            )


def coinbase_trades(msg: dict) -> Iterator[Trade]:
    for ev in msg.get("events", []):
        replay = ev["type"] == "snapshot"
        for t in ev["trades"]:
            yield Trade(
                t["product_id"],
                str(t["trade_id"]),
                iso_to_ns(t["time"]),
                _f(t["price"]),
                _f(t["size"]),
                "sell" if t["side"] == "BUY" else "buy",  # venue labels the maker side
                replay,
            )


# ---- Kraken v2 -----------------------------------------------------------------------------


def kraken_book(msg: dict) -> Iterator[BookRow]:
    is_snap = msg["type"] == "snapshot"
    for d in msg["data"]:
        sym = d["symbol"].replace("/", "-")
        ts = iso_to_ns(d["timestamp"]) if d.get("timestamp") else None
        if is_snap:
            yield BookRow(sym, ts, "reset", None, None, None)
        kind = "snapshot" if is_snap else "update"
        for side, key in (("bid", "bids"), ("ask", "asks")):
            for lvl in d.get(key, []):
                yield BookRow(sym, ts, kind, side, _f(lvl["price"]), _f(lvl["qty"]))


def kraken_trades(msg: dict) -> Iterator[Trade]:
    replay = msg["type"] == "snapshot"
    for t in msg["data"]:
        yield Trade(
            t["symbol"].replace("/", "-"),
            str(t["trade_id"]),
            iso_to_ns(t["timestamp"]),
            _f(t["price"]),
            _f(t["qty"]),
            t["side"],
            replay,
        )


# ---- Polymarket ---------------------------------------------------------------------------


def polymarket_book(msg: dict | list) -> Iterator[BookRow]:
    for b in msg if isinstance(msg, list) else [msg]:
        sym = b["asset_id"]
        ts = ms_to_ns(b["timestamp"]) if b.get("timestamp") else None
        yield BookRow(sym, ts, "reset", None, None, None)
        for side, key in (("bid", "bids"), ("ask", "asks")):
            for lvl in b.get(key, []):
                yield BookRow(sym, ts, "snapshot", side, _f(lvl["price"]), _f(lvl["size"]))


def polymarket_price_change(msg: dict) -> Iterator[BookRow]:
    ts = ms_to_ns(msg["timestamp"]) if msg.get("timestamp") else None
    for c in msg["price_changes"]:
        side = "bid" if c["side"] == "BUY" else "ask"
        yield BookRow(c["asset_id"], ts, "update", side, _f(c["price"]), _f(c["size"]))


def polymarket_trades(msg: dict) -> Iterator[Trade]:
    yield Trade(
        msg["asset_id"],
        msg.get("transaction_hash"),
        ms_to_ns(msg["timestamp"]) if msg.get("timestamp") else None,
        _f(msg["price"]),
        _f(msg["size"]),
        msg["side"].lower(),
        False,
    )


# ---- Kalshi (REST-sampled) ---------------------------------------------------------------


def kalshi_book(msg: dict) -> Iterator[BookRow]:
    """Kalshi lists resting *bids* for Yes and for No. A No bid at p is a Yes ask at 1 - p."""
    sym = msg["ticker"]
    book = msg["resp"].get("orderbook_fp") or {}
    yield BookRow(sym, None, "reset", None, None, None)
    for price, qty in book.get("yes_dollars") or []:
        yield BookRow(sym, None, "snapshot", "bid", _f(price), _f(qty))
    for price, qty in book.get("no_dollars") or []:
        yield BookRow(sym, None, "snapshot", "ask", round(1.0 - _f(price), 4), _f(qty))


def kalshi_trades(msg: dict) -> Iterator[Trade]:
    yield Trade(
        msg["ticker"],
        msg["trade_id"],
        iso_to_ns(msg["created_time"]),
        _f(msg["yes_price_dollars"]),
        _f(msg["count_fp"]),
        "buy" if msg["taker_side"] == "yes" else "sell",
        False,
    )


# (venue, channel) -> (table, parser). Within a venue, order here breaks ts_recv ties.
PARSERS: dict[tuple[str, str], tuple[str, Parser]] = {
    ("coinbase", "l2_data"): ("book", coinbase_l2),
    ("coinbase", "market_trades"): ("trades", coinbase_trades),
    ("kraken", "book"): ("book", kraken_book),
    ("kraken", "trade"): ("trades", kraken_trades),
    ("polymarket", "book"): ("book", polymarket_book),
    ("polymarket", "price_change"): ("book", polymarket_price_change),
    ("polymarket", "last_trade_price"): ("trades", polymarket_trades),
    ("kalshi", "orderbook"): ("book", kalshi_book),
    ("kalshi", "trades"): ("trades", kalshi_trades),
}
