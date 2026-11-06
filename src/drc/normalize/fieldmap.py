"""Field accounting: for every raw (venue, channel), what each field is and what we do with it.

This is the written answer to "what does normalization throw away, and is that safe?" (#20).
`drc.validate.fields` scans raw recordings and fails if it meets a field or channel that is not
listed here, so an exchange adding a field (or a new message type) cannot be silently dropped.

Paths use dots for objects and `[]` for arrays (`events[].updates[].price_level`).
Statuses:
- KEPT:     goes into a normalized column
- DROPPED:  not stored in Parquet; the note says why that is safe, or what we lose
- ROUTING:  constant envelope field that only says which channel/event this is
After raw expires (15 days) a DROPPED field is gone for good, so every DROPPED note below that
says "RISK" is a decision to revisit.
"""

from __future__ import annotations

KEPT, DROPPED, ROUTING = "kept", "dropped", "routing"

Fields = dict[str, tuple[str, str]]


def _k(note: str = "") -> tuple[str, str]:
    return (KEPT, note)


def _d(note: str) -> tuple[str, str]:
    return (DROPPED, note)


def _r(note: str = "constant envelope") -> tuple[str, str]:
    return (ROUTING, note)


FIELD_MAP: dict[tuple[str, str], Fields] = {
    ("coinbase", "l2_data"): {
        "channel": _r(),
        "events[].type": _k("snapshot -> reset + snapshot rows; update -> update rows"),
        "events[].product_id": _k("symbol"),
        "events[].updates[].side": _k("bid / offer -> bid / ask"),
        "events[].updates[].event_time": _k("ts_exch"),
        "events[].updates[].price_level": _k("price"),
        "events[].updates[].new_quantity": _k("size (absolute)"),
        "timestamp": _k("ts_exch of the reset row only"),
        "sequence_num": _d(
            "counter shared by every Coinbase channel on one connection; used by "
            "`drc.validate.timing` to detect lost messages on raw, but gone after expiry"
        ),
    },
    ("coinbase", "market_trades"): {
        "channel": _r(),
        "events[].type": _k("snapshot -> replay=True"),
        "events[].trades[].product_id": _k("symbol"),
        "events[].trades[].trade_id": _k(),
        "events[].trades[].price": _k(),
        "events[].trades[].size": _k(),
        "events[].trades[].time": _k("ts_exch"),
        "events[].trades[].side": _k("maker side; flipped to aggressor in the parser"),
        "timestamp": _d("message send time; each row has its own trade time"),
        "sequence_num": _d("see l2_data"),
    },
    ("kraken", "book"): {
        "channel": _r(),
        "type": _k("snapshot / update"),
        "data[].symbol": _k(),
        "data[].timestamp": _k("ts_exch"),
        "data[].bids[].price": _k(),
        "data[].bids[].qty": _k("size (absolute)"),
        "data[].asks[].price": _k(),
        "data[].asks[].qty": _k("size (absolute)"),
        "data[].checksum": _d(
            "RISK: CRC32 of the top-100 book, verified on raw by `drc.validate.kraken`; "
            "after raw expires replay can no longer be checked against the exchange"
        ),
    },
    ("kraken", "trade"): {
        "channel": _r(),
        "type": _k("snapshot -> replay=True"),
        "data[].symbol": _k(),
        "data[].trade_id": _k(),
        "data[].timestamp": _k("ts_exch"),
        "data[].price": _k(),
        "data[].qty": _k("size"),
        "data[].side": _k("already the aggressor side"),
        "data[].ord_type": _d("market/limit of the taker order; not needed by any strategy yet"),
    },
    ("polymarket", "book"): {
        # initial snapshots arrive as a JSON array of books, later ones as single objects
        "[].asset_id": _k("symbol"),
        "[].timestamp": _k("ts_exch (ms)"),
        "[].bids[].price": _k(),
        "[].bids[].size": _k(),
        "[].asks[].price": _k(),
        "[].asks[].size": _k(),
        "[].event_type": _r(),
        "[].market": _d("condition id; the asset -> market mapping is in the _catalog channel"),
        "[].hash": _d("book hash; algorithm undocumented to us, not used for verification"),
        "[].tick_size": _d("RISK: tick size at snapshot time (see tick_size_change channel)"),
        "[].last_trade_price": _d("duplicates the last_trade_price channel"),
        "asset_id": _k("symbol"),
        "timestamp": _k("ts_exch (ms)"),
        "bids[].price": _k(),
        "bids[].size": _k(),
        "asks[].price": _k(),
        "asks[].size": _k(),
        "event_type": _r(),
        "market": _d("see [].market"),
        "hash": _d("see [].hash"),
    },
    ("polymarket", "price_change"): {
        "event_type": _r(),
        "market": _d("condition id; mapping is in _catalog"),
        "timestamp": _k("ts_exch (ms)"),
        "price_changes[].asset_id": _k("symbol"),
        "price_changes[].price": _k(),
        "price_changes[].size": _k("size (absolute, 0 removes the level)"),
        "price_changes[].side": _k("BUY -> bid, SELL -> ask"),
        "price_changes[].hash": _d("per-change hash; not used"),
        "price_changes[].best_bid": _d(
            "exchange-reported best bid after the change; redundant with replay, used as ground "
            "truth by `drc.validate.polymarket` on raw only"
        ),
        "price_changes[].best_ask": _d("see best_bid"),
    },
    ("polymarket", "last_trade_price"): {
        "event_type": _r(),
        "market": _d("condition id; mapping is in _catalog"),
        "asset_id": _k("symbol"),
        "price": _k(),
        "size": _k(),
        "side": _k("already the aggressor side"),
        "timestamp": _k("ts_exch (ms)"),
        "transaction_hash": _k("trade_id (not unique per fill: one tx can fill several orders)"),
        "fee_rate_bps": _d(
            "RISK: per-trade fee rate; '0' on every message seen so far, while fees are charged "
            "per market (Gamma). Fee models use the catalog's fee data instead"
        ),
    },
    ("kalshi", "orderbook"): {
        "ticker": _k("symbol"),
        "resp.orderbook_fp.yes_dollars[][]": _k("[price, qty] resting Yes bids"),
        "resp.orderbook_fp.no_dollars[][]": _k("[price, qty] No bids -> Yes asks at 1 - price"),
    },
    ("kalshi", "trades"): {
        "ticker": _k("symbol"),
        "trade_id": _k(),
        "created_time": _k("ts_exch"),
        "yes_price_dollars": _k("price"),
        "count_fp": _k("size"),
        "taker_side": _k("yes -> buy, no -> sell"),
        "no_price_dollars": _d("always 1 - yes_price_dollars (checked on all raw)"),
        "taker_outcome_side": _d("always equals taker_side (checked on all raw)"),
        "taker_book_side": _d("bid when taker buys Yes, ask when it buys No; implied by side"),
        "is_block_trade": _d(
            "RISK: False on every trade seen; a block trade is negotiated off the book and "
            "should not be treated as flow, so the validator fails if one ever appears"
        ),
    },
}

# Raw channels no parser reads. Each is an explicit decision, not an oversight.
IGNORED_CHANNELS: dict[tuple[str, str], str] = {
    ("coinbase", "subscriptions"): "subscribe acknowledgement",
    ("kraken", "subscriptions"): "subscribe acknowledgement",
    ("kraken", "status"): "connection status message",
    ("polymarket", "tick_size_change"): (
        "RISK: tick size changes when a price nears 0 or 1 (0.01 -> 0.001). Needed to model fills "
        "and minimum price steps at the extremes; recorded in raw but not normalized yet"
    ),
    ("polymarket", "_catalog"): "market catalog; read by backfill/forward code, not normalized",
    ("kalshi", "_catalog"): "market catalog; read by backfill/forward code, not normalized",
}
# `_meta` (connect/disconnect events) is read by the pipeline for epochs, never as a table.
META_CHANNEL_NAME = "_meta"
