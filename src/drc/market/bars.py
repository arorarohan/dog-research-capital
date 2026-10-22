"""Fixed-interval bars: book state plus trade flow, per (venue, symbol, epoch).

Time axis: `coalesce(ts_exch, ts_recv)`. Exchange timestamps are preferred because this
machine's clock is not trustworthy at the ~1s level (see CLAUDE.md). Kalshi books are REST
samples with no exchange time, so those fall back to receive time.

Rules:
- Book columns hold the *last* book state seen in the interval, forward-filled across quiet
  intervals within an epoch. Nothing is filled across epochs (reconnects) and nothing is
  extended past the last message of an epoch.
- Intervals before the first book state of an epoch have null book columns.
- Trades replayed from a venue's post-subscribe history are de-duplicated by trade id.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

KEY = ["venue", "symbol", "epoch"]
BOOK_COLS = ["bid", "bid_sz", "ask", "ask_sz", "bid_depth", "ask_depth", "crossed"]


def load_trades(norm_root: Path, venue: str) -> pl.DataFrame:
    pattern = norm_root / "trades" / f"venue={venue}" / "**" / "*.parquet"
    df = pl.scan_parquet(str(pattern)).sort("ts_recv").collect()
    with_id = df.filter(pl.col("trade_id").is_not_null()).unique(
        ["venue", "symbol", "trade_id"], keep="first", maintain_order=True
    )
    return pl.concat([with_id, df.filter(pl.col("trade_id").is_null())]).sort("ts_recv")


def make_bars(tops: pl.DataFrame, trades: pl.DataFrame, bar_secs: int = 1) -> pl.DataFrame:
    width = bar_secs * 1_000_000_000
    t_expr = pl.coalesce("ts_exch", "ts_recv")

    book = (
        tops.with_columns(((t_expr // width) * width).alias("ts"))
        .group_by([*KEY, "ts"], maintain_order=True)
        .agg(*(pl.col(c).last() for c in BOOK_COLS), pl.len().alias("n_book"))
    )
    flow = (
        trades.with_columns(((t_expr // width) * width).alias("ts"))
        .with_columns(
            (pl.col("size") * (pl.col("side") == "buy")).alias("_buy"),
            (pl.col("size") * (pl.col("side") == "sell")).alias("_sell"),
            (pl.col("size") * pl.col("price")).alias("_notional"),
        )
        .group_by([*KEY, "ts"], maintain_order=True)
        .agg(
            pl.len().alias("n_trades"),
            pl.col("size").sum().alias("volume"),
            pl.col("_buy").sum().alias("buy_volume"),
            pl.col("_sell").sum().alias("sell_volume"),
            (pl.col("_notional").sum() / pl.col("size").sum()).alias("vwap"),
            pl.col("price").last().alias("last_price"),
        )
    )

    # Grid of every interval from the first to the last observation of each epoch.
    spans = (
        pl.concat([book.select([*KEY, "ts"]), flow.select([*KEY, "ts"])])
        .group_by(KEY)
        .agg(pl.col("ts").min().alias("lo"), pl.col("ts").max().alias("hi"))
    )
    grid = (
        spans.with_columns(pl.int_ranges(pl.col("lo"), pl.col("hi") + width, width).alias("ts"))
        .explode("ts")
        .select([*KEY, "ts"])
    )

    out = (
        grid.join(book, on=[*KEY, "ts"], how="left")
        .join(flow, on=[*KEY, "ts"], how="left")
        .sort([*KEY, "ts"])
        .with_columns(
            pl.col("n_book").fill_null(0),
            pl.col("n_trades").fill_null(0),
            pl.col("volume", "buy_volume", "sell_volume").fill_null(0.0),
            *(pl.col(c).forward_fill().over(KEY) for c in BOOK_COLS),
        )
        .with_columns(
            ((pl.col("bid") + pl.col("ask")) / 2).alias("mid"),
            (pl.col("ask") - pl.col("bid")).alias("spread"),
            (
                (pl.col("bid_depth") - pl.col("ask_depth"))
                / (pl.col("bid_depth") + pl.col("ask_depth"))
            ).alias("imbalance"),
        )
    )
    return out
