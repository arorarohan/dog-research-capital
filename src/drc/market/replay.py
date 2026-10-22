"""Replay normalized book events into top-of-book states.

Rules that keep reconstruction honest:
- A new `epoch` (recorder reconnect) wipes every book of the venue; nothing carries across.
- A `reset` row starts a snapshot for one symbol; its levels follow.
- `update` rows for a symbol that has not been snapshotted in the current epoch are dropped
  (counted in `ReplayStats.dropped_unsynced`) because applying them to an empty book is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from drc.market.book import Book

TOP_DEPTH = 5

# Venues that stream a depth-limited book never send removals for levels that fall out of range;
# the client must trim to the subscribed depth. Kraken: config/recorder.toml `book_depth`.
# Validated against Kraken's own CRC32 book checksums (see docs/validation.md).
DEFAULT_MAX_LEVELS = {"kraken": 100}

TOPS_SCHEMA = {
    "venue": pl.Utf8,
    "symbol": pl.Utf8,
    "epoch": pl.Int64,
    "ts_recv": pl.Int64,
    "ts_exch": pl.Int64,  # exchange timestamp of the latest event for this symbol (may be null)
    "bid": pl.Float64,
    "bid_sz": pl.Float64,
    "ask": pl.Float64,
    "ask_sz": pl.Float64,
    "bid_depth": pl.Float64,  # total size in the best TOP_DEPTH bid levels
    "ask_depth": pl.Float64,
    "crossed": pl.Boolean,
}


@dataclass
class ReplayStats:
    messages: int = 0
    dropped_unsynced: int = 0
    crossed_states: int = 0
    states: int = 0
    symbols: set[str] = field(default_factory=set)


def load_book_events(norm_root: Path, venue: str, symbols: list[str] | None = None) -> pl.DataFrame:
    lf = pl.scan_parquet(
        str(norm_root / "book_events" / f"venue={venue}" / "**" / "*.parquet"),
        hive_partitioning=False,
    )
    if symbols:
        lf = lf.filter(pl.col("symbol").is_in(symbols))
    return lf.sort("ts_recv", "msg_id", maintain_order=True).collect()


def _top_row(venue, symbol, epoch, ts, ts_exch, book: Book):
    bb, ba = book.best_bid(), book.best_ask()
    return (
        venue,
        symbol,
        epoch,
        ts,
        ts_exch,
        bb[0] if bb else None,
        bb[1] if bb else None,
        ba[0] if ba else None,
        ba[1] if ba else None,
        book.depth("bid", TOP_DEPTH),
        book.depth("ask", TOP_DEPTH),
        book.crossed(),
    )


def replay_tops(
    events: pl.DataFrame, max_levels: dict[str, int] | None = None
) -> tuple[pl.DataFrame, ReplayStats]:
    """One output row per message per touched symbol, kept only when the top state changed."""
    max_levels = DEFAULT_MAX_LEVELS if max_levels is None else max_levels
    stats = ReplayStats()
    rows: list[tuple] = []
    books: dict[tuple[str, str], Book] = {}
    synced: set[tuple[str, str]] = set()
    last_state: dict[tuple[str, str], tuple] = {}
    cur_epoch: dict[str, int] = {}
    last_exch: dict[tuple[str, str], int | None] = {}

    cols = [
        "venue",
        "symbol",
        "epoch",
        "ts_recv",
        "msg_id",
        "ts_exch",
        "kind",
        "side",
        "price",
        "size",
    ]
    touched: set[tuple[str, str]] = set()
    cur_msg: tuple[int, int] | None = None
    cur_ctx: tuple | None = None

    def flush() -> None:
        for key in touched:
            venue, symbol = key
            row = _top_row(venue, symbol, cur_ctx[0], cur_ctx[1], last_exch.get(key), books[key])
            state = row[5:]
            if last_state.get(key) != state:
                last_state[key] = state
                rows.append(row)
                stats.states += 1
                stats.crossed_states += row[-1]
        touched.clear()

    for row in events.select(cols).iter_rows():
        venue, symbol, epoch, ts, msg_id, ts_exch, kind, side, price, size = row
        msg = (ts, msg_id)
        if msg != cur_msg:
            if cur_msg is not None:
                flush()
            cur_msg = msg
            stats.messages += 1
        cur_ctx = (epoch, ts)
        if cur_epoch.get(venue) != epoch:  # reconnect: forget everything for this venue
            cur_epoch[venue] = epoch
            for key in [k for k in books if k[0] == venue]:
                books[key].clear()
                synced.discard(key)
                last_state.pop(key, None)
        key = (venue, symbol)
        if ts_exch is not None:
            last_exch[key] = ts_exch
        stats.symbols.add(symbol)
        if kind == "reset":
            books.setdefault(key, Book()).clear()
            synced.add(key)
            last_state.pop(key, None)
        elif key not in synced:
            stats.dropped_unsynced += 1
            continue
        else:
            books[key].set_level(side, price, size)
            if venue in max_levels:
                books[key].trim(max_levels[venue])
        touched.add(key)
    if cur_msg is not None:
        flush()
    return pl.DataFrame(rows, schema=TOPS_SCHEMA, orient="row"), stats
