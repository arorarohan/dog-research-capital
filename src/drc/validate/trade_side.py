"""Empirically check what a venue's trade `side` label means.

For each trade we look at the book state prevailing at its arrival and measure where the
trade price sits relative to the mid. If 'buy' trades print mostly above mid, the label is the
aggressor (taker) side; if mostly below, it is the resting (maker) side.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import polars as pl

from drc.market.bars import load_trades
from drc.market.replay import load_book_events, replay_tops


def side_vs_mid(norm_root: Path, venue: str) -> pl.DataFrame:
    tops, _ = replay_tops(load_book_events(norm_root, venue))
    tops = tops.drop_nulls(["bid", "ask"]).with_columns(
        ((pl.col("bid") + pl.col("ask")) / 2).alias("mid")
    )
    warnings.filterwarnings("ignore", message="Sortedness of columns")  # both sides pre-sorted
    trades = load_trades(norm_root, venue).filter(~pl.col("replay"))
    joined = trades.sort("ts_recv").join_asof(
        tops.sort("ts_recv").select("venue", "symbol", "epoch", "ts_recv", "mid", "bid", "ask"),
        on="ts_recv",
        by=["venue", "symbol", "epoch"],
        strategy="backward",
    )
    return (
        joined.drop_nulls("mid")
        .with_columns(
            pl.when(pl.col("price") > pl.col("mid"))
            .then(pl.lit("above_mid"))
            .when(pl.col("price") < pl.col("mid"))
            .then(pl.lit("below_mid"))
            .otherwise(pl.lit("at_mid"))
            .alias("where")
        )
        .group_by("side", "where")
        .len()
        .sort("side", "where")
    )


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/norm")
    for venue in ("coinbase", "kraken", "polymarket", "kalshi"):
        print(f"== {venue}")
        print(side_vs_mid(root, venue))
